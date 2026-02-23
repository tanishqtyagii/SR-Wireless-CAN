from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import can  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - allows dry-run/trace tools without python-can
    can = None  # type: ignore


HOST_CAN_ID = 0x001
VCU_CAN_ID = 0x002
DEFAULT_NODE = 0x01

DEFAULT_RAM_BUFFER_ADDR = 0x00E08000
DEFAULT_RAM_BUFFER_SIZE = 0x8000
DEFAULT_LIFESIGN_PERIOD = 0x40
MAX_FLASH_WRITE_CHUNK = 0x10000

CRC32_TABLE_POLY = 0xEDB88320
STREAM_POLY = 0x04C11DB7
STREAM_KEY = 0x6088569B
MAGIC_POLY = 0x82608EDB
MAGIC_INIT = 0xFADEEDDA

HEADER_SIZE = 0x80


def _u32_le(data: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _put_u32_le(buf: bytearray, offset: int, value: int) -> None:
    buf[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")


def _u32_to_be_bytes(value: int) -> list[int]:
    value &= 0xFFFFFFFF
    return [
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ]


def pack_tdate(dt: datetime) -> int:
    value = 0
    value |= dt.year & 0xFFF
    value |= (dt.month << 12) & 0xF000
    value |= (dt.day << 16) & 0x1F0000
    value |= (dt.hour << 21) & 0x3E00000
    value |= (dt.minute << 26) & 0xFC000000
    return value & 0xFFFFFFFF


def unpack_tdate(value: int) -> tuple[int, int, int, int, int]:
    year = value & 0xFFF
    month = (value >> 12) & 0xF
    day = (value >> 16) & 0x1F
    hour = (value >> 21) & 0x1F
    minute = (value >> 26) & 0x3F
    return year, month, day, hour, minute


def _stream_transform(value: int, *, use_cipher_feedback: bool) -> int:
    state = STREAM_KEY & 0xFFFFFFFF
    out = 0
    for i in range(32):
        state_bit = state & 1
        input_bit = (value >> i) & 1
        output_bit = state_bit ^ input_bit
        out |= output_bit << i
        state >>= 1
        feedback_bit = output_bit if use_cipher_feedback else input_bit
        if feedback_bit:
            state ^= STREAM_POLY
    return out & 0xFFFFFFFF


def encode_crc_seed(raw_value: int) -> int:
    return _stream_transform(raw_value & 0xFFFFFFFF, use_cipher_feedback=True)


def decode_crc_seed(encoded_value: int) -> int:
    return _stream_transform(encoded_value & 0xFFFFFFFF, use_cipher_feedback=False)


def build_crc32_table() -> list[int]:
    table: list[int] = []
    for i in range(256):
        c = i
        for _ in range(8):
            if c & 1:
                c = (c >> 1) ^ CRC32_TABLE_POLY
            else:
                c >>= 1
        table.append(c & 0xFFFFFFFF)
    return table


CRC32_TABLE = build_crc32_table()


def ttc_crc32(data: bytes | bytearray, seed: int) -> int:
    crc = seed & 0xFFFFFFFF
    for b in data:
        crc = ((crc >> 8) ^ CRC32_TABLE[(crc ^ b) & 0xFF]) & 0xFFFFFFFF
    return crc


def sahj_magic_crc(data: bytes | bytearray, *, poly: int = MAGIC_POLY, seed: int = MAGIC_INIT) -> int:
    words: list[int] = [0] * ((len(data) // 2) if len(data) % 2 == 0 else (len(data) // 2 + 1))
    for i in range(len(words)):
        lo = data[i * 2]
        if i * 2 + 1 < len(data):
            hi = data[i * 2 + 1]
            words[i] = ((hi << 8) | lo) & 0xFFFF
        else:
            words[i] = lo

    result = seed & 0xFFFFFFFF
    highest_bit = 31
    while highest_bit >= 0 and poly <= (1 << highest_bit):
        highest_bit -= 1
    if highest_bit < 0:
        raise ValueError(f"Invalid polynomial: 0x{poly:08X}")

    for j in range(len(words), 0, -1):
        result ^= words[j - 1]
        parity = result & 1
        result >>= 1

        mix = ((result >> 16) & (poly >> 16)) ^ (result & 0xFFFF & (poly & 0xFFFF))
        for i in range(16):
            parity ^= (mix >> i) & 1

        if parity:
            result ^= 1 << highest_bit

    return result & 0xFFFFFFFF


def parse_intel_hex(path: str) -> tuple[int, bytearray]:
    upper = 0
    memory: dict[int, int] = {}
    min_addr: Optional[int] = None
    max_addr: Optional[int] = None

    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or not line.startswith(":"):
            continue

        ll = int(line[1:3], 16)
        addr16 = int(line[3:7], 16)
        rectype = int(line[7:9], 16)

        if rectype == 0x04:
            upper = int(line[9:13], 16)
            continue

        if rectype == 0x00 and ll:
            base = (upper << 16) | addr16
            data = bytes.fromhex(line[9 : 9 + ll * 2])
            for i, b in enumerate(data):
                memory[base + i] = b
            if min_addr is None or base < min_addr:
                min_addr = base
            end_addr = base + ll - 1
            if max_addr is None or end_addr > max_addr:
                max_addr = end_addr
            continue

        if rectype == 0x01:
            break

    if min_addr is None or max_addr is None:
        raise ValueError(f"No data records found in {path}")

    span = max_addr - min_addr + 1
    image = bytearray(span)
    for offset in range(span):
        image[offset] = memory.get(min_addr + offset, 0)
    return min_addr, image


@dataclass
class HeaderPatchResult:
    base_address: int
    app_start: int
    app_size: int
    tdate_raw: int
    tdate_tuple: tuple[int, int, int, int, int]
    crc_18: int
    crc_1c: int
    crc_24_enc: int
    crc_4c: int
    magic_60: int
    crc_7c: int
    crc_seed_raw: int


def patch_apdb_header(
    image: bytearray,
    *,
    base_address: int,
    crc_seed_raw: int,
    tdate_raw_override: Optional[int] = None,
) -> HeaderPatchResult:
    if len(image) < HEADER_SIZE:
        raise ValueError("Image is smaller than APDB header (0x80 bytes)")

    app_start = _u32_le(image, 0x10)
    app_size = _u32_le(image, 0x14)
    app_offset = app_start - base_address
    if app_offset < 0 or app_offset + app_size > len(image):
        raise ValueError(
            f"App range 0x{app_start:08X}+0x{app_size:X} is outside image base 0x{base_address:08X}/size 0x{len(image):X}"
        )

    app_data = image[app_offset : app_offset + app_size]
    crc_1c = ttc_crc32(app_data, crc_seed_raw)
    crc_18 = ttc_crc32(app_data, 0xFFFFFFFF)
    magic_60 = sahj_magic_crc(app_data, poly=MAGIC_POLY, seed=MAGIC_INIT)
    crc_24_enc = encode_crc_seed(crc_seed_raw)

    tdate_raw = tdate_raw_override if tdate_raw_override is not None else pack_tdate(datetime.now())
    _put_u32_le(image, 0x04, tdate_raw)
    _put_u32_le(image, 0x18, crc_18)
    _put_u32_le(image, 0x1C, crc_1c)
    _put_u32_le(image, 0x24, crc_24_enc)

    crc_4c = ttc_crc32(image[:0x4C], 0xFFFFFFFF)
    _put_u32_le(image, 0x4C, crc_4c)

    _put_u32_le(image, 0x60, magic_60)
    crc_7c = ttc_crc32(image[:0x7C], 0xFFFFFFFF)
    _put_u32_le(image, 0x7C, crc_7c)

    return HeaderPatchResult(
        base_address=base_address,
        app_start=app_start,
        app_size=app_size,
        tdate_raw=tdate_raw & 0xFFFFFFFF,
        tdate_tuple=unpack_tdate(tdate_raw & 0xFFFFFFFF),
        crc_18=crc_18,
        crc_1c=crc_1c,
        crc_24_enc=crc_24_enc,
        crc_4c=crc_4c,
        magic_60=magic_60,
        crc_7c=crc_7c,
        crc_seed_raw=crc_seed_raw & 0xFFFFFFFF,
    )


def send_can(bus: "can.Bus", canid: int, data: list[int], delay: float = 0.0) -> None:
    if can is None:
        raise RuntimeError("python-can is not installed. Install it to send CAN frames.")
    msg = can.Message(arbitration_id=canid, data=data, is_extended_id=False)
    bus.send(msg)
    if delay > 0:
        time.sleep(delay / 1000.0)


def vcu_response(
    bus: "can.Bus",
    canid: int,
    data: Optional[list[int]] = None,
    timeout: float = 0.3,
) -> Optional["can.Message"]:
    target = None if data is None else bytes(data)
    end = time.monotonic() + timeout
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return None
        msg = bus.recv(timeout=remaining)
        if msg is None:
            continue
        if msg.arbitration_id != canid:
            continue
        if target is not None and bytes(msg.data) != target:
            continue
        return msg


class ImageWriter:
    def __init__(
        self,
        *,
        bus: can.Bus,
        node_id: int,
        tx_delay_ms: float = 0.0,
    ) -> None:
        self.bus = bus
        self.node_id = node_id & 0xFF
        self.tx_delay_ms = tx_delay_ms

    def send_can(self, canid: int, data: list[int], delay: Optional[float] = None) -> None:
        sleep_ms = self.tx_delay_ms if delay is None else delay
        send_can(self.bus, canid, data, delay=sleep_ms)

    def vcu_response(self, canid: int, data: Optional[list[int]] = None, timeout: float = 0.3) -> Optional[can.Message]:
        return vcu_response(self.bus, canid, data=data, timeout=timeout)

    def _expect_ack(self, expected: list[int], timeout: float = 0.6) -> None:
        msg = self.vcu_response(VCU_CAN_ID, expected, timeout=timeout)
        if msg is None:
            raise RuntimeError(f"Missing ACK {expected}")

    def mem_set_address(self, address: int) -> None:
        self.send_can(HOST_CAN_ID, [0x0D, self.node_id] + _u32_to_be_bytes(address))
        self._expect_ack([0x0D, self.node_id])

    def _lifesign_check(self, expected_count: int) -> None:
        self.send_can(HOST_CAN_ID, [0x02, self.node_id], delay=0.0)
        msg = self.vcu_response(VCU_CAN_ID, timeout=1.0)
        if msg is None:
            raise RuntimeError("Missing LifeSign response")
        data = list(msg.data)
        if len(data) < 6 or data[0] != 0x02 or data[1] != self.node_id:
            raise RuntimeError(f"Malformed LifeSign response: {data}")
        count = (data[2] << 8) | data[3]
        if count != expected_count:
            raise RuntimeError(f"LifeSign byte-count mismatch: expected {expected_count}, got {count}")

    def mem_write_stream(self, payload: bytes | bytearray, *, lifesign_period: int) -> None:
        sent_since_lifesign = 0
        offset = 0
        while offset < len(payload):
            chunk = list(payload[offset : offset + 6])
            self.send_can(HOST_CAN_ID, [0x05, self.node_id] + chunk, delay=0.0)
            offset += len(chunk)
            sent_since_lifesign += len(chunk)
            if sent_since_lifesign >= lifesign_period or offset == len(payload):
                self._lifesign_check(sent_since_lifesign)
                sent_since_lifesign = 0

    def flash_write(self, source_ram_address: int, length: int) -> None:
        if length <= 0:
            raise ValueError("FlashWrite length must be > 0")
        size_minus_1 = (length - 1) & 0xFFFF
        payload = _u32_to_be_bytes(source_ram_address) + [(size_minus_1 >> 8) & 0xFF, size_minus_1 & 0xFF]
        self.send_can(HOST_CAN_ID, [0x0B, self.node_id] + payload)
        self._expect_ack([0x0B, self.node_id, 0x01], timeout=2.0)

    def set_crc_init_encrypted(self, encrypted_crc_seed: int) -> None:
        self.send_can(HOST_CAN_ID, [0x18, self.node_id] + _u32_to_be_bytes(encrypted_crc_seed))
        self._expect_ack([0x18, self.node_id])

    def mem_crc(self, length: int) -> int:
        self.send_can(HOST_CAN_ID, [0x10, self.node_id] + _u32_to_be_bytes(length))
        msg = self.vcu_response(VCU_CAN_ID, timeout=1.2)
        if msg is None:
            raise RuntimeError("Missing MemCRC response")
        data = list(msg.data)
        if len(data) != 6 or data[0] != 0x10 or data[1] != self.node_id:
            raise RuntimeError(f"Malformed MemCRC response: {data}")
        return ((data[2] << 24) | (data[3] << 16) | (data[4] << 8) | data[5]) & 0xFFFFFFFF


@dataclass
class UploadResult:
    block_count: int
    total_bytes: int
    blocks: list[dict[str, int]]
    crc_checks: dict[str, int | bool]
    header: HeaderPatchResult


def upload_image(
    *,
    hex_path: str,
    node_id: int = DEFAULT_NODE,
    interface: str = "socketcan",
    channel: str = "can0",
    tx_delay_ms: float = 0.0,
    ram_buffer_address: int = DEFAULT_RAM_BUFFER_ADDR,
    ram_buffer_size: int = DEFAULT_RAM_BUFFER_SIZE,
    lifesign_period: int = DEFAULT_LIFESIGN_PERIOD,
    crc_seed_raw: Optional[int] = None,
    crc_seed_encrypted: Optional[int] = None,
    tdate_raw_override: Optional[int] = None,
    redundant_crc_check: bool = True,
) -> UploadResult:
    if can is None:
        raise RuntimeError("python-can is not installed. Install it to send CAN frames.")

    base_addr, image = parse_intel_hex(hex_path)

    if crc_seed_raw is None:
        if crc_seed_encrypted is not None:
            crc_seed_raw = decode_crc_seed(crc_seed_encrypted)
        else:
            crc_seed_raw = decode_crc_seed(_u32_le(image, 0x24))

    header = patch_apdb_header(
        image,
        base_address=base_addr,
        crc_seed_raw=crc_seed_raw,
        tdate_raw_override=tdate_raw_override,
    )

    blocks: list[dict[str, int]] = []
    bus = can.Bus(interface=interface, channel=channel)
    writer = ImageWriter(bus=bus, node_id=node_id, tx_delay_ms=tx_delay_ms)

    try:
        offset = 0
        while offset < len(image):
            block = image[offset : offset + ram_buffer_size]
            flash_dest = base_addr + offset

            writer.mem_set_address(ram_buffer_address)
            writer.mem_write_stream(block, lifesign_period=lifesign_period)

            writer.mem_set_address(flash_dest)
            written = 0
            while written < len(block):
                chunk_len = min(MAX_FLASH_WRITE_CHUNK, len(block) - written)
                writer.flash_write(ram_buffer_address + written, chunk_len)
                written += chunk_len

            blocks.append(
                {
                    "flash_destination": flash_dest,
                    "ram_source": ram_buffer_address,
                    "length": len(block),
                }
            )
            offset += len(block)

        expected_1c = _u32_le(image, 0x1C)
        expected_7c = _u32_le(image, 0x7C)

        writer.set_crc_init_encrypted(_u32_le(image, 0x24))
        writer.mem_set_address(_u32_le(image, 0x10))
        crc_1c_actual = writer.mem_crc(_u32_le(image, 0x14))

        writer.set_crc_init_encrypted(0xB6E0C2EC)
        writer.mem_set_address(base_addr)
        crc_7c_actual = writer.mem_crc(0x7C)

        crc_1c_second = 0
        if redundant_crc_check:
            writer.set_crc_init_encrypted(_u32_le(image, 0x24))
            writer.mem_set_address(_u32_le(image, 0x10))
            crc_1c_second = writer.mem_crc(_u32_le(image, 0x14))

        crc_checks: dict[str, int | bool] = {
            "expected_1c": expected_1c,
            "actual_1c": crc_1c_actual,
            "match_1c": crc_1c_actual == expected_1c,
            "expected_7c": expected_7c,
            "actual_7c": crc_7c_actual,
            "match_7c": crc_7c_actual == expected_7c,
        }
        if redundant_crc_check:
            crc_checks["actual_1c_second"] = crc_1c_second
            crc_checks["match_1c_second"] = crc_1c_second == expected_1c

        return UploadResult(
            block_count=len(blocks),
            total_bytes=len(image),
            blocks=blocks,
            crc_checks=crc_checks,
            header=header,
        )
    finally:
        bus.shutdown()


TRACE_LINE_RE = re.compile(
    r"^\s*(\d+)\)\s+[\d.]+\s+Rx\s+([0-9A-Fa-f]{4})\s+(\d+)\s+((?:[0-9A-Fa-f]{2}\s*)+)$"
)


def _parse_trace_frames(trace_path: str) -> list[tuple[int, list[int]]]:
    frames: list[tuple[int, list[int]]] = []
    for line in Path(trace_path).read_text().splitlines():
        m = TRACE_LINE_RE.match(line)
        if not m:
            continue
        can_id = int(m.group(2), 16)
        data = [int(x, 16) for x in m.group(4).strip().split()]
        frames.append((can_id, data))
    return frames


def trace_format_check(trace_path: str) -> dict[str, int]:
    frames = _parse_trace_frames(trace_path)
    tx = [data for can_id, data in frames if can_id == HOST_CAN_ID]

    cmd_05 = 0
    cmd_02 = 0
    cmd_0b = 0
    cmd_0d = 0
    lifesign_bad = 0

    pending_bytes = 0
    for frame in tx:
        if not frame:
            continue
        cmd = frame[0]
        if cmd == 0x05 and len(frame) >= 3:
            cmd_05 += 1
            pending_bytes += len(frame) - 2
        elif frame[:2] == [0x02, 0x01]:
            cmd_02 += 1
            if pending_bytes not in (0x42, 0x20) and pending_bytes != 0:
                lifesign_bad += 1
            pending_bytes = 0
        elif frame[:2] == [0x0B, 0x01]:
            cmd_0b += 1
        elif frame[:2] == [0x0D, 0x01]:
            cmd_0d += 1

    return {
        "frames_05": cmd_05,
        "frames_02": cmd_02,
        "frames_0b": cmd_0b,
        "frames_0d": cmd_0d,
        "lifesign_groups_nonstandard": lifesign_bad,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch APDB header fields, stream image via 0x05, flash via 0x0B, then run final CRC checks."
    )
    parser.add_argument("hex_path", help="Path to Intel HEX file")
    parser.add_argument("--node", type=lambda x: int(x, 0), default=DEFAULT_NODE)
    parser.add_argument("--interface", default="socketcan")
    parser.add_argument("--channel", default="can0")
    parser.add_argument("--tx-delay-ms", type=float, default=0.0)
    parser.add_argument("--ram-addr", type=lambda x: int(x, 0), default=DEFAULT_RAM_BUFFER_ADDR)
    parser.add_argument("--ram-size", type=lambda x: int(x, 0), default=DEFAULT_RAM_BUFFER_SIZE)
    parser.add_argument("--lifesign", type=lambda x: int(x, 0), default=DEFAULT_LIFESIGN_PERIOD)
    parser.add_argument("--crc-seed-raw", type=lambda x: int(x, 0))
    parser.add_argument("--crc-seed-enc", type=lambda x: int(x, 0))
    parser.add_argument("--tdate-raw", type=lambda x: int(x, 0))
    parser.add_argument("--no-redundant-crc", action="store_true")
    parser.add_argument("--trace-check", help="Optional .trc file or folder to check frame format only")
    parser.add_argument("--dry-run", action="store_true", help="Patch and print values without sending CAN frames")
    args = parser.parse_args()

    if args.trace_check:
        trace_target = Path(args.trace_check)
        trace_files: list[Path]
        if trace_target.is_dir():
            trace_files = sorted(trace_target.glob("*.trc"))
        else:
            trace_files = [trace_target]
        print("Trace format checks:")
        for trace_file in trace_files:
            info = trace_format_check(str(trace_file))
            print(f"- {trace_file.name}: {info}")

    base_addr, image = parse_intel_hex(args.hex_path)
    if args.crc_seed_raw is None:
        if args.crc_seed_enc is not None:
            crc_seed_raw = decode_crc_seed(args.crc_seed_enc)
        else:
            crc_seed_raw = decode_crc_seed(_u32_le(image, 0x24))
    else:
        crc_seed_raw = args.crc_seed_raw

    header = patch_apdb_header(
        image,
        base_address=base_addr,
        crc_seed_raw=crc_seed_raw,
        tdate_raw_override=args.tdate_raw,
    )

    print("Patched header:")
    print(
        {
            "base_address": f"0x{header.base_address:08X}",
            "app_start": f"0x{header.app_start:08X}",
            "app_size": f"0x{header.app_size:08X}",
            "tdate_raw": f"0x{header.tdate_raw:08X}",
            "tdate_fields": header.tdate_tuple,
            "0xC10018": f"0x{header.crc_18:08X}",
            "0xC1001C": f"0x{header.crc_1c:08X}",
            "0xC10024": f"0x{header.crc_24_enc:08X}",
            "0xC1004C": f"0x{header.crc_4c:08X}",
            "0xC10060": f"0x{header.magic_60:08X}",
            "0xC1007C": f"0x{header.crc_7c:08X}",
            "crc_seed_raw": f"0x{header.crc_seed_raw:08X}",
        }
    )

    if args.dry_run:
        print("Dry run enabled: no CAN frames sent.")
        return

    result = upload_image(
        hex_path=args.hex_path,
        node_id=args.node,
        interface=args.interface,
        channel=args.channel,
        tx_delay_ms=args.tx_delay_ms,
        ram_buffer_address=args.ram_addr,
        ram_buffer_size=args.ram_size,
        lifesign_period=args.lifesign,
        crc_seed_raw=args.crc_seed_raw,
        crc_seed_encrypted=args.crc_seed_enc,
        tdate_raw_override=args.tdate_raw,
        redundant_crc_check=not args.no_redundant_crc,
    )
    print("Upload summary:")
    print(
        {
            "total_bytes": result.total_bytes,
            "block_count": result.block_count,
            "blocks": result.blocks,
            "crc_checks": result.crc_checks,
        }
    )


if __name__ == "__main__":
    main()
