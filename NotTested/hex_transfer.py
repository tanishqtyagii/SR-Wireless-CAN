import can
import time
import intelhex
from typing import Optional
from NotTested.CAN_controller import session_token
from NotTested.CAN_controller import VCU_response
from NotTested.CAN_controller import send_can
from NotTested.CAN_controller import heartbeat
from NotTested.CAN_controller import flash_kernel
from NotTested.CAN_controller import hex_clear_span, erase_plan_0x0C_frames

# ALL STAGES
# 0x00C10000
def flash_hex(bus: can.Bus, hex_path: str) -> dict:

    # Downloads flash kernel to VCU memory MEMORY != FLASH MEMORY
    flash_kernel()


    # Clear flash memory from C10000
    erase_start, length = hex_clear_span(hex_path)
    frames = erase_plan_0x0C_frames(erase_start, length)
    for frame in frames:
        send_can(canid=0x001, data=frame)
        if not VCU_response(canid=0x002, prefix=[0x0C, 0x01, 0x01]):
            raise Exception("Flash erase failed")

    print("Memory clear success les gooo")

    # Set pointer to flash memory
    send_can(0x001, [0x0D, 0x01, 0x00, 0xE0, 0x80, 0x00])

    if VCU_response(0x002, [0x0D, 0x01]):
        print("Flash memory pointer set")



    return {"status": "success"}