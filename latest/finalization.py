import time
from CAN_controller import CANController


def finalize(ctrl: CANController) -> dict:
    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response
    heartbeat = ctrl.heartbeat

    security_key = ctrl.key_0x17_1[2:]          # [0xF5, 0x69, 0x5A, 0x48]
    security_key2 = [0xB6, 0xE0, 0xC2, 0xEC]    # constant across runs

    def wait_for_stream(terminator: list[int], timeout_ms: float = 5000) -> bool:
        """
        Eat all frames until we see the terminator EXACTLY on 0x002.
        Uses the BufferedReader (NOT bus.recv), so it plays nicely with Notifier.
        """
        end = time.monotonic() + (timeout_ms / 1000.0)
        term = bytes(terminator)

        while True:
            remaining_s = end - time.monotonic()
            if remaining_s <= 0:
                return False

            msg = ctrl.reader.get_message(timeout=remaining_s)
            if msg is None:
                continue

            if msg.arbitration_id != 0x002:
                continue

            if bytes(msg.data) == term:
                return True

    def security_handshake(key=None):
        k = key if key is not None else security_key
        send_can(canid=0x001, data=[0x18, 0x01] + k)
        VCU_response(canid=0x002, data=[0x18, 0x01])

    security_handshake()

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x0D, 0x01])

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1000)  # ms

    # 04 stream 1
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x00, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x00, 0x00]):
        raise Exception("04 stream 1 failed")

    # 04 stream 2
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC1, 0x00, 0x00, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x74, 0x80]):
        raise Exception("04 stream 2 failed")

    security_handshake(key=security_key2)  # 18 B6 E0 C2 EC

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x00])
    VCU_response(canid=0x002, data=[0x0D, 0x01])

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x00, 0x00, 0x7C])
    VCU_response(canid=0x002, data=[0x10, 0x01, 0x80, 0x74, 0xC7, 0x83])

    security_handshake()

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x0D, 0x01])

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1000)  # ms

    # 04 stream 3
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x80, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x00, 0x00]):
        raise Exception("04 stream 3 failed")

    # HEARTBEAT LOOP
    for _ in range(3):
        heartbeat()
        heartbeat()

        security_handshake()

        send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
        VCU_response(canid=0x002, data=[0x0D, 0x01])

        send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
        VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1000)  # ms

    print("Finalization successful")
    return {"status": "success"}