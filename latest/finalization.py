import time
from CAN_controller import CANController, VCUTimeoutError


def finalize(ctrl: CANController) -> dict:
    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response
    heartbeat = ctrl.heartbeat

    security_key  = ctrl.key_0x17_1[2:]       # [0xF5, 0x69, 0x5A, 0x48]
    security_key2 = [0xB6, 0xE0, 0xC2, 0xEC]  # constant across runs

    def wait_for_stream(terminator: list, timeout: float = 5.0) -> bool:
        """Consume 0x002 messages until the exact terminator frame is seen."""
        end = time.monotonic() + timeout
        term = bytes(terminator)
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            msg = ctrl.bus.recv(timeout=remaining)
            if msg is None:
                return False
            if msg.arbitration_id == 0x002 and bytes(msg.data) == term:
                return True

    def security_handshake(key=None):
        k = key if key is not None else security_key
        send_can(canid=0x001, data=[0x18, 0x01] + k)
        VCU_response(canid=0x002, data=[0x18, 0x01])

    security_handshake()

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x0D, 0x01])

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1.0)

    # 04 stream 1
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x00, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x00, 0x00]):
        raise Exception("04 stream 1 failed")

    # 04 stream 2
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC1, 0x00, 0x00, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x74, 0x80]):
        raise Exception("04 stream 2 failed")

    security_handshake(key=security_key2)

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x00])
    VCU_response(canid=0x002, data=[0x0D, 0x01])

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x00, 0x00, 0x7C])
    VCU_response(canid=0x002, data=[0x10, 0x01, 0x80, 0x74, 0xC7, 0x83])

    security_handshake()

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x0D, 0x01])

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1.0)

    # 04 stream 3
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x80, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x00, 0x00]):
        raise Exception("04 stream 3 failed")

    # Heartbeat loop (3 cycles)
    for _ in range(3):
        heartbeat()
        heartbeat()

        security_handshake()

        send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
        VCU_response(canid=0x002, data=[0x0D, 0x01])

        send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
        VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1.0)

    print("Finalization successful")
    return {"status": "success"}
