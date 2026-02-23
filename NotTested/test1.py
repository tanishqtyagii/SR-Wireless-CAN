
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple


def hex_length(path: str, expected_start: int = 0x00C10000) -> int:
    upper = 0
    min_addr = None
    max_addr = None

    for line in open(path, "r"):
        line = line.strip()
        if not line or line[0] != ":":
            continue

        ll = int(line[1:3], 16)
        addr16 = int(line[3:7], 16)
        rectype = int(line[7:9], 16)

        if rectype == 0x04:              # extended linear address
            upper = int(line[9:13], 16)

        elif rectype == 0x00 and ll:     # data record
            base = (upper << 16) | addr16
            if min_addr is None or base < min_addr:
                min_addr = base
            end = base + ll - 1
            if max_addr is None or end > max_addr:
                max_addr = end

        elif rectype == 0x01:            # EOF
            break

    if min_addr is None or max_addr is None:
        raise ValueError("No data records found")

    if min_addr != expected_start:
        raise ValueError(f"Unexpected start address: 0x{min_addr:08X} (expected 0x{expected_start:08X})")

    return max_addr - min_addr + 1  # bytes to clear

print(f"0x{hex_length(path="/Users/tanishqtyagi/TTC Reverse Protocl/Code/hex_files/main.hex"):X} ")