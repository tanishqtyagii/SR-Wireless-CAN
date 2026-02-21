import can
import time
from typing import Optional

can_id = {
    "pc2vcu": 0x001,
    "vcu2pc": 0x002,
    "0x19bytes": 0x019
}

bus = can.Bus(interface='socketcan', channel='can0')

# def VCU_response(canid: int, data: Optional[list[int]] = None, timeout: Optional[float] = 0.3) -> bool:
#     if data is not None:
#         target = bytes(data)
#
#     end = time.monotonic() + timeout
#
#     while True:
#         remaining = end - time.monotonic()
#         if remaining <= 0:
#             return False
#
#         msg = bus.recv(timeout=remaining)  # one frame per call
#         if data is None:
#             if canid == 0x17:
#                 return True
#         if msg and msg.arbitration_id == canid and bytes(msg.data) == target:
#             return True

def server_response(canid: int, data: Optional[list[int]] = None, timeout: float = 0.3) -> bool:
    if not hasattr(server_response, "seen"):
        server_response.seen = []  # list of (arbitration_id, data_bytes)

    target = None if data is None else bytes(data)
    end = time.monotonic() + timeout

    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False

        msg = bus.recv(timeout=remaining)
        if msg is None:
            continue

        server_response.seen.append((msg.arbitration_id, bytes(msg.data)))

        if msg.arbitration_id != canid:
            continue

        if target is None or bytes(msg.data) == target:
            return True

def send_can(canid: int, data: list[int], delay: Optional[float] = 0.5):
    # id = can_id[canid] # ex. 0x001

    msg = can.Message(
        arbitration_id=canid,
        data=data,
        is_extended_id=False
        # DLC handled internally yurrr
    )
    bus.send(msg)
    time.sleep(delay/1000) # ms -> s

# Random frames (not sure what they do)
# Update: I know what they do
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

time.sleep(0.0042)

# Server ack thing idek im just tryna copy the trc
server_ack = False

# while server_ack != True:
session_token = [0x01, 0x81, 0x16, 0x92, 0xAE]

for i in range(0x00, 0x100):
    send_can(canid=0x001, data=[0x14, i], delay=0.0)
    if server_response(canid=0x002, data=[0x14] + session_token, timeout=35):  # ~35ms
        break

# Heartbeat check (ex: HEY IM STILL HERE)
send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])

send_can(canid=0x001, data=[0x11] + session_token + [0x01])

if server_response(canid=0x002, data=[0x11] + session_token):
    print("VCU is still alive")
else:
    raise Exception("server died")

# 0x17 challenge
send_can(canid=0x001, data=[0x17, 0x01, 0xB2, 0x25, 0x6A, 0xFC, 0x00])

server_response(canid=0x002)

send_can(canid=0x001, data=[0x17, 0x01, 0xE9, 0x30, 0x5A, 0x10, 0x01])
server_response(canid=0x002)

send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00], delay=130)
send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
send_can(canid=0x001, data=[0x11] + session_token + [0x01], delay=5)

if server_response(canid=0x002, data=[0x11] + session_token):
    print("VCU is still alive")
else:
    raise Exception("server fucking commited")
send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xE0, 0x00, 0x00])

if server_response(canid=0x002, data=[0x0D, 0x01]):
    print("IT FUCKING BOOTLOADED")
else:
    raise Exception("FUCKKK")

bus.shutdown()