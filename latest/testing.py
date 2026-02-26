import intelhex

def hex_clear_span(hex_path: str, erase_block: int = 0x10000) -> tuple[int, int]:
    """
    Returns (erase_start_addr, length_to_clear) based on the HEX's highest/lowest used addresses.
    """
    ih = IntelHex(hex_path)

    min_addr = ih.minaddr()
    max_addr = ih.maxaddr()

    # Check for empty hex
    if min_addr is None or max_addr is None:
        raise ValueError("HEX has no data records")

    erase_start = min_addr & ~(erase_block - 1)
    length_to_clear = (max_addr - erase_start) + 1
    return erase_start, length_to_clear

def erase_plan_0x0C_frames(erase_start: int, length_to_clear: int,
                           chunk: int = 0x10000) -> list[list[int]]:
    """
    Builds 0x0C erase frames of the form:
      [0x0C, session, addr32_be(4 bytes), (len-1)_be(2 bytes)]
    Split into chunk-sized erases (default 0x10000), final chunk is remainder.
    """
    if length_to_clear <= 0:
        return []

    frames: list[list[int]] = []
    addr = erase_start
    remaining = length_to_clear

    while remaining > 0:
        this_len = min(remaining, chunk)
        len_m1 = this_len - 1  # protocol expects (len-1)

        frames.append([
            0x0C, 0x01,
            (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF,
            (len_m1 >> 8) & 0xFF, len_m1 & 0xFF,  # big-endian
        ])

        addr += this_len
        remaining -= this_len

    return frames

print(hex_clear_span("231_80kw.hex"))

