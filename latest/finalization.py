import time
from CAN_controller import CANController


def finalize(ctrl: CANController) -> dict:
    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response

    token = ctrl.session_token  # [0x81, 0x16, 0x92, 0xAE]

    # These should already exist on your controller exactly like you set them up:
    key_0x19_2 = ctrl.key_0x19_2  # [0x19, 0x01, 0xC9, 0x1E, 0x2E, 0xCE]
    key_0x19_1 = ctrl.key_0x19_1  # [0x19, 0x01, 0xF5, 0x69, 0x5A, 0x48]

    # 0x18 seed used in your trace snippet: 18 01 F5 69 5A 48
    # (you can swap if later you see the other constant seed being used)
    key_0x18 = ctrl.key_0x17_1[:][2:]  # [0xF5, 0x69, 0x5A, 0x48] (derived)

    def _expect_11_token(timeout_ms: float = 1000) -> None:
        # Trace expects: 0002  11 01 81 16 92 AE  (6 bytes, no trailing mode byte)
        VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=timeout_ms)

    def _auth_pair(key_19: list[int], mode_byte: int) -> None:
        """
        Copies:
          11 FF ...
          11 01 <token> <mode>
          expect 11 01 <token>
          19 01 ....
          expect 19 01 01
          11 01 <token> <othermode>
          expect 11 01 <token>
        """
        # 28799 / 28806 / 28819 / 28826 / 28839 style
        send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])

        # 28800 style
        send_can(canid=0x001, data=[0x11, 0x01] + token + [mode_byte])
        _expect_11_token(timeout_ms=1500)

        # 28802 / 28809 style
        send_can(canid=0x001, data=key_19)
        VCU_response(canid=0x002, data=[0x19, 0x01, 0x01], timeout=1500)

        # 28804 / 28811 style
        other = 0x00 if mode_byte == 0x01 else 0x01
        send_can(canid=0x001, data=[0x11, 0x01] + token + [other])
        _expect_11_token(timeout_ms=1500)

    def _crc_check_block() -> None:
        """
        Copies:
          18 01 F5 69 5A 48
          expect 18 01
          0D 01 00 C1 00 80
          expect 0D 01
          10 01 00 01 DE 00
          expect 10 01 <4 bytes> (prefix)
        """
        # 28813
        send_can(canid=0x001, data=[0x18, 0x01] + key_0x18)
        VCU_response(canid=0x002, data=[0x18, 0x01], timeout=1500)

        # 28815
        send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
        VCU_response(canid=0x002, data=[0x0D, 0x01], timeout=1500)

        # 28817
        send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
        # 28818: response varies (it’s the CRC), so match prefix only
        VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=60000)

    # ---------------------------------------------------------------------
    # This section matches your trace order (first big block shown)
    # ---------------------------------------------------------------------

    # 28799–28805
    _auth_pair(key_19=key_0x19_2, mode_byte=0x01)

    # 28806–28812
    _auth_pair(key_19=key_0x19_1, mode_byte=0x01)

    # 28813–28818
    _crc_check_block()

    # 28819–28825
    _auth_pair(key_19=key_0x19_2, mode_byte=0x01)

    # 28826–28832
    _auth_pair(key_19=key_0x19_1, mode_byte=0x01)

    # 28833–28838
    _crc_check_block()

    # 28839–28845 (ends mid-block in your pasted snippet)
    _auth_pair(key_19=key_0x19_2, mode_byte=0x01)

    print("Finalization successful")
    return {"status": "success"}