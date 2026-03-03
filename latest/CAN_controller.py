import can
import time
import errno
from intelhex import IntelHex
from typing import Iterable, List, Tuple, Optional
from dataclasses import dataclass

global VCU_state


@dataclass
class VCUTimeoutError(TimeoutError):
    canid: int
    timeout: float
    expected_data: Optional[bytes] = None
    expected_prefix: Optional[bytes] = None

    def __post_init__(self) -> None:
        if self.expected_data is not None and self.expected_prefix is not None:
            raise ValueError("Provide only one of expected_data or expected_prefix")

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
        self.reader = can.BufferedReader()
        self.notifier = can.Notifier(self.bus, [self.reader])

        # Optional: if you ONLY ever care about VCU->PC (0x002) during flashing,
        # kernel-level filters reduce Python load a bit.
        # Uncomment if you want it:
        # self.bus.set_filters([{"can_id": 0x002, "can_mask": 0x7FF, "extended": False}])

        self.session_token = [0x81, 0x16, 0x92, 0xAE]
        self.key_0x17_1 = [0x17, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x17_2 = [0x17, 0x01, 0x78, 0x52, 0x25, 0x6C]
        self.key_0x19_1 = [0x19, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x19_2 = [0x19, 0x01, 0xC9, 0x1E, 0x2E, 0xCE]

    def send_can(
        self,
        canid: int,
        data: list[int],
        delay: Optional[float] = 2,   # ms (keep your API)
        *,
        tx_timeout: float = 0.02,     # seconds to wait for kernel send
        backoff: float = 0.0002,      # seconds (200us) if tx buffer full
        max_retries: int = 5000,
    ):
        """
        Tight send:
          - no sleep unless delay > 0
          - retry on ENOBUFS / can.CanError with tiny backoff
        """
        msg = can.Message(
            arbitration_id=canid,
            data=bytes(data),          # bytes is slightly cheaper/cleaner
            is_extended_id=False,
        )

        tries = 0
        while True:
            try:
                # timeout blocks briefly if the TX queue is full (implementation-dependent),
                # and helps you avoid dropping frames when bursting.
                self.bus.send(msg, timeout=tx_timeout)
                break
            except can.CanError:
                tries += 1
                if tries >= max_retries:
                    raise
                time.sleep(backoff)
            except OSError as e:
                # SocketCAN "No buffer space available" is ENOBUFS (105)
                if e.errno in (errno.ENOBUFS, errno.EAGAIN, errno.EWOULDBLOCK):
                    tries += 1
                    if tries >= max_retries:
                        raise
                    time.sleep(backoff)
                else:
                    raise

        # IMPORTANT: don't yield/sleep unless you asked for it
        if delay is not None and delay > 0:
            time.sleep(delay / 1000.0)

    def VCU_response(
        self,
        canid: int,
        data: Optional[list[int]] = None,
        prefix: Optional[list[int]] = None,
        timeout: float = 100,         # ms (keep your API)
        *,
        debug: bool = False,
    ) -> bool:
        timeout_s = timeout / 1000.0

        if data is not None and prefix is not None:
            raise ValueError("only use data OR prefix, not both")

        target = None if data is None else bytes(data)
        target_prefix = None if prefix is None else bytes(prefix)

        end = time.monotonic() + timeout_s

        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise VCUTimeoutError(
                    canid=canid,
                    timeout=timeout_s,
                    expected_data=target,
                    expected_prefix=target_prefix,
                )

            msg = self.reader.get_message(timeout=remaining)
            if msg is None:
                continue

            if debug:
                print(msg)  # ONLY if you explicitly enable debug

            if msg.arbitration_id != canid:
                continue

            payload = bytes(msg.data)

            if target is not None:
                if payload == target:
                    return True
                continue

            if target_prefix is not None:
                if payload.startswith(target_prefix):
                    return True
                continue

            return True

    def heartbeat(self):
        self.send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00], delay=0)
        self.send_can(canid=0x001, data=[0x11, 0x01] + self.session_token + [0x01], delay=0)

        try:
            self.VCU_response(0x002, data=[0x11, 0x01] + self.session_token, timeout=500)
            print("VCU is still alive")
        except VCUTimeoutError:
            print("its dead")

    def close(self) -> None:
        try:
            self.notifier.stop()
        finally:
            self.bus.shutdown()