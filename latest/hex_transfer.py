from __future__ import annotations

from typing import List
from intelhex import IntelHex

from CAN_controller import CANController
from flash_kernel import flash_kernel


def _u32_be(x: int) -> List[int]:
    return [(x >> 24) & 0xFF, (x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF]


def calculate_0x0C_frames(ih: IntelHex, length: int) -> List[List[int]]:
    FLASH_BASE = 0xC10000  # static
    SESSION = 0x01         # static
    CHUNK = 0x10000        # static

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


def flash_hex(
    ctrl: CANController,
    ih: IntelHex,
    header80: List[int],
    *,
    do_flash_kernel: bool = True,
    do_erase: bool = True,
) -> dict:

    # all statics
    FLASH_BASE = 0xC10000
    STAGING_ADDR = 0xE08000
    BLOCK_SIZE = 0x8000
    CHUNK_SIZE = 6
    POLL_EVERY_FRAMES = 11          # 11 * 6 = 66 (0x42)

    SEND_DELAY_MS = 0 # try not to change, testing at 0 to see if flashing is continuous

    # timeout constants
    PTR_TIMEOUT_MS = 1000
    POLL_TIMEOUT_MS = 1000
    COMMIT_TIMEOUT_MS = 3000
    ERASE_ACK_TIMEOUT_MS = 2000

    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response
    hex_length = ctrl.hex_length

    # header validation, (just a precaution)
    if not isinstance(header80, list) or len(header80) != 0x80:
        raise ValueError("header80 must be a list of exactly 0x80 ints")
    if any((not isinstance(b, int) or b < 0 or b > 0xFF) for b in header80):
        raise ValueError("header80 elements must be ints 0..255")

    # always runs, kept as a variable in case something changes
    if do_flash_kernel:
        flash_kernel(ctrl)
    else:
        send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xE0, 0x00, 0x00], delay=SEND_DELAY_MS)
        VCU_response(0x002, data=[0x0D, 0x01], timeout=PTR_TIMEOUT_MS)

    # calculates writable length of hex file
    length = hex_length(ih)

    # also always runs usually
    if do_erase:
        erase_frames = calculate_0x0C_frames(ih, length)
        for frame in erase_frames:
            send_can(0x001, frame, delay=SEND_DELAY_MS)
            VCU_response(0x002, prefix=[0x0C, 0x01, 0x01], timeout=ERASE_ACK_TIMEOUT_MS)


    end_addr = FLASH_BASE + length - 1

    # TESTING 03/22/26 -> fixing stupid IH deprecation warning
    ih.padding = 0xFF
    image = bytearray(ih.tobinarray(start=FLASH_BASE, size=length))  # inclusive end

    if len(image) < 0x80:
        raise ValueError("HEX image is smaller than 0x80 bytes; cannot apply APDB header")
    image[0:0x80] = bytes(header80)

    total_len = len(image)
    mv = memoryview(image)

    # sets pointer back to staging buffer (32KB)
    send_can(0x001, [0x0D, 0x01] + _u32_be(STAGING_ADDR), delay=SEND_DELAY_MS)
    VCU_response(0x002, data=[0x0D, 0x01], timeout=PTR_TIMEOUT_MS)

    offset = 0
    block_index = 0

    while offset < total_len:
        this_len = min(BLOCK_SIZE, total_len - offset)
        block = mv[offset: offset + this_len]

        frames_since_poll = 0
        bytes_since_poll = 0

        i = 0
        while i < this_len:
            chunk = block[i: i + CHUNK_SIZE]
            send_can(0x001, [0x05, 0x01] + list(chunk), delay=SEND_DELAY_MS)

            frames_since_poll += 1
            bytes_since_poll += len(chunk)
            i += len(chunk)

            if frames_since_poll >= POLL_EVERY_FRAMES:
                send_can(0x001, [0x02, 0x01], delay=SEND_DELAY_MS)
                expected = bytes_since_poll & 0xFF
                VCU_response(
                    0x002,
                    data=[0x02, 0x01, 0x00, expected, 0x00, 0x00],
                    timeout=POLL_TIMEOUT_MS,
                )
                frames_since_poll = 0
                bytes_since_poll = 0

        # final remainder poll at end-of-block
        if bytes_since_poll > 0:
            send_can(0x001, [0x02, 0x01], delay=SEND_DELAY_MS)
            expected = bytes_since_poll & 0xFF
            VCU_response(
                0x002,
                data=[0x02, 0x01, 0x00, expected, 0x00, 0x00],
                timeout=POLL_TIMEOUT_MS,
            )

        # sets destination pointer to FLASH_BASE + offset
        dest_addr = FLASH_BASE + offset
        send_can(0x001, [0x0D, 0x01] + _u32_be(dest_addr), delay=SEND_DELAY_MS)
        VCU_response(0x002, data=[0x0D, 0x01], timeout=PTR_TIMEOUT_MS)

        # commits written length from staging buffer to flash memory
        len_m1 = this_len - 1
        commit = [0x0B, 0x01, 0x00, 0xE0, 0x80, 0x00, (len_m1 >> 8) & 0xFF, len_m1 & 0xFF]
        send_can(0x001, commit, delay=SEND_DELAY_MS)
        VCU_response(0x002, data=[0x0B, 0x01, 0x01], timeout=COMMIT_TIMEOUT_MS)

        offset += this_len
        block_index += 1

        # looped to reset staging pointer while theres chunks left to be written
        if offset < total_len:
            send_can(0x001, [0x0D, 0x01] + _u32_be(STAGING_ADDR), delay=SEND_DELAY_MS)
            VCU_response(0x002, data=[0x0D, 0x01], timeout=PTR_TIMEOUT_MS)

    return {
        "status": "success",
        "flash_base": hex(FLASH_BASE),
        "total_len": total_len,
        "blocks": block_index,
        "last_block_len": (total_len % BLOCK_SIZE) or BLOCK_SIZE,
        "erased": bool(do_erase),
        "kernel": bool(do_flash_kernel),
    }


hex_transfer = flash_hex
