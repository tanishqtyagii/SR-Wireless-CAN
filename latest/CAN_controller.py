import can
import time
import errno
from intelhex import IntelHex
from typing import Iterable, List, Tuple, Optional
from dataclasses import dataclass
import time
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

        self.session_token = [0x81, 0x16, 0x92, 0xAE]
        self.key_0x17_1 = [0x17, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x17_2 = [0x17, 0x01, 0x78, 0x52, 0x25, 0x6C]
        self.key_0x19_1 = [0x19, 0x01, 0xF5, 0x69, 0x5A, 0x48]
        self.key_0x19_2 = [0x19, 0x01, 0xC9, 0x1E, 0x2E, 0xCE]

    def send_can(
        self,
        canid: int,
        data: list[int],
        delay: Optional[float] = 2, #ms
        *,
        tx_timeout: float = 0.02,
        backoff: float = 0.0002,
        max_retries: int = 5000,
    ):
        msg = can.Message(
            arbitration_id=canid,
            data=bytes(data),
            is_extended_id=False,
        )

        tries = 0
        while True:
            try:
                self.bus.send(msg, timeout=tx_timeout)
                break
            except can.CanError:
                tries += 1
                if tries >= max_retries:
                    raise
                time.sleep(backoff)
            except OSError as e:
                if e.errno in (errno.ENOBUFS, errno.EAGAIN, errno.EWOULDBLOCK):
                    tries += 1
                    if tries >= max_retries:
                        raise
                    time.sleep(backoff)
                else:
                    raise

        if delay is not None and delay > 0:
            time.sleep(delay / 1000.0)

    def VCU_response(
        self,
        canid: int,
        data: Optional[list[int]] = None,
        prefix: Optional[list[int]] = None,
        timeout: float = 100,         # ms
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

    def hex_length(self, ih: IntelHex):
        FLASH_BASE = 0xC10000
        HEADER_SIZE = 0x80
        APP_SIZE_OFF = 0x14  # little-endian body size in APDB header

        body_len = (
                ih[FLASH_BASE + APP_SIZE_OFF + 0]
                | (ih[FLASH_BASE + APP_SIZE_OFF + 1] << 8)
                | (ih[FLASH_BASE + APP_SIZE_OFF + 2] << 16)
                | (ih[FLASH_BASE + APP_SIZE_OFF + 3] << 24)
        )

        total_len = body_len + HEADER_SIZE

        span_len = ih.maxaddr() - FLASH_BASE + 1
        if total_len != span_len:
            raise ValueError(
                f"header-derived length 0x{total_len:X} != actual span 0x{span_len:X}"
            )

        return total_len

    def get_main_address(ih: IntelHex):
        """
        Return APDB header bytes 0x38..0x3B as a list, e.g.:
        [0xFA, 0x77, 0xC2, 0x00]

        Assumes the APDB header starts at absolute address 0xC10000.
        """
        base = 0xC10000
        offset = 0x38
        return [ih[base + offset + i] for i in range(4)]

    # HELPER FUNCTIONS THAT ARE NEEDED (AT MINIMUM) FOR HEADERS
    def magic_seed_checksum(data: bytes | bytearray) -> int:
        poly = 0x82608EDB
        result = 0xFADEEDDA
        top_bit_mask = 0x80000000  # highest set bit of 0x82608EDB

        words = []
        for i in range(0, len(data), 2):
            lo = data[i]
            hi = data[i + 1] if i + 1 < len(data) else 0
            words.append(((hi << 8) | lo) & 0xFFFF)

        for word in reversed(words):
            result ^= word
            parity = result & 1
            result = (result >> 1) & 0xFFFFFFFF

            mix = ((result >> 16) & (poly >> 16)) ^ ((result & 0xFFFF) & (poly & 0xFFFF))
            for i in range(16):
                parity ^= (mix >> i) & 1

            if parity:
                result ^= top_bit_mask

            result &= 0xFFFFFFFF

        return result

    def pack_time() -> int:
        """
        TTC tDate packing of the current local time:
        bits  0..11 = year
        bits 12..15 = month
        bits 16..20 = day
        bits 21..25 = hour
        bits 26..31 = minute
        """
        t = time.localtime()
        return (
                (t.tm_year & 0x0FFF)
                | ((t.tm_mon & 0x0F) << 12)
                | ((t.tm_mday & 0x1F) << 16)
                | ((t.tm_hour & 0x1F) << 21)
                | ((t.tm_min & 0x3F) << 26)
        ) & 0xFFFFFFFF

    def ttc_crc32(data: bytes, seed: int) -> int:
        crc = seed & 0xFFFFFFFF
        poly = 0xEDB88320

        for b in data:
            crc ^= b
            for _ in range(8):
                crc = ((crc >> 1) ^ poly) if (crc & 1) else (crc >> 1)
                crc &= 0xFFFFFFFF

        return crc

    def enc32(data: int) -> int:
        key = 0x6088569B
        poly = 0x04C11DB7

        data &= 0xFFFFFFFF
        out = 0
        reg = key

        for i in range(32):
            key_bit = reg & 1
            in_bit = (data >> i) & 1
            out_bit = key_bit ^ in_bit
            out |= out_bit << i
            reg >>= 1
            if out_bit:
                reg ^= poly

        return out & 0xFFFFFFFF

    def dec32(data: int) -> int:
        key = 0x6088569B
        poly = 0x04C11DB7

        data &= 0xFFFFFFFF
        out = 0
        reg = key

        for i in range(32):
            key_bit = reg & 1
            in_bit = (data >> i) & 1
            out_bit = key_bit ^ in_bit
            out |= out_bit << i
            reg >>= 1
            if in_bit:
                reg ^= poly

        return out & 0xFFFFFFFF

    def close(self) -> None:
        try:
            self.notifier.stop()
        finally:
            self.bus.shutdown()
