from typing import List, Tuple
import intelhex

from CAN_controller import CANController, VCUTimeoutError


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


def _erase_plan_0x0C_frames(
    erase_start: int, length: int, *, session: int, chunk: int = 0x10000
) -> List[List[int]]:
    """
    Build 0x0C erase frames:  [0x0C, session, addr32_be(4), (len-1)_be(2)]
    Split into 0x10000-byte chunks as seen in the trace.
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


# ---------------------------------------------------------------------------
# Main transfer function
# ---------------------------------------------------------------------------

def flash_hex(
    ctrl: CANController,
    hex_path: str,
    header80: List[int],
    *,
    session: int = 0x01,
    staging_addr: int = 0xE08000,
    block_size: int = 0x8000,
    chunk_size: int = 6,
    poll_every_frames: int = 11,      # 11 * 6 = 66 (0x42) bytes per poll
    send_delay_ms: float = 1.0,
    poll_timeout: float = 1.0,        # seconds
    ptr_timeout: float = 1.0,         # seconds
    commit_timeout: float = 3.0,      # seconds
    do_erase: bool = True,
) -> dict:
    """
    Stream firmware to the VCU.

    Matches the trace behavior:
      - optional erase via 0x0C frames
      - 0x0D -> staging buffer (0xE08000)
      - for each block:
          * 0x05 01 payload bytes (up to 6 per frame)
          * 0x02 01 poll every 11 frames + final remainder poll
          * 0x0D -> destination flash address
          * 0x0B commit from staging with (len-1)
          * if more blocks remain: 0x0D -> staging again
    """
    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response

    if not isinstance(header80, list) or len(header80) != 0x80:
        raise ValueError("header80 must be a list of exactly 0x80 ints")
    if any((not isinstance(b, int) or b < 0 or b > 0xFF) for b in header80):
        raise ValueError("header80 elements must be ints 0..255")

    # --- erase (span derived from hex file) ---
    if do_erase:
        erase_start, length = _hex_span(hex_path)
        for frame in _erase_plan_0x0C_frames(erase_start, length, session=session):
            send_can(0x001, frame, delay=send_delay_ms)
            VCU_response(0x002, prefix=[0x0C, session, 0x01], timeout=2.0)

    # --- build linearized image; overwrite first 0x80 bytes with APDB header ---
    flash_base, image = _load_linear_image(hex_path, pad=0xFF)
    if len(image) < 0x80:
        raise ValueError("HEX image is smaller than 0x80 bytes; cannot apply APDB header")
    image[0:0x80] = bytes(header80)

    total_len = len(image)
    mv = memoryview(image)

    # --- set pointer to staging buffer before first burst ---
    send_can(0x001, [0x0D, session] + _u32_be(staging_addr), delay=send_delay_ms)
    VCU_response(0x002, data=[0x0D, session], timeout=ptr_timeout)

    offset = 0
    block_index = 0

    while offset < total_len:
        this_len = min(block_size, total_len - offset)
        block = mv[offset:offset + this_len]

        # ---- stream block into staging: 0x05 frames + periodic 0x02 polls ----
        frames_since_poll = 0
        bytes_since_poll = 0
        i = 0

        while i < this_len:
            chunk = block[i:i + chunk_size]
            send_can(0x001, [0x05, session] + list(chunk), delay=send_delay_ms)

            frames_since_poll += 1
            bytes_since_poll += len(chunk)
            i += len(chunk)

            if frames_since_poll >= poll_every_frames:
                send_can(0x001, [0x02, session], delay=send_delay_ms)
                expected = bytes_since_poll & 0xFF
                VCU_response(
                    0x002,
                    data=[0x02, session, 0x00, expected, 0x00, 0x00],
                    timeout=poll_timeout,
                )
                frames_since_poll = 0
                bytes_since_poll = 0

        # final remainder poll for end-of-block
        if bytes_since_poll > 0:
            send_can(0x001, [0x02, session], delay=send_delay_ms)
            expected = bytes_since_poll & 0xFF
            VCU_response(
                0x002,
                data=[0x02, session, 0x00, expected, 0x00, 0x00],
                timeout=poll_timeout,
            )

        # ---- set destination flash pointer ----
        dest_addr = flash_base + offset
        send_can(0x001, [0x0D, session] + _u32_be(dest_addr), delay=send_delay_ms)
        VCU_response(0x002, data=[0x0D, session], timeout=ptr_timeout)

        # ---- commit block from staging to flash ----
        len_m1 = this_len - 1
        commit = [0x0B, session, 0x00, 0xE0, 0x80, 0x00, (len_m1 >> 8) & 0xFF, len_m1 & 0xFF]
        send_can(0x001, commit, delay=send_delay_ms)
        VCU_response(0x002, data=[0x0B, session, 0x01], timeout=commit_timeout)

        offset += this_len
        block_index += 1

        # reset staging pointer only if another block follows (matches trace)
        if offset < total_len:
            send_can(0x001, [0x0D, session] + _u32_be(staging_addr), delay=send_delay_ms)
            VCU_response(0x002, data=[0x0D, session], timeout=ptr_timeout)

    print(f"Hex transfer complete: {block_index} block(s), {total_len} bytes, base 0x{flash_base:08X}")
    return {
        "status": "success",
        "flash_base": hex(flash_base),
        "total_len": total_len,
        "blocks": block_index,
    }
