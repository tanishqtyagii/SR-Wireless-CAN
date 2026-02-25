from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Tuple


@dataclass(frozen=True)
class HexCoverage:
    min_addr: int
    max_addr: int
    touched_blocks: Tuple[int, ...]  # block base addresses, sorted


def parse_hex_coverage(hex_path: str, erase_block: int = 0x10000) -> HexCoverage:
    """
    Parses Intel HEX and returns:
      - min/max absolute address that appears in any data record (rectype 00)
      - which erase blocks (by base address) contain any data bytes
    Notes:
      - Does NOT validate Intel HEX checksums (same as your approach).
      - Uses type 04 (extended linear address) for upper 16 bits.
    """
    upper = 0
    min_addr = None
    max_addr = None
    touched = set()

    with open(hex_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or not line.startswith(":"):
                continue

            ll = int(line[1:3], 16)
            addr16 = int(line[3:7], 16)
            rectype = int(line[7:9], 16)

            if rectype == 0x04:  # extended linear address
                upper = int(line[9:13], 16)
                continue

            if rectype == 0x00 and ll:  # data record
                base = (upper << 16) | addr16
                end = base + ll - 1

                if min_addr is None or base < min_addr:
                    min_addr = base
                if max_addr is None or end > max_addr:
                    max_addr = end

                # mark every erase-block that overlaps [base, end]
                blk_start = base // erase_block
                blk_end = end // erase_block
                for b in range(blk_start, blk_end + 1):
                    touched.add(b * erase_block)

                continue

            if rectype == 0x01:  # EOF
                break

    if min_addr is None or max_addr is None:
        raise ValueError("No data records found in Intel HEX")

    touched_blocks = tuple(sorted(touched))
    return HexCoverage(min_addr=min_addr, max_addr=max_addr, touched_blocks=touched_blocks)


def build_erase_commands_from_blocks(
    touched_blocks: Iterable[int],
    erase_block: int = 0x10000,
) -> List[Tuple[int, int]]:
    """
    Returns a list of (addr, length) tuples to erase, where length is the actual length,
    NOT (len-1).
    Here we erase whole blocks for each touched block.
    """
    return [(blk, erase_block) for blk in touched_blocks]


def erase_plan_to_can_frames(
    plan: Iterable[Tuple[int, int]],
    send_can: Callable[[int, List[int]], None],
    VCU_response: Callable[[int, List[int]], bool],
):
    """
    Sends your protocol's 0x0C erase frames:
      [0x0C, 0x01, addr32_be..., len_minus_1_be]
    """
    for addr, length in plan:
        l = length - 1
        frame = [
            0x0C, 0x01,
            (addr >> 24) & 0xFF,
            (addr >> 16) & 0xFF,
            (addr >> 8) & 0xFF,
            addr & 0xFF,
            (l >> 8) & 0xFF,
            l & 0xFF,
        ]
        send_can(0x001, frame)

        # Your ack pattern may be different; adjust to whatever your trace shows.
        # You mentioned you see the VCU ack after some time.
        VCU_response(0x002, [0x0C, 0x01, 0x01], timeout=0.5)

print(parse_hex_coverage("../hex_files/main.hex"))