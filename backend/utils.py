from __future__ import annotations

import binascii
import hashlib
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def crc32_hex(data: bytes) -> str:
    return f"{binascii.crc32(data) & 0xFFFFFFFF:08X}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize_download_name(name: str) -> str:
    base = os.path.basename(name or "firmware.hex")
    base = base.replace("\\", "_").replace("/", "_").strip()
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base)
    return base or "firmware.hex"


def format_log_line(message: str) -> str:
    return f"[{now_iso()}] {message.strip()}"


def safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe_json(v) for v in value]
    return str(value)


_INTELHEX_IMPORT_ERROR: Exception | None = None
try:
    from intelhex import IntelHex  # type: ignore
except Exception as exc:  # pragma: no cover - depends on host env
    IntelHex = None  # type: ignore[assignment]
    _INTELHEX_IMPORT_ERROR = exc


def require_intelhex() -> Any:
    if IntelHex is None:
        raise RuntimeError(
            "intelhex is not installed. Install dependencies from requirements.txt, or enable FLASH_SIMULATE=1."
        ) from _INTELHEX_IMPORT_ERROR
    return IntelHex


def load_hex_lenient(path: str | Path) -> Any:
    IntelHexCls = require_intelhex()
    path = Path(path)
    try:
        return IntelHexCls(str(path))
    except Exception:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            lines = []
            for raw_line in handle.read().splitlines():
                raw = raw_line.strip()
                if raw.startswith(":"):
                    lines.append("".join(raw.split()))
        if not lines:
            raise
        ih = IntelHexCls()
        ih.loadhex(io.StringIO("\n".join(lines) + "\n"))
        return ih
