from __future__ import annotations

import time
from typing import Any, Callable, Optional

try:
    import can  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    can = None  # type: ignore

DEFAULT_SESSION_TOKEN = [0x81, 0x16, 0x92, 0xAE]
HOST_CAN_ID = 0x001
VCU_CAN_ID = 0x002


def bootload(
    bus: "can.Bus",
    *,
    session_token: Optional[list[int]] = None,
    send_can_fn: Optional[Callable[..., Any]] = None,
    vcu_response_fn: Optional[Callable[..., Any]] = None,
    strict_identify_ack: bool = True,
) -> dict[str, Any]:
    if can is None:
        raise RuntimeError("python-can is required for bootload operations")
    token = list(session_token) if session_token is not None else list(DEFAULT_SESSION_TOKEN)

    def local_send(canid: int, data: list[int], delay: float = 0.0) -> None:
        msg = can.Message(arbitration_id=canid, data=data, is_extended_id=False)
        bus.send(msg)
        time.sleep(max(0.0, delay) / 1000.0)

    def send(canid: int, data: list[int], delay: float = 0.0) -> None:
        if send_can_fn is not None:
            send_can_fn(canid=canid, data=data, delay=delay)
            return
        local_send(canid=canid, data=data, delay=delay)

    def local_recv(canid: int, data: Optional[list[int]] = None, timeout: float = 0.3) -> bool:
        target = None if data is None else bytes(data)
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            msg = bus.recv(timeout=remaining)
            if msg is None or msg.arbitration_id != canid:
                continue
            if target is None or bytes(msg.data) == target:
                return True

    def recv(canid: int, data: Optional[list[int]] = None, timeout: float = 0.3) -> bool:
        if vcu_response_fn is not None:
            return bool(vcu_response_fn(canid=canid, data=data, timeout=timeout))
        return local_recv(canid=canid, data=data, timeout=timeout)

    def heartbeat() -> None:
        send(HOST_CAN_ID, [0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        send(HOST_CAN_ID, [0x11, 0x01] + token + [0x01])
        if not recv(VCU_CAN_ID, [0x11, 0x01] + token, timeout=1.0):
            raise RuntimeError("VCU heartbeat failed during bootload")

    send(HOST_CAN_ID, [0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x01], delay=0.5)
    send(HOST_CAN_ID, [0x03, 0xFF], delay=0.7)
    send(HOST_CAN_ID, [0x01, 0xFF], delay=0.9)

    for can_id in range(0x600, 0x700):
        send(can_id, [0x2B, 0x25, 0x10, 0x01, 0x13, 0x03, 0x00, 0x00], delay=10)
        send(HOST_CAN_ID, [0x01, 0xFF], delay=10)

    for _ in range(650):
        send(HOST_CAN_ID, [0x01, 0xFF], delay=6)

    time.sleep(0.0042)

    identify_ok = False
    identify_target = [0x14, 0x01] + token
    for value in range(0x00, 0x100):
        send(HOST_CAN_ID, [0x14, value], delay=0.0)
        if recv(VCU_CAN_ID, identify_target, timeout=0.040):
            identify_ok = True
            break

    if strict_identify_ack and not identify_ok:
        raise RuntimeError("VCU identify/select did not ACK")

    heartbeat()

    send(HOST_CAN_ID, [0x17, 0x01, 0xB2, 0x25, 0x6A, 0xFC, 0x00])
    if not recv(VCU_CAN_ID, timeout=1.0):
        raise RuntimeError("Missing response for first 0x17 challenge")

    send(HOST_CAN_ID, [0x17, 0x01, 0xE9, 0x30, 0x5A, 0x10, 0x01])
    if not recv(VCU_CAN_ID, timeout=1.0):
        raise RuntimeError("Missing response for second 0x17 challenge")

    send(HOST_CAN_ID, [0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00], delay=130)
    heartbeat()

    send(HOST_CAN_ID, [0x0D, 0x01, 0x00, 0xE0, 0x00, 0x00])
    if not recv(VCU_CAN_ID, [0x0D, 0x01], timeout=1.0):
        raise RuntimeError("Bootload entry pointer ACK failed")

    return {
        "status": "BOOTLOADING_SUCCESS",
        "identify_ack": identify_ok,
        "session_token": token,
    }
