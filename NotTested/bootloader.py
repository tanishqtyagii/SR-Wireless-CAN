import can
import time
from typing import Optional
from CAN_controller import VCU_response, send_can, heartbeat


def bootload(bus: can.Bus) -> dict:
    session_token = [0x81, 0x16, 0x92, 0xAE]  # dont change.
    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x01], delay=0.5)
    send_can(canid=0x001, data=[0x03, 0xFF], delay=0.7)
    send_can(canid=0x001, data=[0x01, 0xFF], delay=0.9)

    # Frame blast / Power Cycle required within time limit
    for i in range(0x600, 0x700):
        send_can(canid=i, data=[0x2B, 0x25, 0x10, 0x01, 0x13, 0x03, 0x00, 0x00], delay=10)
        send_can(canid=0x001, data=[0x01, 0xFF], delay=10)
        print("DO A FUCKING POWER CYCLE")

    # 01 FF silence (probably waiting for VCU to boot)
    for i in range(650):
        send_can(canid=0x001, data=[0x01, 0xFF], delay=6)


    send_can(canid=0x001, data=[0x14, 0x01], delay=4)

    heartbeat()

    # 0x17 challenge
    send_can(canid=0x001, data=[0x17, 0x01, 0xB2, 0x25, 0x6A, 0xFC, 0x00])

    if not VCU_response(canid=0x002, prefix=[0x17, 0x01], timeout=1.0):
        raise Exception("challenge failed")

    send_can(canid=0x001, data=[0x17, 0x01, 0xE9, 0x30, 0x5A, 0x10, 0x01])
    if not VCU_response(canid=0x002, prefix=[0x17, 0x01], timeout=1.0):
        raise Exception("challenge failed")

    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00], delay=130)

    heartbeat() # BEEP BEEP

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xE0, 0x00, 0x00])

    if VCU_response(canid=0x002, data=[0x0D, 0x01]):
        print("IT FUCKING BOOTLOADED")
    else:
        raise Exception("FUCKKK")

    return {"status:": "BOOTLOADING SUCCESS"}
