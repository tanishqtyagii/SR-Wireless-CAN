from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    root_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    host: str = field(default_factory=lambda: os.getenv("VCU_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("VCU_PORT", "8000")))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv(
                "ALLOWED_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173",
            ).split(",")
            if origin.strip()
        ]
    )
    session_cookie_name: str = field(default_factory=lambda: os.getenv("SESSION_COOKIE_NAME", "vcu_session"))
    session_priority_seconds: int = field(default_factory=lambda: int(os.getenv("SESSION_PRIORITY_SECONDS", "5")))
    max_hex_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_HEX_BYTES", str(8 * 1024 * 1024))))

    flash_simulate: bool = field(default_factory=lambda: _env_flag("FLASH_SIMULATE", True))
    flash_can_interface: str = field(default_factory=lambda: os.getenv("FLASH_CAN_INTERFACE", "socketcan"))
    flash_can_channel: str = field(default_factory=lambda: os.getenv("FLASH_CAN_CHANNEL", "can0"))
    flash_do_erase: bool = field(default_factory=lambda: _env_flag("FLASH_DO_ERASE", True))
    flash_require_imd_confirm: bool = field(default_factory=lambda: _env_flag("FLASH_REQUIRE_IMD_CONFIRM", True))
    flash_imd_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("FLASH_IMD_TIMEOUT_SECONDS", "300")))

    @property
    def db_dir(self) -> Path:
        return self.root_dir / "db"

    @property
    def upload_dir(self) -> Path:
        return self.db_dir / "uploads"

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
