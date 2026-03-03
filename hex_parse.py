from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Optional

HOST_CAN_ID = 0x001
VCU_CAN_ID = 0x002
DEFAULT_NODE = 0x01

DEFAULT_RAM_BUFFER_ADDR = 0x00E08000
DEFAULT_RAM_BUFFER_SIZE = 0x8000
DEFAULT_LIFESIGN_PERIOD = 0x40
MAX_FLASH_WRITE_CHUNK = 0x10000

CRC32_TABLE_POLY = 0xEDB88320
CRC_STREAM_POLY = 0x04C11DB7
CRC_STREAM_KEY = 0x6088569B
MAGIC_POLY = 0x82608EDB
MAGIC_INIT = 0xFADEEDDA

PASSWORD_STREAM_KEY = 1619547803
PASSWORD_STREAM_POLY = 79764919

HEADER_SIZE = 0x80
HEX_DIGITS = set("0123456789abcdefABCDEF")

TRACE_LINE_RE = re.compile(
    r"^\s*(\d+)\)\s+[\d.]+\s+Rx\s+([0-9A-Fa-f]{4})\s+(\d+)\s+((?:[0-9A-Fa-f]{2}\s*)+)$"
)


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


@dataclass
class BuildOutput:
    base_addr: int
    image: bytearray
    header: HeaderPatchResult
    prelude_frames: list[list[int]]
    erase_frames: list[list[int]]
    upload_frames: list[list[int]]
    verify_frames: list[list[int]]
    prelude_source: str

    @property
    def all_frames(self) -> list[list[int]]:
        return [*self.prelude_frames, *self.erase_frames, *self.upload_frames, *self.verify_frames]


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


def _u16_to_be_bytes(value: int) -> list[int]:
    value &= 0xFFFF
    return [(value >> 8) & 0xFF, value & 0xFF]


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


def _stream_transform(value: int, key: int, poly: int, *, use_cipher_feedback: bool) -> int:
    state = key & 0xFFFFFFFF
    out = 0
    for i in range(32):
        state_bit = state & 1
        input_bit = (value >> i) & 1
        output_bit = state_bit ^ input_bit
        out |= output_bit << i
        state >>= 1
        feedback_bit = output_bit if use_cipher_feedback else input_bit
        if feedback_bit:
            state ^= poly
    return out & 0xFFFFFFFF


def encode_crc_seed(raw_value: int) -> int:
    return _stream_transform(raw_value, CRC_STREAM_KEY, CRC_STREAM_POLY, use_cipher_feedback=True)


def decode_crc_seed(encoded_value: int) -> int:
    return _stream_transform(encoded_value, CRC_STREAM_KEY, CRC_STREAM_POLY, use_cipher_feedback=False)


def encode_password_token(raw_value: int) -> int:
    return _stream_transform(raw_value, PASSWORD_STREAM_KEY, PASSWORD_STREAM_POLY, use_cipher_feedback=True)


def decode_password_token(encoded_value: int) -> int:
    return _stream_transform(
        encoded_value,
        PASSWORD_STREAM_KEY,
        PASSWORD_STREAM_POLY,
        use_cipher_feedback=False,
    )


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
    for byte in data:
        crc = ((crc >> 8) ^ CRC32_TABLE[(crc ^ byte) & 0xFF]) & 0xFFFFFFFF
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


def _clean_hex_line(raw_line: str) -> str:
    # Accept normal Intel HEX and space/bracket-wrapped variants.
    return "".join(ch for ch in raw_line if ch in HEX_DIGITS)


def parse_intel_hex(path: str, *, fill_value: int = 0x00) -> tuple[int, bytearray]:
    upper = 0
    memory: dict[int, int] = {}
    min_addr: Optional[int] = None
    max_addr: Optional[int] = None

    for line_no, raw_line in enumerate(Path(path).read_text(errors="ignore").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped.startswith(":"):
            continue

        packed = _clean_hex_line(stripped[1:])
        if len(packed) < 8:
            continue

        try:
            ll = int(packed[0:2], 16)
            addr16 = int(packed[2:6], 16)
            rectype = int(packed[6:8], 16)
        except ValueError as exc:
            raise ValueError(f"Invalid HEX header at line {line_no} in {path}") from exc

        data_hex = packed[8 : 8 + ll * 2]
        if len(data_hex) < ll * 2:
            raise ValueError(
                f"Line {line_no} in {path}: byte count says {ll}, but data has only {len(data_hex) // 2} bytes"
            )

        if rectype == 0x04:
            if len(data_hex) < 4:
                raise ValueError(f"Line {line_no} in {path}: invalid extended linear address record")
            upper = int(data_hex[:4], 16)
            continue

        if rectype == 0x00 and ll:
            base = (upper << 16) | addr16
            payload = bytes.fromhex(data_hex)
            for i, byte in enumerate(payload):
                memory[base + i] = byte
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
    for i in range(span):
        image[i] = memory.get(min_addr + i, fill_value) & 0xFF
    return min_addr, image


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


def parse_trace_frames(trace_path: str) -> list[tuple[int, list[int]]]:
    frames: list[tuple[int, list[int]]] = []
    for line in Path(trace_path).read_text(errors="ignore").splitlines():
        match = TRACE_LINE_RE.match(line)
        if not match:
            continue
        can_id = int(match.group(2), 16)
        dlc = int(match.group(3))
        data = [int(x, 16) for x in match.group(4).strip().split()[:dlc]]
        frames.append((can_id, data))
    return frames


def host_frames(trace_path: str) -> list[list[int]]:
    return [data for can_id, data in parse_trace_frames(trace_path) if can_id == HOST_CAN_ID]


def extract_static_prelude_from_trace(trace_path: str, node_id: int = DEFAULT_NODE) -> list[list[int]]:
    host = host_frames(trace_path)
    start_pattern = [0x0D, node_id, 0x00, 0xE0, 0x00, 0x00]

    start_idx = -1
    for idx, frame in enumerate(host):
        if frame == start_pattern:
            start_idx = idx
            break
    if start_idx < 0:
        return []

    end_idx = -1
    for idx in range(start_idx + 1, len(host)):
        frame = host[idx]
        if len(frame) >= 2 and frame[0] == 0x0C and frame[1] == node_id:
            end_idx = idx
            break
    if end_idx < 0 or end_idx <= start_idx:
        return []

    return host[start_idx:end_idx]


def default_prelude_trace_for_profile(profile: str, *, search_root: Optional[Path] = None) -> Optional[Path]:
    root = search_root if search_root is not None else Path(__file__).resolve().parents[1]

    def first_existing(candidates: list[Path]) -> Optional[Path]:
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    if profile == "main":
        return first_existing(
            [
                root / "mainfulltrace_Friday_1.trc",
                root / "Code" / "traces" / "mainfulltrace_Friday_1.trc",
                root / "Dump" / "mainfulltrace_Friday_1.trc",
            ]
        )

    if profile == "eff":
        return first_existing(
            [
                root / "fullTraceEndtoEnd.trc",
                root / "full_endtoend_2_Friday_ 231_80kw.trc",
                root / "Code" / "traces" / "fullTraceEndtoEnd.trc",
                root / "Code" / "traces" / "full_endtoend_2_Friday.trc",
                root / "Dump" / "fullTraceEndtoEnd.trc",
                root / "Dump" / "full_endtoend_2_Friday.trc",
            ]
        )

    return None


def build_erase_frames(base_addr: int, total_size: int, node_id: int = DEFAULT_NODE) -> list[list[int]]:
    frames: list[list[int]] = []
    remaining = total_size
    addr = base_addr

    while remaining > 0:
        block = min(0x10000, remaining)
        frames.append([0x0C, node_id] + _u32_to_be_bytes(addr) + _u16_to_be_bytes(block - 1))
        addr += block
        remaining -= block

    return frames


def build_upload_and_commit_frames(
    image: bytes | bytearray,
    *,
    base_addr: int,
    node_id: int = DEFAULT_NODE,
    ram_buffer_addr: int = DEFAULT_RAM_BUFFER_ADDR,
    ram_buffer_size: int = DEFAULT_RAM_BUFFER_SIZE,
    lifesign_period: int = DEFAULT_LIFESIGN_PERIOD,
) -> list[list[int]]:
    frames: list[list[int]] = []
    offset = 0

    while offset < len(image):
        block = image[offset : offset + ram_buffer_size]

        frames.append([0x0D, node_id] + _u32_to_be_bytes(ram_buffer_addr))

        sent_since_lifesign = 0
        idx = 0
        while idx < len(block):
            chunk = list(block[idx : idx + 6])
            frames.append([0x05, node_id] + chunk)
            idx += len(chunk)
            sent_since_lifesign += len(chunk)
            if sent_since_lifesign >= lifesign_period or idx == len(block):
                frames.append([0x02, node_id])
                sent_since_lifesign = 0

        flash_dest = base_addr + offset
        frames.append([0x0D, node_id] + _u32_to_be_bytes(flash_dest))

        written = 0
        while written < len(block):
            chunk_len = min(MAX_FLASH_WRITE_CHUNK, len(block) - written)
            src_addr = ram_buffer_addr + written
            frames.append([0x0B, node_id] + _u32_to_be_bytes(src_addr) + _u16_to_be_bytes(chunk_len - 1))
            written += chunk_len

        offset += len(block)

    return frames


def build_verify_frames(
    image: bytes | bytearray,
    *,
    base_addr: int,
    node_id: int = DEFAULT_NODE,
    redundant_crc_check: bool = True,
) -> list[list[int]]:
    app_start = _u32_le(image, 0x10)
    app_size = _u32_le(image, 0x14)
    crc_seed_enc = _u32_le(image, 0x24)

    frames: list[list[int]] = [
        [0x18, node_id] + _u32_to_be_bytes(crc_seed_enc),
        [0x0D, node_id] + _u32_to_be_bytes(app_start),
        [0x10, node_id] + _u32_to_be_bytes(app_size),
        [0x04, node_id, 0x00, 0xC0, 0x7F, 0x00, 0x80],
        [0x04, node_id, 0x00, 0xC1, 0x00, 0x00, 0x80],
        [0x18, node_id] + _u32_to_be_bytes(0xB6E0C2EC),
        [0x0D, node_id] + _u32_to_be_bytes(base_addr),
        [0x10, node_id] + _u32_to_be_bytes(0x7C),
    ]

    if redundant_crc_check:
        frames.extend(
            [
                [0x18, node_id] + _u32_to_be_bytes(crc_seed_enc),
                [0x0D, node_id] + _u32_to_be_bytes(app_start),
                [0x10, node_id] + _u32_to_be_bytes(app_size),
                [0x04, node_id, 0x00, 0xC0, 0x7F, 0x80, 0x80],
            ]
        )

    return frames


def _heartbeat_pair_frames(
    *,
    node_id: int,
    session_token: list[int],
    password_key: int,
    final_select: int,
) -> list[list[int]]:
    return [
        [0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00],
        [0x11, node_id] + list(session_token) + [0x01],
        [0x19, node_id] + _u32_to_be_bytes(password_key),
        [0x11, node_id] + list(session_token) + [final_select & 0xFF],
    ]


def build_dynamic_prelude_frames(
    image: bytes | bytearray,
    *,
    base_addr: int,
    node_id: int = DEFAULT_NODE,
    session_token: Optional[list[int]] = None,
    maintenance_pairs: int = 1,
) -> list[list[int]]:
    token = list(session_token) if session_token is not None else [0x81, 0x16, 0x92, 0xAE]

    app_start = _u32_le(image, 0x10)
    app_size = _u32_le(image, 0x14)
    primary_key = _u32_le(image, 0x24)
    session_random = decode_password_token(primary_key)
    secondary_key = encode_password_token((123 + (session_random * 2)) & 0xFFFFFFFF)

    frames: list[list[int]] = [
        [0x18, node_id] + _u32_to_be_bytes(primary_key),
        [0x0D, node_id] + _u32_to_be_bytes(app_start),
        [0x10, node_id] + _u32_to_be_bytes(app_size),
        [0x04, node_id, 0x00, 0xC0, 0x7F, 0x00, 0x80],
        [0x04, node_id, 0x00, 0xC1, 0x00, 0x00, 0x80],
        [0x18, node_id] + _u32_to_be_bytes(0xB6E0C2EC),
        [0x0D, node_id] + _u32_to_be_bytes(base_addr),
        [0x10, node_id] + _u32_to_be_bytes(0x7C),
        [0x18, node_id] + _u32_to_be_bytes(primary_key),
        [0x0D, node_id] + _u32_to_be_bytes(app_start),
        [0x10, node_id] + _u32_to_be_bytes(app_size),
        [0x04, node_id, 0x00, 0xC0, 0x7F, 0x80, 0x80],
    ]

    for _ in range(max(0, maintenance_pairs)):
        frames.extend(
            _heartbeat_pair_frames(
                node_id=node_id,
                session_token=token,
                password_key=secondary_key,
                final_select=0x00,
            )
        )
        frames.extend(
            _heartbeat_pair_frames(
                node_id=node_id,
                session_token=token,
                password_key=primary_key,
                final_select=0x01,
            )
        )
        frames.extend(
            [
                [0x18, node_id] + _u32_to_be_bytes(primary_key),
                [0x0D, node_id] + _u32_to_be_bytes(app_start),
                [0x10, node_id] + _u32_to_be_bytes(app_size),
            ]
        )

    return frames


def build_frames_for_hex(
    hex_path: str,
    *,
    node_id: int = DEFAULT_NODE,
    profile: str = "main",
    prelude_trace: Optional[str] = None,
    ram_buffer_addr: int = DEFAULT_RAM_BUFFER_ADDR,
    ram_buffer_size: int = DEFAULT_RAM_BUFFER_SIZE,
    lifesign_period: int = DEFAULT_LIFESIGN_PERIOD,
    crc_seed_raw: Optional[int] = None,
    crc_seed_enc: Optional[int] = None,
    tdate_raw_override: Optional[int] = None,
    redundant_crc_check: bool = True,
    include_dynamic_prelude: bool = True,
    session_token: Optional[list[int]] = None,
) -> BuildOutput:
    base_addr, image = parse_intel_hex(hex_path)

    if crc_seed_raw is None:
        if crc_seed_enc is not None:
            crc_seed_raw = decode_crc_seed(crc_seed_enc)
        else:
            crc_seed_raw = decode_crc_seed(_u32_le(image, 0x24))

    header = patch_apdb_header(
        image=image,
        base_address=base_addr,
        crc_seed_raw=crc_seed_raw,
        tdate_raw_override=tdate_raw_override,
    )

    prelude_frames: list[list[int]] = []
    prelude_source = "none"

    trace_path: Optional[Path] = None
    if prelude_trace:
        candidate = Path(prelude_trace)
        if not candidate.exists() and not candidate.is_absolute():
            alt = Path(__file__).resolve().parents[1] / candidate
            if alt.exists():
                candidate = alt
        if candidate.exists():
            trace_path = candidate
    elif profile != "none":
        trace_path = default_prelude_trace_for_profile(profile)

    if trace_path and trace_path.exists():
        prelude_frames = extract_static_prelude_from_trace(str(trace_path), node_id=node_id)
        if prelude_frames:
            prelude_source = f"trace:{trace_path}"

    if not prelude_frames and include_dynamic_prelude:
        prelude_frames = build_dynamic_prelude_frames(
            image=image,
            base_addr=base_addr,
            node_id=node_id,
            session_token=session_token,
        )
        prelude_source = "dynamic"

    erase_frames = build_erase_frames(base_addr=base_addr, total_size=len(image), node_id=node_id)
    upload_frames = build_upload_and_commit_frames(
        image=image,
        base_addr=base_addr,
        node_id=node_id,
        ram_buffer_addr=ram_buffer_addr,
        ram_buffer_size=ram_buffer_size,
        lifesign_period=lifesign_period,
    )
    verify_frames = build_verify_frames(
        image=image,
        base_addr=base_addr,
        node_id=node_id,
        redundant_crc_check=redundant_crc_check,
    )

    return BuildOutput(
        base_addr=base_addr,
        image=image,
        header=header,
        prelude_frames=prelude_frames,
        erase_frames=erase_frames,
        upload_frames=upload_frames,
        verify_frames=verify_frames,
        prelude_source=prelude_source,
    )


def write_host_trace(frames: list[list[int]], out_path: str) -> None:
    lines: list[str] = []
    for idx, frame in enumerate(frames, start=1):
        dlc = len(frame)
        payload = " ".join(f"{b:02X}" for b in frame)
        lines.append(f"{idx:6d})  Tx 0001 {dlc:2d}  {payload}")
    Path(out_path).write_text("\n".join(lines) + "\n")
