from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Optional

try:
    import can  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    can = None  # type: ignore

from bootloader2 import bootload
from finalization import run_with_controller_functions

HOST_CAN_ID = 0x001
VCU_CAN_ID = 0x002
SESSION_TOKEN = [0x81, 0x16, 0x92, 0xAE]
session_token = SESSION_TOKEN  # backward-compatible alias

ROOT_DIR = Path(__file__).resolve().parents[1]

bus: Optional["can.Bus"] = None


def _require_can() -> None:
    if can is None:
        raise RuntimeError("python-can is required for CAN_controller live operations")


def init_bus(interface: str = "socketcan", channel: str = "can0") -> "can.Bus":
    _require_can()
    global bus
    if bus is None:
        bus = can.Bus(interface=interface, channel=channel)
    return bus


def close_bus() -> None:
    global bus
    if bus is None:
        return
    try:
        bus.shutdown()
    finally:
        bus = None


def _active_bus() -> "can.Bus":
    if bus is None:
        raise RuntimeError("CAN bus is not initialized. Call init_bus() first.")
    return bus


def send_can(canid: int, data: list[int], delay: Optional[float] = 0.0) -> None:
    msg = can.Message(arbitration_id=canid, data=data, is_extended_id=False)
    _active_bus().send(msg)
    if delay and delay > 0:
        time.sleep(delay / 1000.0)


def VCU_response(
    canid: int,
    data: Optional[list[int]] = None,
    *,
    prefix: Optional[list[int]] = None,
    timeout: float = 0.5,
) -> bool:
    if not hasattr(VCU_response, "seen"):
        VCU_response.seen = []  # type: ignore[attr-defined]

    target = None if data is None else bytes(data)
    prefix_bytes = None if prefix is None else bytes(prefix)
    deadline = time.monotonic() + timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        msg = _active_bus().recv(timeout=remaining)
        if msg is None:
            continue

        payload = bytes(msg.data)
        VCU_response.seen.append((msg.arbitration_id, payload))  # type: ignore[attr-defined]

        if msg.arbitration_id != canid:
            continue
        if target is not None and payload != target:
            continue
        if prefix_bytes is not None and not payload.startswith(prefix_bytes):
            continue
        return True


def heartbeat(token: Optional[list[int]] = None) -> None:
    active_token = list(token) if token is not None else list(SESSION_TOKEN)

    send_can(canid=HOST_CAN_ID, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=HOST_CAN_ID, data=[0x11, 0x01] + active_token + [0x01])

    if not VCU_response(canid=VCU_CAN_ID, data=[0x11, 0x01] + active_token, timeout=1.2):
        raise RuntimeError("VCU heartbeat failed")


def _resolve_path(path_value: str) -> Path:
    candidate = Path(path_value)
    if candidate.exists():
        return candidate
    if not candidate.is_absolute():
        alt = ROOT_DIR / candidate
        if alt.exists():
            return alt
    return candidate


def run_bootload_then_flash(
    hex_path: str,
    *,
    run_bootload: bool = True,
    profile: str = "main",
    prelude_trace: Optional[str] = None,
    node: int = 0x01,
    ram_addr: int = 0x00E08000,
    ram_size: int = 0x8000,
    lifesign: int = 0x40,
    crc_seed_raw: Optional[int] = None,
    crc_seed_enc: Optional[int] = None,
    tdate_raw: Optional[int] = None,
    redundant_crc_check: bool = True,
    include_dynamic_prelude: bool = True,
    strict_live: bool = False,
    tx_delay_ms: float = 0.0,
    heartbeat_before_send: bool = False,
    post_verify_maintenance_pairs: int = 0,
    maintenance_delay_s: float = 0.0,
    strict_identify_ack: bool = True,
) -> dict[str, Any]:
    active = _active_bus()

    resolved_hex = _resolve_path(hex_path)
    if not resolved_hex.exists():
        raise FileNotFoundError(f"HEX file not found: {resolved_hex}")

    resolved_prelude: Optional[str]
    if prelude_trace:
        p = _resolve_path(prelude_trace)
        resolved_prelude = str(p)
    else:
        resolved_prelude = None

    if hasattr(VCU_response, "seen"):
        VCU_response.seen.clear()  # type: ignore[attr-defined]

    if run_bootload:
        boot_result = bootload(
            bus=active,
            session_token=SESSION_TOKEN,
            send_can_fn=send_can,
            vcu_response_fn=VCU_response,
            strict_identify_ack=strict_identify_ack,
        )
    else:
        boot_result = {"status": "BOOTLOAD_SKIPPED"}

    build = run_with_controller_functions(
        hex_path=str(resolved_hex),
        send_can_func=send_can,
        vcu_response_func=VCU_response,
        heartbeat_func=lambda: heartbeat(token=SESSION_TOKEN),
        session_token=SESSION_TOKEN,
        profile=profile,
        prelude_trace=resolved_prelude,
        node=node,
        ram_addr=ram_addr,
        ram_size=ram_size,
        lifesign=lifesign,
        crc_seed_raw=crc_seed_raw,
        crc_seed_enc=crc_seed_enc,
        tdate_raw=tdate_raw,
        redundant_crc_check=redundant_crc_check,
        include_dynamic_prelude=include_dynamic_prelude,
        strict_live=strict_live,
        tx_delay_ms=tx_delay_ms,
        heartbeat_before_send=heartbeat_before_send,
        post_verify_maintenance_pairs=post_verify_maintenance_pairs,
        maintenance_delay_s=maintenance_delay_s,
    )

    return {
        "bootload": boot_result,
        "hex_path": str(resolved_hex),
        "base_addr": f"0x{build.base_addr:08X}",
        "image_size": len(build.image),
        "prelude_source": build.prelude_source,
        "prelude_frames": len(build.prelude_frames),
        "erase_frames": len(build.erase_frames),
        "upload_frames": len(build.upload_frames),
        "verify_frames": len(build.verify_frames),
        "header": {
            "crc_18": f"0x{build.header.crc_18:08X}",
            "crc_1c": f"0x{build.header.crc_1c:08X}",
            "crc_24_enc": f"0x{build.header.crc_24_enc:08X}",
            "crc_4c": f"0x{build.header.crc_4c:08X}",
            "crc_60": f"0x{build.header.magic_60:08X}",
            "crc_7c": f"0x{build.header.crc_7c:08X}",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootload + flash orchestrator for TTC over CAN.")
    parser.add_argument("hex_path", nargs="?", help="Path to Intel HEX file")
    parser.add_argument("--profile", choices=["main", "eff", "none"], default="main")
    parser.add_argument("--prelude-trace", help="Optional trace to extract static pre-erase prelude")
    parser.add_argument("--interface", default="socketcan")
    parser.add_argument("--channel", default="can0")
    parser.add_argument("--node", type=lambda x: int(x, 0), default=0x01)
    parser.add_argument("--ram-addr", type=lambda x: int(x, 0), default=0x00E08000)
    parser.add_argument("--ram-size", type=lambda x: int(x, 0), default=0x8000)
    parser.add_argument("--lifesign", type=lambda x: int(x, 0), default=0x40)
    parser.add_argument("--crc-seed-raw", type=lambda x: int(x, 0))
    parser.add_argument("--crc-seed-enc", type=lambda x: int(x, 0))
    parser.add_argument("--tdate-raw", type=lambda x: int(x, 0))
    parser.add_argument("--no-redundant-crc", action="store_true")
    parser.add_argument("--no-dynamic-prelude", action="store_true")
    parser.add_argument("--strict-live", action="store_true")
    parser.add_argument("--tx-delay-ms", type=float, default=0.0)
    parser.add_argument("--heartbeat-before-send", action="store_true")
    parser.add_argument("--maintenance-pairs", type=int, default=0)
    parser.add_argument("--maintenance-delay-s", type=float, default=0.0)
    parser.add_argument("--boot-only", action="store_true")
    parser.add_argument("--flash-only", action="store_true")
    parser.add_argument("--allow-missing-identify-ack", action="store_true")
    args = parser.parse_args()

    if not args.boot_only and not args.hex_path:
        parser.error("hex_path is required unless --boot-only is used")

    init_bus(interface=args.interface, channel=args.channel)

    try:
        if args.boot_only:
            result = bootload(
                bus=_active_bus(),
                session_token=SESSION_TOKEN,
                send_can_fn=send_can,
                vcu_response_fn=VCU_response,
                strict_identify_ack=not args.allow_missing_identify_ack,
            )
            print(result)
            return

        summary = run_bootload_then_flash(
            args.hex_path,
            run_bootload=not args.flash_only,
            profile=args.profile,
            prelude_trace=args.prelude_trace,
            node=args.node,
            ram_addr=args.ram_addr,
            ram_size=args.ram_size,
            lifesign=args.lifesign,
            crc_seed_raw=args.crc_seed_raw,
            crc_seed_enc=args.crc_seed_enc,
            tdate_raw=args.tdate_raw,
            redundant_crc_check=not args.no_redundant_crc,
            include_dynamic_prelude=not args.no_dynamic_prelude,
            strict_live=args.strict_live,
            tx_delay_ms=args.tx_delay_ms,
            heartbeat_before_send=args.heartbeat_before_send,
            post_verify_maintenance_pairs=max(0, args.maintenance_pairs),
            maintenance_delay_s=max(0.0, args.maintenance_delay_s),
            strict_identify_ack=not args.allow_missing_identify_ack,
        )
        print("run summary:")
        print(summary)
    finally:
        close_bus()


if __name__ == "__main__":
    main()
