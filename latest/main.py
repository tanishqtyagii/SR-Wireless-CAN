"""
VCU firmware update — full end-to-end flow:
  1. bootload      — enter bootloader, authenticate session
  2. flash_kernel  — stream the kernel image (hardcoded trace replay)
  3. hex_transfer  — erase flash, stream firmware hex, commit blocks
  4. finalize      — CRC verify, heartbeat loop, wrap up

Usage:
    python main.py <hex_path> [interface] [channel]

    hex_path   — path to the firmware .hex file (e.g. 231_80kw.hex)
    interface  — python-can interface name (default: socketcan)
    channel    — CAN channel (default: can0)
"""

import sys

from CAN_controller import CANController, VCUTimeoutError
from bootloader import bootload
from flash_kernel import flash_kernel
from hex_transfer import flash_hex
from finalization import finalize


# ---------------------------------------------------------------------------
# APDB header (first 0x80 bytes of the firmware image).
# Replace this with the actual header bytes for the firmware being flashed.
# ---------------------------------------------------------------------------
HEADER80: list = [0x00] * 0x80  # TODO: supply real APDB header


def main(hex_path: str, interface: str = "pcan", channel: str = "PCAN_USBBUS1"):
    ctrl = CANController(interface=interface, channel=channel)

    try:
        print("=== Step 1: Bootload ===")
        bootload(ctrl)

        print("=== Step 2: Flash Kernel ===")
        flash_kernel(ctrl)

        print("=== Step 3: Hex Transfer ===")
        flash_hex(ctrl, hex_path=hex_path, header80=HEADER80)

        print("=== Step 4: Finalize ===")
        finalize(ctrl)

        print("=== Done ===")

    except VCUTimeoutError as e:
        print(f"[TIMEOUT] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
    finally:
        ctrl.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py <hex_path> [interface] [channel]")
        sys.exit(1)

    hex_path = sys.argv[1]
    interface = sys.argv[2] if len(sys.argv) > 2 else "socketcan"
    channel   = sys.argv[3] if len(sys.argv) > 3 else "can0"

    main(hex_path, interface, channel)
