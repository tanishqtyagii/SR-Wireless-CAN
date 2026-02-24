import can
import time
from typing import Optional
from NotTested.CAN_controller import SESSION_TOKEN as session_token
from NotTested.CAN_controller import VCU_response
from NotTested.CAN_controller import send_can

# ALL STAGES
# 0x00C10000
def flash_hex(bus: can.Bus, hex_path: str) -> dict:
    def hex_length(hex_path: str, expected_start: int = 0x00C10000) -> int:
        # Find the length of the hex file (for headers n shi)
        upper = 0
        min_addr = None
        max_addr = None
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
        return hex_length

    def erase_flash_from_c10000(hex_len: int, send_can):
        """
        Minimal erase sender matching your trace style:
          - erase 0x10000 blocks starting at 0xC10000
          - then erase remainder (if any) starting at next block boundary
          - length field is (len-1) as 2 bytes big-endian
        """
        base = 0x00C10000
        blk = 0x10000

        full = hex_len // blk
        rem = hex_len % blk

        # full 64KB blocks
        for i in range(full):
            addr = base + i * blk
            send_can(0x001, [0x0C, 0x01, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF, 0xFF, 0xFF])

            if VCU_response(0x002, [0x0C, 0x01, 0x01], timeout=0.5):
                print(f"Erased block {i}")

        # final partial block (if needed)
        if rem:
            addr = base + full * blk
            l = rem - 1
            send_can(0x001, [0x0C, 0x01, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF, (l >> 8) & 0xFF, l & 0xFF])

            if VCU_response(0x002, [0x0C, 0x01, 0x01], timeout=0.5):
                print(f"Erased remainder block")

    with open(hex_path, "rb") as file:
        for line in file:
            line_bytes = []
            for byte in line:
                line_bytes.append(byte)

    # Clear flash memory from C10000
    length = hex_length(hex_path)
    erase_flash_from_c10000(length, send_can)

    # Set pointer to flash memory
    send_can(0x001, [0x0D, 0x01, 0x00, 0xE0, 0x80, 0x00])

    if VCU_response(0x002, [0x0D, 0x01]):
        print("Flash memory pointer set")



    return {"status": "success"}