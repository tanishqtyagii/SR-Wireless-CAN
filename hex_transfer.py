import can
import time
from typing import Optional
from CAN_controller import SESSION_TOKEN as session_token

# ALL STAGES
# 0x00C10000
def flash_hex(bus: can.Bus, hex_path: str) -> dict:
    # Find the length of the hex file (for headers n shi)
    upper = 0
    min_addr = None
    max_addr = None
    expected_start = 0x00C10000

    for line in open(hex_path, "r"):
        line = line.strip()
        if not line or line[0] != ":":
            continue

        ll = int(line[1:3], 16)
        addr16 = int(line[3:7], 16)
        rectype = int(line[7:9], 16)

        if rectype == 0x04:  # extended linear address
            upper = int(line[9:13], 16)

        elif rectype == 0x00 and ll:  # data record
            base = (upper << 16) | addr16
            if min_addr is None or base < min_addr:
                min_addr = base
            end = base + ll - 1
            if max_addr is None or end > max_addr:
                max_addr = end

        elif rectype == 0x01:  # EOF
            break

    if min_addr is None or max_addr is None:
        raise ValueError("No data records found")

    if min_addr != expected_start:
        raise ValueError(f"Unexpected start address: 0x{min_addr:08X} (expected 0x{expected_start:08X})")


    hex_length = max_addr - min_addr + 1  # SUPER DUPER IMPORTANT FOR HEADERS





    return {"status": "success"}