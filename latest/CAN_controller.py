import can
import time
import sys
import types
from intelhex import IntelHex
from typing import Iterable, List, Tuple, Optional
from dataclasses import dataclass

global VCU_state

# Exceptions
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
        # "0001  05 01 40 00 AD 22 43 8E "
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
    # ideally these never change, but ya never know
    def __init__(self, interface: str="socketcan", channel: str="can0"):
        # should ideally never change
        self.interface = interface
        self.channel = channel

        # Create the bus & Buffered Reader
        self.bus = can.Bus(interface=interface, channel=channel)
        self.reader = can.BufferedReader()
        self.notifier = can.Notifier(self.bus, [self.reader])

        # Variables that are needed across
        self.session_token = [0x81, 0x16, 0x92, 0xAE]  # from 0x14 01 response; Constant
        # Derived from uj6; Since we're making uj6 constant, these all stay the same!!
        self.key_0x17_1 = [0x17, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x17_2 = [0x17, 0x01, 0x78, 0x52, 0x25, 0x6C]
        self.key_0x19_1 = [0x19, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x19_2 = [0x19, 0x01, 0xC9, 0x1E, 0x2E, 0xCE]

    # Sending messages via can
    def send_can(self, canid: int, data: list[int], delay: Optional[float] = 2):
        """
        Sends data (list) via canid (int), optional delay between messages (default 2ms)
        :param canid:
        :param data:
        :param delay:
        :return:
        """
        # id = can_id[canid] # ex. 0x001

        msg = can.Message(
            arbitration_id=canid,
            data=data,
            is_extended_id=False
            # DLC handled internally
        )
        self.bus.send(msg)
        time.sleep(delay / 1000)  # ms -> s (not a problem now since receivers on a diff thread)

    # Awaits response from VCU from specified ID, optional exact data
    def VCU_response(
            self,
            canid: int,
            data: Optional[list[int]] = None,
            prefix: Optional[list[int]] = None,
            timeout: float = 100,
    ) -> bool:
        timeout = timeout / 1000

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
                    timeout=timeout/1000,
                    expected_data=target,
                    expected_prefix=target_prefix,
                )

            msg = self.reader.get_message(timeout=remaining)
            print(msg)
            if msg is None:
                continue

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

    # Used throughout to see if VCU is alive
    def heartbeat(self):
        # Heartbeat check (ex: HEY IM STILL HERE)
        self.send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        self.send_can(canid=0x001, data=[0x11, 0x01] + self.session_token + [0x01])

        # Since we now have VCUTimeoutError
        try:
            self.VCU_response(0x002, data=[0x11, 0x01] + self.session_token, timeout=0.5)
            print("VCU is still alive")
        except VCUTimeoutError:
            print("its dead")

    # To close the bus and reader thread
    def close(self) -> None:
        try:
            self.notifier.stop()
        finally:
            self.bus.shutdown()

def hex_clear_span(hex_path: str, erase_block: int = 0x10000) -> tuple[int, int]:
    """
    Returns (erase_start_addr, length_to_clear) based on the HEX's highest/lowest used addresses.
    """
    ih = IntelHex(hex_path)

    min_addr = ih.minaddr()
    max_addr = ih.maxaddr()

    # Check for empty hex
    if min_addr is None or max_addr is None:
        raise ValueError("HEX has no data records")

    erase_start = min_addr & ~(erase_block - 1)
    length_to_clear = (max_addr - erase_start) + 1
    return erase_start, length_to_clear


def erase_plan_0x0C_frames(erase_start: int, length_to_clear: int,
                           chunk: int = 0x10000) -> list[list[int]]:
    """
    Builds 0x0C erase frames of the form:
      [0x0C, session, addr32_be(4 bytes), (len-1)_be(2 bytes)]
    Split into chunk-sized erases (default 0x10000), final chunk is remainder.
    """
    if length_to_clear <= 0:
        return []

    frames: list[list[int]] = []
    addr = erase_start
    remaining = length_to_clear

    while remaining > 0:
        this_len = min(remaining, chunk)
        len_m1 = this_len - 1  # protocol expects (len-1)

        frames.append([
            0x0C, 0x01,
            (addr >> 24) & 0xFF, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF,
            (len_m1 >> 8) & 0xFF, len_m1 & 0xFF,  # big-endian
        ])

        addr += this_len
        remaining -= this_len

    return frames