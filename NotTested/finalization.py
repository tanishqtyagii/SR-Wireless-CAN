import time
from NotTested.CAN_controller import send_can, VCU_response, heartbeat, bus, key_0x17

SECURITY_KEY  = key_0x17[2:]              # [0xF5, 0x69, 0x5A, 0x48] — derived from CAN controller
SECURITY_KEY2 = [0xB6, 0xE0, 0xC2, 0xEC] # constant across runs


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



def finalize():

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
    if not VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1.0):  # doesn't matter what it is, just needs to be 10
        raise Exception("10 memory pointer failed")

    # 04 stream 3
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x80, 0x80])
    if not wait_for_stream(terminator=[0x04, 0x01, 0x00, 0x00]):
        raise Exception("04 stream 3 failed")


    # HEARTBEAT LOOP
    # each full cycle = pair 1 + pair 2 + security + 0D + 10
    for i in range(3):

        # -- heartbeat pair 1 --
        heartbeat()

        # -- heartbeat pair 2 --
        heartbeat()

        # -- end of cycle --
        security_handshake()

        send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
        if not VCU_response(canid=0x002, data=[0x0D, 0x01]):
            raise Exception("0D in heartbeat loop failed")

        send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
        if not VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1.0):  # doesn't matter what it is, just needs to be 10
            raise Exception("10 in heartbeat loop failed")
