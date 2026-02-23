import can
from bootloader2 import bootload
import time
from typing import Optional

global VCU_state

bus = can.Bus(interface='socketcan', channel='can0')

SESSION_TOKEN = [0x81, 0x16, 0x92, 0xAE] # dont change.

def VCU_response(canid: int, data: Optional[list[int]] = None, timeout: float = 0.3) -> bool:
    if not hasattr(VCU_response, "seen"):
        VCU_response.seen = []  # list of (arbitration_id, data_bytes)

    target = None if data is None else bytes(data)
    end = time.monotonic() + timeout

    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False

        msg = bus.recv(timeout=remaining)
        if msg is None:
            continue

        VCU_response.seen.append((msg.arbitration_id, bytes(msg.data)))

        if msg.arbitration_id != canid:
            continue

        if target is None or bytes(msg.data) == target:
            return True


def send_can(canid: int, data: list[int], delay: Optional[float] = 5):
    # id = can_id[canid] # ex. 0x001

    msg = can.Message(
        arbitration_id=canid,
        data=data,
        is_extended_id=False
        # DLC handled internally yurrr
    )
    bus.send(msg)
    time.sleep(delay / 1000)  # ms -> s


def heartbeat():
    # Heartbeat check (ex: HEY IM STILL HERE)
    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=0x001, data=[0x11] + SESSION_TOKEN + [0x01])

    if VCU_response(canid=0x002, data=[0x11] + SESSION_TOKEN):
        print("VCU is still alive")
    else:
        raise Exception("server died")

bus.shutdown()