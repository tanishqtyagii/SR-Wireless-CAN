import can
import time
from typing import Optional


class VCUTimeoutError(TimeoutError):
    def __init__(self, canid: int, timeout: float, expected_data=None, expected_prefix=None):
        self.canid = canid
        self.timeout = timeout
        self.expected_data = bytes(expected_data) if expected_data is not None else None
        self.expected_prefix = bytes(expected_prefix) if expected_prefix is not None else None
        super().__init__(str(self))

    @staticmethod
    def _fmt_can_line(canid: int, data: bytes) -> str:
        return f"{canid:04X}  " + " ".join(f"{b:02X}" for b in data) + " "

    def __str__(self) -> str:
        if self.expected_data is not None:
            expected_line = self._fmt_can_line(self.canid, self.expected_data)
            expected_kind = "exact"
        elif self.expected_prefix is not None:
            expected_line = self._fmt_can_line(self.canid, self.expected_prefix)
            expected_kind = "prefix"
        else:
            expected_line = f"{self.canid:04X}  <any> "
            expected_kind = "any"

        return (
            f"VCU response timeout after {self.timeout:.3f}s\n"
            f"Expected ({expected_kind}): {expected_line}"
        )


class CANController:
    def __init__(self, interface: str = "socketcan", channel: str = "can0"):
        self.interface = interface
        self.channel = channel

        self.bus = can.Bus(interface=interface, channel=channel)

        # Constants — these never change
        self.session_token = [0x81, 0x16, 0x92, 0xAE]
        self.key_0x17_1 = [0x17, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x17_2 = [0x17, 0x01, 0x78, 0x52, 0x25, 0x6C]
        self.key_0x19_1 = [0x19, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x19_2 = [0x19, 0x01, 0xC9, 0x1E, 0x2E, 0xCE]

    def send_can(self, canid: int, data: list, delay: Optional[float] = 2):
        """Send a CAN message. delay is post-send pause in milliseconds (default 2ms)."""
        msg = can.Message(
            arbitration_id=canid,
            data=data,
            is_extended_id=False,
        )
        self.bus.send(msg)
        if delay:
            time.sleep(delay / 1000)  # ms -> s

    def VCU_response(
        self,
        canid: int,
        data: Optional[list] = None,
        prefix: Optional[list] = None,
        timeout: float = 0.1,
    ) -> bool:
        """
        Block on bus.recv() until a matching message arrives from canid.
        timeout is in seconds (default 0.1 = 100ms).
        Raises VCUTimeoutError if the response is not seen in time.
        Accepts any message with matching canid when data and prefix are both None.
        """
        if data is not None and prefix is not None:
            raise ValueError("only use data OR prefix, not both")

        target = None if data is None else bytes(data)
        target_prefix = None if prefix is None else bytes(prefix)

        end = time.monotonic() + timeout

        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise VCUTimeoutError(
                    canid=canid,
                    timeout=timeout,
                    expected_data=data,
                    expected_prefix=prefix,
                )

            msg = self.bus.recv(timeout=remaining)
            if msg is None:
                # recv timed out
                raise VCUTimeoutError(
                    canid=canid,
                    timeout=timeout,
                    expected_data=data,
                    expected_prefix=prefix,
                )

            if msg.arbitration_id != canid:
                continue  # not our message; keep waiting

            payload = bytes(msg.data)

            if target is not None:
                if payload == target:
                    return True
                continue  # wrong payload; keep waiting

            if target_prefix is not None:
                if payload.startswith(target_prefix):
                    return True
                continue  # wrong prefix; keep waiting

            return True  # no filter — any message on this ID counts

    def heartbeat(self) -> bool:
        """Send the standard heartbeat pair and wait for VCU ack."""
        self.send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        self.send_can(canid=0x001, data=[0x11, 0x01] + self.session_token + [0x01])
        try:
            self.VCU_response(0x002, data=[0x11, 0x01] + self.session_token, timeout=0.5)
            print("VCU is still alive")
            return True
        except VCUTimeoutError:
            print("VCU heartbeat timeout")
            return False

    def close(self) -> None:
        self.bus.shutdown()
