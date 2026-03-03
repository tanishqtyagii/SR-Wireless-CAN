from __future__ import annotations

from typing import List
from intelhex import IntelHex

from CAN_controller import CANController
from flash_kernel import flash_kernel


def _u32_be(x: int) -> List[int]:
    return [(x >> 24) & 0xFF, (x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF]


def hex_length(ih: IntelHex) -> int:
    """
    Returns the firmware image length in bytes for THIS protocol:
    image starts at 0xC10000 and extends through the HEX's max address (inclusive).

    Example: maxaddr=0xC2DE7F -> length = 0xC2DE7F - 0xC10000 + 1 = 0x1DE80
    """
    FLASH_BASE = 0xC10000  # protocol fact

    mn = ih.minaddr()
    mx = ih.maxaddr()
    if mn is None or mx is None:
        raise ValueError("HEX has no data records")

    if mx < FLASH_BASE:
        raise ValueError(f"HEX maxaddr 0x{mx:X} is below FLASH_BASE 0x{FLASH_BASE:X}")

    return (mx - FLASH_BASE) + 1


def calculate_0x0C_frames(ih: IntelHex, length: int) -> List[List[int]]:
    """
    Builds 0x0C erase frames for THIS protocol:
      - start address ALWAYS 0xC10000
      - session ALWAYS 0x01
      - chunk size ALWAYS 0x10000
      - length encoding is (len-1) big-endian

    Returns: list of frames as list[int] (each frame is 8 bytes).
    """
    FLASH_BASE = 0xC10000  # protocol fact
    SESSION = 0x01         # protocol fact
    CHUNK = 0x10000        # protocol fact (64KiB)

    mn = ih.minaddr()
    mx = ih.maxaddr()
    if mn is None or mx is None:
        raise ValueError("HEX has no data records")
    if mx < FLASH_BASE:
        raise ValueError(f"HEX maxaddr 0x{mx:X} is below FLASH_BASE 0x{FLASH_BASE:X}")

    if length <= 0:
        return []

    frames: List[List[int]] = []
    addr = FLASH_BASE
    remaining = length

    while remaining > 0:
        this_len = CHUNK if remaining > CHUNK else remaining
        len_m1 = this_len - 1  # protocol uses (len-1)

        frames.append([
            0x0C, SESSION,
            (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF,
            (len_m1 >> 8) & 0xFF, len_m1 & 0xFF,
        ])

        addr += this_len
        remaining -= this_len

    return frames


def flash_hex(ctrl: CANController, ih: IntelHex, header80: List[int], *, do_flash_kernel: bool = True, do_erase: bool = True) -> dict:
    """
    Duplicates the trace-style streaming stage (kernel -> erase -> stage -> stream -> poll -> dest -> commit).

    Inputs:
      - ctrl: your CANController (has send_can() delay_ms and VCU_response() timeout_ms)
      - ih: IntelHex object (already loaded)
      - header80: list[int] length 0x80 (the patched APDB header)
    """

    # ---- protocol facts / hardcoded knobs (match your trace conventions) ----
    SESSION = 0x01
    FLASH_BASE = 0xC10000
    STAGING_ADDR = 0xE08000
    BLOCK_SIZE = 0x8000
    CHUNK_SIZE = 6
    POLL_EVERY_FRAMES = 11          # 11 * 6 = 66 (0x42) typical
    SEND_DELAY_MS = 10

    PTR_TIMEOUT_MS = 1000
    POLL_TIMEOUT_MS = 1000
    COMMIT_TIMEOUT_MS = 3000
    ERASE_ACK_TIMEOUT_MS = 2000

    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response

    # ---- validate header ----
    if not isinstance(header80, list) or len(header80) != 0x80:
        raise ValueError("header80 must be a list of exactly 0x80 ints")
    if any((not isinstance(b, int) or b < 0 or b > 0xFF) for b in header80):
        raise ValueError("header80 elements must be ints 0..255")
    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xE0, 0x00, 0x00])
    VCU_response(0x002, data=[0x0D, 0x01])

    # ---- optional flash kernel stage ----
    if do_flash_kernel:
        flash_kernel(ctrl)  # assumes your flash_kernel() takes ctrl

    # ---- compute image length + optional erase frames (0x0C) ----
    length = hex_length(ih)

    if do_erase:
        erase_frames = calculate_0x0C_frames(ih, length)
        for frame in erase_frames:
            send_can(0x001, frame, delay=SEND_DELAY_MS)
            # trace-style ack is typically: 0C 01 01 (prefix is safest)
            VCU_response(0x002, prefix=[0x0C, 0x01, 0x01], timeout=ERASE_ACK_TIMEOUT_MS)

    # ---- build exact streamed image bytes from FLASH_BASE for 'length' bytes, pad gaps with 0xFF ----
    end_addr = FLASH_BASE + length - 1
    image = bytearray(ih.tobinarray(start=FLASH_BASE, end=end_addr, pad=0xFF))  # inclusive end
    if len(image) < 0x80:
        raise ValueError("HEX image is smaller than 0x80 bytes; cannot apply APDB header")
    image[0:0x80] = bytes(header80)

    total_len = len(image)
    mv = memoryview(image)

    # ---- set pointer to staging buffer (0x0D -> E08000) ----
    send_can(0x001, [0x0D, 0x01] + _u32_be(STAGING_ADDR), delay=SEND_DELAY_MS)
    VCU_response(0x002, data=[0x0D, 0x01], timeout=PTR_TIMEOUT_MS)

    offset = 0
    block_index = 0

    while offset < total_len:
        this_len = min(BLOCK_SIZE, total_len - offset)
        block = mv[offset: offset + this_len]

        # ---- write into staging: 0x05 frames (<=6 bytes), poll 0x02 every 11 frames ----
        frames_since_poll = 0
        bytes_since_poll = 0

        i = 0
        while i < this_len:
            chunk = block[i: i + CHUNK_SIZE]
            send_can(0x001, [0x05, SESSION] + list(chunk), delay=SEND_DELAY_MS)

            frames_since_poll += 1
            bytes_since_poll += len(chunk)
            i += len(chunk)

            if frames_since_poll >= POLL_EVERY_FRAMES:
                send_can(0x001, [0x02, SESSION], delay=SEND_DELAY_MS)
                expected = bytes_since_poll & 0xFF
                VCU_response(
                    0x002,
                    data=[0x02, SESSION, 0x00, expected, 0x00, 0x00],
                    timeout=POLL_TIMEOUT_MS,
                )
                frames_since_poll = 0
                bytes_since_poll = 0

        # ---- final remainder poll at end-of-block ----
        if bytes_since_poll > 0:
            send_can(0x001, [0x02, SESSION], delay=SEND_DELAY_MS)
            expected = bytes_since_poll & 0xFF
            VCU_response(
                0x002,
                data=[0x02, SESSION, 0x00, expected, 0x00, 0x00],
                timeout=POLL_TIMEOUT_MS,
            )

        # ---- set destination pointer: 0x0D -> FLASH_BASE + offset ----
        dest_addr = FLASH_BASE + offset
        send_can(0x001, [0x0D, SESSION] + _u32_be(dest_addr), delay=SEND_DELAY_MS)
        VCU_response(0x002, data=[0x0D, SESSION], timeout=PTR_TIMEOUT_MS)

        # ---- commit: 0x0B copies from E08000 (staging) to current dest pointer ----
        len_m1 = this_len - 1
        commit = [0x0B, SESSION, 0x00, 0xE0, 0x80, 0x00, (len_m1 >> 8) & 0xFF, len_m1 & 0xFF]
        send_can(0x001, commit, delay=SEND_DELAY_MS)
        VCU_response(0x002, data=[0x0B, SESSION, 0x01], timeout=COMMIT_TIMEOUT_MS)

        offset += this_len
        block_index += 1

        # ---- trace resets staging pointer only if another block follows ----
        if offset < total_len:
            send_can(0x001, [0x0D, SESSION] + _u32_be(STAGING_ADDR), delay=SEND_DELAY_MS)
            VCU_response(0x002, data=[0x0D, SESSION], timeout=PTR_TIMEOUT_MS)

    return {
        "status": "success",
        "flash_base": hex(FLASH_BASE),
        "total_len": total_len,
        "blocks": block_index,
        "last_block_len": (total_len % BLOCK_SIZE) or BLOCK_SIZE,
        "erased": bool(do_erase),
        "kernel": bool(do_flash_kernel),
    }