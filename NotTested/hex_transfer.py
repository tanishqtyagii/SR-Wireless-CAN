import can
import time
from typing import Optional
from NotTested.CAN_controller import session_token
from NotTested.CAN_controller import VCU_response
from NotTested.CAN_controller import send_can
from NotTested.CAN_controller import heartbeat
from NotTested.CAN_controller import flash_kernel
from NotTested.CAN_controller import parse_hex_coverage, build_erase_commands_from_blocks, erase_plan_to_can_frames

# ALL STAGES
# 0x00C10000
def flash_hex(bus: can.Bus, hex_path: str) -> dict:

    # Downloads flash kernel to VCU memory MEMORY != FLASH MEMORY
    flash_kernel()


    # Clear flash memory from C10000
    cov = parse_hex_coverage(hex_path, erase_block=0x10000)
    plan = build_erase_commands_from_blocks(cov.touched_blocks, erase_block=0x10000)
    erase_plan_to_can_frames(plan, send_can=send_can, VCU_response=VCU_response, erase_timeout=3.0)
    print("Memory clear success les gooo")

    # Set pointer to flash memory
    send_can(0x001, [0x0D, 0x01, 0x00, 0xE0, 0x80, 0x00])

    if VCU_response(0x002, [0x0D, 0x01]):
        print("Flash memory pointer set")



    return {"status": "success"}