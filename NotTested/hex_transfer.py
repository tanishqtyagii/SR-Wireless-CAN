import intelhex
from typing import List, Optional, Tuple



def _u32_be(x: int) -> List[int]:
    return [(x >> 24) & 0xFF, (x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF]


def _load_linear_image(hex_path: str, pad: int = 0xFF) -> Tuple[int, bytearray]:
    """
    Returns (base_addr, linearized_image_bytes).
    Linearizes Intel HEX into a contiguous bytearray from minaddr..maxaddr inclusive.
    Gaps are padded with 0xFF (erased flash value).
    """
    ih = intelhex.IntelHex(hex_path)
    base = ih.minaddr()
    end = ih.maxaddr()
    if base is None or end is None:
        raise ValueError("HEX has no data records")

    data = ih.tobinarray(start=base, end=end, pad=pad)  # inclusive end
    return base, bytearray(data)


def _hex_span(hex_path: str) -> Tuple[int, int]:
    """(minaddr, length) inclusive span of data in the hex file."""
    ih = intelhex.IntelHex(hex_path)
    mn = ih.minaddr()
    mx = ih.maxaddr()
    if mn is None or mx is None:
        raise ValueError("HEX has no data records")
    return mn, (mx - mn + 1)


def _erase_plan_0x0C_frames(erase_start: int, length: int, *, session: int, chunk: int = 0x10000) -> List[List[int]]:
    """
    0x0C erase frames:
      [0x0C, session, addr32_be(4), (len-1)_be(2)]
    Split into 0x10000 chunks like your traces.
    """
    frames: List[List[int]] = []
    addr = erase_start
    remaining = length
    while remaining > 0:
        this_len = min(remaining, chunk)
        len_m1 = this_len - 1
        frames.append([
            0x0C, session,
            (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF,
            (len_m1 >> 8) & 0xFF, len_m1 & 0xFF,
        ])
        addr += this_len
        remaining -= this_len
    return frames


def flash_hex(
    hex_path: str,
    header80: List[int],
    *,
    session: int = 0x01,
    staging_addr: int = 0xE08000,
    block_size: int = 0x8000,
    chunk_size: int = 6,
    poll_every_frames: int = 11,          # 11 * 6 = 66 (0x42) typical
    send_delay_ms: float = 1.0,
    poll_timeout: float = 1.0,
    ptr_timeout: float = 1.0,
    commit_timeout: float = 3.0,
    do_flash_kernel: bool = True,
    do_erase: bool = True,
) -> dict:
    from NotTested.CAN_controller import send_can, VCU_response, flash_kernel

    """
    Matches your trace behavior for the firmware streaming stage:
      - optional flash_kernel()
      - optional erase via 0x0C
      - 0D -> staging (E08000)
      - for each block:
          * 0x05 01 payload bytes (1..6 each)
          * 0x02 01 poll every 11 frames + final remainder poll
          * 0D -> destination (C1xxxx / C2xxxx ...)
          * 0B commit from E08000 with len-1
          * if MORE blocks remain: 0D -> staging again (like trace)
        stops after final 0B ack (does NOT do 0x18/0x10 verify steps)
    """

    # --- validate header ---
    if not isinstance(header80, list) or len(header80) != 0x80:
        raise ValueError("header80 must be a list of exactly 0x80 ints")
    if any((not isinstance(b, int) or b < 0 or b > 0xFF) for b in header80):
        raise ValueError("header80 elements must be ints 0..255")

    if do_flash_kernel:
        flash_kernel()

    # --- erase (dynamic from hex span) ---
    if do_erase:
        erase_start, length = _hex_span(hex_path)
        for frame in _erase_plan_0x0C_frames(erase_start, length, session=session, chunk=0x10000):
            send_can(0x001, frame, delay=send_delay_ms)
            if not VCU_response(0x002, prefix=[0x0C, session, 0x01], timeout=2.0):
                raise RuntimeError("Flash erase failed (0x0C ack not seen)")

    # --- build the exact streamed image: linearized HEX, then overwrite first 0x80 with your header ---
    flash_base, image = _load_linear_image(hex_path, pad=0xFF)
    if len(image) < 0x80:
        raise ValueError("HEX image is smaller than 0x80 bytes; cannot apply APDB header")
    image[0:0x80] = bytes(header80)

    total_len = len(image)

    # --- set pointer to staging buffer (trace does this before first 0x05 burst) ---
    send_can(0x001, [0x0D, session] + _u32_be(staging_addr), delay=send_delay_ms)
    if not VCU_response(0x002, data=[0x0D, session], timeout=ptr_timeout):
        raise RuntimeError("Failed to set staging pointer (0x0D ack not seen)")

    offset = 0
    block_index = 0

    mv = memoryview(image)

    while offset < total_len:
        # dynamic block length
        this_len = min(block_size, total_len - offset)
        block = mv[offset:offset + this_len]

        # ---- fill staging with 0x05 frames + 0x02 polls (dynamic ack byte count) ----
        frames_since_poll = 0
        bytes_since_poll = 0

        i = 0
        while i < this_len:
            chunk = block[i:i + chunk_size]              # 1..6 bytes at end-of-block
            send_can(0x001, [0x05, session] + list(chunk), delay=send_delay_ms)

            frames_since_poll += 1
            bytes_since_poll += len(chunk)
            i += len(chunk)

            if frames_since_poll >= poll_every_frames:
                send_can(0x001, [0x02, session], delay=send_delay_ms)
                expected = bytes_since_poll & 0xFF
                if not VCU_response(0x002, data=[0x02, session, 0x00, expected, 0x00, 0x00], timeout=poll_timeout):
                    raise RuntimeError(
                        f"Poll ack missing/mismatch in block {block_index}: expected 0x{expected:02X}"
                    )
                frames_since_poll = 0
                bytes_since_poll = 0

        # final remainder poll for end-of-block (e.g., 0x20 after 0x8000, 0x24 at end of 0x5E80)
        if bytes_since_poll > 0:
            send_can(0x001, [0x02, session], delay=send_delay_ms)
            expected = bytes_since_poll & 0xFF
            if not VCU_response(0x002, data=[0x02, session, 0x00, expected, 0x00, 0x00], timeout=poll_timeout):
                raise RuntimeError(
                    f"Final remainder poll ack missing/mismatch in block {block_index}: expected 0x{expected:02X}"
                )

        # ---- set destination flash pointer (dynamic: base + offset) ----
        dest_addr = flash_base + offset
        send_can(0x001, [0x0D, session] + _u32_be(dest_addr), delay=send_delay_ms)
        if not VCU_response(0x002, data=[0x0D, session], timeout=ptr_timeout):
            raise RuntimeError(f"Failed to set dest pointer 0x{dest_addr:08X} (0x0D ack not seen)")

        # ---- commit (dynamic: this_len) ----
        len_m1 = this_len - 1
        # matches your trace exactly (8 bytes total):
        commit = [0x0B, session, 0x00, 0xE0, 0x80, 0x00, (len_m1 >> 8) & 0xFF, len_m1 & 0xFF]
        send_can(0x001, commit, delay=send_delay_ms)
        if not VCU_response(0x002, data=[0x0B, session, 0x01], timeout=commit_timeout):
            raise RuntimeError(f"Commit failed in block {block_index} (0x0B ack not seen)")

        offset += this_len
        block_index += 1

        # ---- IMPORTANT: trace only resets staging pointer if another block follows ----
        if offset < total_len:
            send_can(0x001, [0x0D, session] + _u32_be(staging_addr), delay=send_delay_ms)
            if not VCU_response(0x002, data=[0x0D, session], timeout=ptr_timeout):
                raise RuntimeError("Failed to reset staging pointer for next block (0x0D ack not seen)")

    return {
        "status": "success",
        "flash_base": hex(flash_base),
        "total_len": total_len,
        "block_size": block_size,
        "blocks": block_index,
        "last_block_len": (total_len % block_size) or block_size,
    }