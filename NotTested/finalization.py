import time
from NotTested.CAN_controller import send_can, VCU_response, SESSION_TOKEN, bus

# session-derived, vary per run — no idea how they're computed yet
SECURITY_KEY  = [0xF5, 0x69, 0x5A, 0x48]
CHALLENGE_A   = [0xC9, 0x1E, 0x2E, 0xCE]
CHALLENGE_B   = [0xF5, 0x69, 0x5A, 0x48]  # same as SECURITY_KEY, not a coincidence probably
SECURITY_KEY2 = [0xB6, 0xE0, 0xC2, 0xEC]  # constant across runs


def wait_for_stream(terminator: list[int], timeout: float = 5.0) -> bool:
    # eat all 04 packets until we get the terminator frame
    end = time.monotonic() + timeout
    term = bytes(terminator)
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        msg = bus.recv(timeout=remaining)
        if msg is None:
            continue
        if msg.arbitration_id == 0x002 and bytes(msg.data) == term:
            return True


def security_handshake(key=None):
    k = key if key is not None else SECURITY_KEY
    send_can(canid=0x001, data=[0x18, 0x01] + k)
    if not VCU_response(canid=0x002, data=[0x18, 0x01]):
        raise Exception("security handshake failed")


def heartbeat_pair(challenge: list[int], end_byte: int):
    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=0x001, data=[0x11, 0x01] + SESSION_TOKEN + [0x01])
    if not VCU_response(canid=0x002, data=[0x11, 0x01] + SESSION_TOKEN):
        raise Exception("heartbeat ack failed")

    send_can(canid=0x001, data=[0x19, 0x01] + challenge)
    if not VCU_response(canid=0x002, data=[0x19, 0x01, 0x01]):
        raise Exception("19 challenge failed")

    send_can(canid=0x001, data=[0x11, 0x01] + SESSION_TOKEN + [end_byte])
    if not VCU_response(canid=0x002, data=[0x11, 0x01] + SESSION_TOKEN):
        raise Exception("heartbeat close failed")


def finalize():

    # 02 data check
    send_can(canid=0x001, data=[0x02, 0x01])
    if not VCU_response(canid=0x002, prefix=[0x02, 0x01]):
        raise Exception("02 data check failed")

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC2, 0x80, 0x00])
    if not VCU_response(canid=0x002, data=[0x0D, 0x01]):
        raise Exception("0D init failed")

    # 0B readiness check
    send_can(canid=0x001, data=[0x0B, 0x01, 0x00, 0xE0, 0x80, 0x00, 0x5E, 0x7F])
    if not VCU_response(canid=0x002, data=[0x0B, 0x01, 0x01]):
        raise Exception("0B readiness failed")

    security_handshake()

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    if not VCU_response(canid=0x002, data=[0x0D, 0x01]):
        raise Exception("0D failed")

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    if not VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1.0):  # doesn't matter what it is, just needs to be 10
        raise Exception("10 memory pointer failed")

    # 04 stream 1
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x00, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x00, 0x00]):
        raise Exception("04 stream 1 failed")

    # 04 stream 2
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC1, 0x00, 0x00, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x74, 0x80]):
        raise Exception("04 stream 2 failed")

    security_handshake(key=SECURITY_KEY2)  # 18 B6 E0 C2 EC - constant variant

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x00])
    if not VCU_response(canid=0x002, data=[0x0D, 0x01]):
        raise Exception("0D second op failed")

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x00, 0x00, 0x7C])
    if not VCU_response(canid=0x002, data=[0x10, 0x01, 0x80, 0x74, 0xC7, 0x83]):
        raise Exception("10 second op failed")

    security_handshake()

    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    if not VCU_response(canid=0x002, data=[0x0D, 0x01]):
        raise Exception("0D failed")

    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    if not VCU_response(canid=0x002, timeout=1.0):  # doesn't matter what it is, just needs to be 10
        raise Exception("10 memory pointer failed")

    # 04 stream 3
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x80, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x00, 0x00]):
        raise Exception("04 stream 3 failed")


    # HEARTBEAT LOOP
    # each full cycle = pair 1 + pair 2 + security + 0D + 10
    while True:

        # -- heartbeat pair 1 --
        heartbeat_pair(challenge=CHALLENGE_A, end_byte=0x00)

        # -- heartbeat pair 2 --
        heartbeat_pair(challenge=CHALLENGE_B, end_byte=0x01)

        # -- end of cycle --
        security_handshake()

        send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
        if not VCU_response(canid=0x002, data=[0x0D, 0x01]):
            raise Exception("0D in heartbeat loop failed")

        send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
        if not VCU_response(canid=0x002, timeout=1.0):  # doesn't matter what it is, just needs to be 10
            raise Exception("10 in heartbeat loop failed")
