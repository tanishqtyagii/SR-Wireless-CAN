from __future__ import annotations

import argparse
import time
from typing import Any, Callable, Optional

try:
    import can  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    can = None  # type: ignore

from hex_parse import BuildOutput, build_frames_for_hex, decode_password_token, encode_password_token

HOST_CAN_ID = 0x001
VCU_CAN_ID = 0x002
DEFAULT_NODE = 0x01
DEFAULT_SESSION_TOKEN = [0x81, 0x16, 0x92, 0xAE]


def _u32_to_be_bytes(value: int) -> list[int]:
    value &= 0xFFFFFFFF
    return [
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ]


class _FlowTransport:
    @property
    def session_token(self) -> list[int]:
        return list(DEFAULT_SESSION_TOKEN)

    def close(self) -> None:
        return

    def send_can(self, frame: list[int]) -> None:
        raise NotImplementedError

    def heartbeat(self) -> None:
        return

    def expect_exact(self, expected: list[int], timeout: float = 0.8) -> bool:
        raise NotImplementedError

    def read_any(self, timeout: float = 0.8) -> Optional[list[int]]:
        raise NotImplementedError

    def _last_seen_data(self) -> Optional[list[int]]:
        return None

    def _consume_read_stream(self, request_frame: list[int], timeout: float = 4.0) -> bool:
        if len(request_frame) < 2:
            return False

        node = request_frame[1]
        req = tuple(request_frame)
        expected_end = {
            (0x04, node, 0x00, 0xC0, 0x7F, 0x00, 0x80): [0x04, node, 0x00, 0x00],
            (0x04, node, 0x00, 0xC1, 0x00, 0x00, 0x80): [0x04, node, 0x74, 0x80],
            (0x04, node, 0x00, 0xC0, 0x7F, 0x80, 0x80): [0x04, node, 0x00, 0x00],
        }.get(req)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            msg = self.read_any(timeout=remaining)
            if msg is None:
                return False
            if len(msg) < 2 or msg[0] != 0x04 or msg[1] != node:
                continue

            if expected_end is not None:
                if msg == expected_end:
                    return True
                continue

            # Fallback if stream shape is unknown: treat short 0x04 as stream end.
            if len(msg) <= 4:
                return True

    def _expect_for_command(self, frame: list[int]) -> bool:
        if len(frame) < 2:
            return True

        cmd = frame[0]
        node = frame[1]

        if cmd == 0x05:
            return True
        if cmd == 0x0D:
            return self.expect_exact([0x0D, node], timeout=2.0)
        if cmd == 0x0E:
            return self.expect_exact([0x0E, node], timeout=2.0)
        if cmd == 0x0C:
            return self.expect_exact([0x0C, node, 0x01], timeout=45.0)
        if cmd == 0x0B:
            return self.expect_exact([0x0B, node, 0x01], timeout=95.0)
        if cmd == 0x18:
            return self.expect_exact([0x18, node], timeout=2.0)
        if cmd == 0x19:
            return self.expect_exact([0x19, node, 0x01], timeout=2.0)
        if cmd == 0x11 and frame[1] != 0xFF and len(frame) >= 7:
            expected = [0x11, node] + self.session_token
            return self.expect_exact(expected, timeout=2.0)
        if cmd == 0x14 and len(frame) == 2:
            resp = self.read_any(timeout=2.0)
            if not resp or len(resp) < 2 + len(self.session_token) or resp[0] != 0x14:
                return False
            return resp[-len(self.session_token) :] == self.session_token
        if cmd == 0x17:
            resp = self.read_any(timeout=2.0)
            return bool(resp and len(resp) >= 2 and resp[0] == 0x17 and resp[1] == node)
        if cmd == 0x02:
            resp = self.read_any(timeout=2.0)
            return bool(resp and len(resp) >= 2 and resp[0] == 0x02 and resp[1] == node)
        if cmd == 0x10:
            resp = self.read_any(timeout=2.0)
            return bool(resp and len(resp) == 6 and resp[0] == 0x10 and resp[1] == node)
        if cmd == 0x04:
            return self._consume_read_stream(frame, timeout=5.0)

        return True

    def replay_frames(self, frames: list[list[int]], *, strict: bool, label: str) -> None:
        sent_since_lifesign = 0

        for idx, frame in enumerate(frames, start=1):
            self.send_can(frame)
            cmd = frame[0] if frame else -1

            if cmd == 0x05 and len(frame) >= 2:
                sent_since_lifesign += len(frame) - 2

            ok = self._expect_for_command(frame)
            if not ok:
                msg = f"{label}: response mismatch for frame #{idx}: {frame}"
                if strict:
                    raise RuntimeError(msg)
                print(f"WARN {msg}")

            if cmd == 0x02:
                last = self._last_seen_data()
                if last and len(last) >= 4 and last[0] == 0x02 and last[1] == frame[1]:
                    count = (last[2] << 8) | last[3]
                    if count != sent_since_lifesign:
                        text = (
                            f"{label}: LifeSign byte count mismatch "
                            f"expected={sent_since_lifesign} got={count}"
                        )
                        if strict:
                            raise RuntimeError(text)
                        print(f"WARN {text}")
                sent_since_lifesign = 0

    def run_verify_phase(
        self,
        verify_frames: list[list[int]],
        *,
        expected_1c: int,
        expected_7c: int,
        strict: bool,
    ) -> None:
        crc_reads: list[int] = []

        for frame in verify_frames:
            self.send_can(frame)
            cmd = frame[0]

            if cmd == 0x10:
                resp = self.read_any(timeout=2.5)
                if not resp or len(resp) != 6 or resp[0] != 0x10 or resp[1] != frame[1]:
                    msg = "verify: missing or malformed 0x10 MemCRC response"
                    if strict:
                        raise RuntimeError(msg)
                    print(f"WARN {msg}")
                    continue
                crc = ((resp[2] << 24) | (resp[3] << 16) | (resp[4] << 8) | resp[5]) & 0xFFFFFFFF
                crc_reads.append(crc)
                continue

            if cmd == 0x04:
                ok = self._consume_read_stream(frame, timeout=5.0)
            else:
                ok = self._expect_for_command(frame)

            if not ok:
                msg = f"verify: response mismatch for frame {frame}"
                if strict:
                    raise RuntimeError(msg)
                print(f"WARN {msg}")

        checks = [
            (0, expected_1c, "app CRC"),
            (1, expected_7c, "header CRC"),
            (2, expected_1c, "redundant app CRC"),
        ]

        for idx, expected, name in checks:
            if idx >= len(crc_reads):
                continue
            got = crc_reads[idx]
            if got != expected:
                msg = f"verify: {name} mismatch expected=0x{expected:08X} got=0x{got:08X}"
                if strict:
                    raise RuntimeError(msg)
                print(f"WARN {msg}")


class CallbackCAN(_FlowTransport):
    def __init__(
        self,
        *,
        send_can_func: Callable[..., Any],
        vcu_response_func: Callable[..., Any],
        heartbeat_func: Optional[Callable[[], Any]] = None,
        tx_delay_ms: float = 0.0,
        session_token: Optional[list[int]] = None,
    ) -> None:
        self._send_can_func = send_can_func
        self._vcu_response_func = vcu_response_func
        self._heartbeat_func = heartbeat_func
        self.tx_delay_ms = tx_delay_ms
        self._session_token = list(session_token) if session_token is not None else list(DEFAULT_SESSION_TOKEN)

    @property
    def session_token(self) -> list[int]:
        return list(self._session_token)

    def send_can(self, frame: list[int]) -> None:
        self._send_can_func(canid=HOST_CAN_ID, data=frame, delay=self.tx_delay_ms)

    def heartbeat(self) -> None:
        if self._heartbeat_func is None:
            return
        self._heartbeat_func()

    def expect_exact(self, expected: list[int], timeout: float = 0.8) -> bool:
        return bool(self._vcu_response_func(canid=VCU_CAN_ID, data=expected, timeout=timeout))

    def read_any(self, timeout: float = 0.8) -> Optional[list[int]]:
        seen_before = len(getattr(self._vcu_response_func, "seen", []))
        ok = bool(self._vcu_response_func(canid=VCU_CAN_ID, data=None, timeout=timeout))
        if not ok:
            return None
        seen = getattr(self._vcu_response_func, "seen", [])
        if not seen or len(seen) <= seen_before:
            return None
        return list(seen[-1][1])

    def _last_seen_data(self) -> Optional[list[int]]:
        seen = getattr(self._vcu_response_func, "seen", [])
        if not seen:
            return None
        return list(seen[-1][1])


class LiveCAN(_FlowTransport):
    def __init__(
        self,
        *,
        bus: "can.Bus",
        tx_delay_ms: float = 0.0,
        session_token: Optional[list[int]] = None,
    ) -> None:
        if can is None:
            raise RuntimeError("python-can is required for live CAN transport")
        self.bus = bus
        self.tx_delay_ms = tx_delay_ms
        self._session_token = list(session_token) if session_token is not None else list(DEFAULT_SESSION_TOKEN)
        self._seen: list[list[int]] = []

    @property
    def session_token(self) -> list[int]:
        return list(self._session_token)

    def send_can(self, frame: list[int]) -> None:
        msg = can.Message(arbitration_id=HOST_CAN_ID, data=frame, is_extended_id=False)
        self.bus.send(msg)
        if self.tx_delay_ms > 0:
            time.sleep(self.tx_delay_ms / 1000.0)

    def expect_exact(self, expected: list[int], timeout: float = 0.8) -> bool:
        target = bytes(expected)
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            msg = self.bus.recv(timeout=remaining)
            if msg is None or msg.arbitration_id != VCU_CAN_ID:
                continue
            data = list(msg.data)
            self._seen.append(data)
            if bytes(data) == target:
                return True

    def read_any(self, timeout: float = 0.8) -> Optional[list[int]]:
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            msg = self.bus.recv(timeout=remaining)
            if msg is None or msg.arbitration_id != VCU_CAN_ID:
                continue
            data = list(msg.data)
            self._seen.append(data)
            return data

    def _last_seen_data(self) -> Optional[list[int]]:
        if not self._seen:
            return None
        return list(self._seen[-1])


def _maintenance_cycle_frames(
    *,
    node_id: int,
    session_token: list[int],
    primary_key: int,
    secondary_key: int,
    app_start: int,
    app_size: int,
) -> list[list[int]]:
    return [
        [0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00],
        [0x11, node_id] + list(session_token) + [0x01],
        [0x19, node_id] + _u32_to_be_bytes(secondary_key),
        [0x11, node_id] + list(session_token) + [0x00],
        [0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00],
        [0x11, node_id] + list(session_token) + [0x01],
        [0x19, node_id] + _u32_to_be_bytes(primary_key),
        [0x11, node_id] + list(session_token) + [0x01],
        [0x18, node_id] + _u32_to_be_bytes(primary_key),
        [0x0D, node_id] + _u32_to_be_bytes(app_start),
        [0x10, node_id] + _u32_to_be_bytes(app_size),
    ]


def execute_live_build(
    build: BuildOutput,
    *,
    transport: _FlowTransport,
    strict_live: bool = False,
    heartbeat_before_send: bool = False,
    post_verify_maintenance_pairs: int = 0,
    maintenance_delay_s: float = 0.0,
) -> None:
    if heartbeat_before_send:
        transport.heartbeat()

    if build.prelude_frames:
        print(f"Replaying prelude frames: {len(build.prelude_frames)} ({build.prelude_source})")
        transport.replay_frames(build.prelude_frames, strict=strict_live, label="prelude")

    print(f"Sending erase frames: {len(build.erase_frames)}")
    transport.replay_frames(build.erase_frames, strict=strict_live, label="erase")

    print(f"Sending upload+commit frames: {len(build.upload_frames)}")
    transport.replay_frames(build.upload_frames, strict=strict_live, label="upload")

    print(f"Sending verify frames: {len(build.verify_frames)}")
    transport.run_verify_phase(
        build.verify_frames,
        expected_1c=build.header.crc_1c,
        expected_7c=build.header.crc_7c,
        strict=strict_live,
    )

    if post_verify_maintenance_pairs > 0:
        node_id = build.verify_frames[0][1] if build.verify_frames and len(build.verify_frames[0]) >= 2 else DEFAULT_NODE
        primary_key = build.header.crc_24_enc
        session_random = decode_password_token(primary_key)
        secondary_key = encode_password_token((123 + session_random * 2) & 0xFFFFFFFF)

        frames = _maintenance_cycle_frames(
            node_id=node_id,
            session_token=transport.session_token,
            primary_key=primary_key,
            secondary_key=secondary_key,
            app_start=build.header.app_start,
            app_size=build.header.app_size,
        )

        for idx in range(post_verify_maintenance_pairs):
            if maintenance_delay_s > 0 and idx > 0:
                time.sleep(maintenance_delay_s)
            transport.replay_frames(frames, strict=strict_live, label=f"maintenance[{idx + 1}]")

    print("Flash flow complete.")


def run_with_controller_functions(
    *,
    hex_path: str,
    send_can_func: Callable[..., Any],
    vcu_response_func: Callable[..., Any],
    heartbeat_func: Optional[Callable[[], Any]] = None,
    session_token: Optional[list[int]] = None,
    profile: str = "main",
    prelude_trace: Optional[str] = None,
    node: int = DEFAULT_NODE,
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
) -> BuildOutput:
    prelude_trace_arg = None if profile == "none" else prelude_trace

    build = build_frames_for_hex(
        hex_path=hex_path,
        node_id=node,
        profile=profile,
        prelude_trace=prelude_trace_arg,
        ram_buffer_addr=ram_addr,
        ram_buffer_size=ram_size,
        lifesign_period=lifesign,
        crc_seed_raw=crc_seed_raw,
        crc_seed_enc=crc_seed_enc,
        tdate_raw_override=tdate_raw,
        redundant_crc_check=redundant_crc_check,
        include_dynamic_prelude=include_dynamic_prelude,
        session_token=session_token,
    )

    transport = CallbackCAN(
        send_can_func=send_can_func,
        vcu_response_func=vcu_response_func,
        heartbeat_func=heartbeat_func,
        tx_delay_ms=tx_delay_ms,
        session_token=session_token,
    )

    execute_live_build(
        build,
        transport=transport,
        strict_live=strict_live,
        heartbeat_before_send=heartbeat_before_send,
        post_verify_maintenance_pairs=post_verify_maintenance_pairs,
        maintenance_delay_s=maintenance_delay_s,
    )

    return build


def main() -> None:
    if can is None:
        raise RuntimeError("python-can is required to run finalization.py live")

    parser = argparse.ArgumentParser(description="Run TTC flash/verify flow on a live CAN bus.")
    parser.add_argument("hex_path", help="Path to Intel HEX file")
    parser.add_argument("--profile", choices=["main", "eff", "none"], default="main")
    parser.add_argument("--prelude-trace")
    parser.add_argument("--interface", default="socketcan")
    parser.add_argument("--channel", default="can0")
    parser.add_argument("--node", type=lambda x: int(x, 0), default=DEFAULT_NODE)
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
    args = parser.parse_args()

    bus = can.Bus(interface=args.interface, channel=args.channel)
    transport = LiveCAN(bus=bus, tx_delay_ms=args.tx_delay_ms)

    try:
        build = build_frames_for_hex(
            hex_path=args.hex_path,
            node_id=args.node,
            profile=args.profile,
            prelude_trace=(None if args.profile == "none" else args.prelude_trace),
            ram_buffer_addr=args.ram_addr,
            ram_buffer_size=args.ram_size,
            lifesign_period=args.lifesign,
            crc_seed_raw=args.crc_seed_raw,
            crc_seed_enc=args.crc_seed_enc,
            tdate_raw_override=args.tdate_raw,
            redundant_crc_check=not args.no_redundant_crc,
            include_dynamic_prelude=not args.no_dynamic_prelude,
            session_token=transport.session_token,
        )

        print(
            {
                "base_addr": f"0x{build.base_addr:08X}",
                "image_size": len(build.image),
                "prelude": len(build.prelude_frames),
                "erase": len(build.erase_frames),
                "upload": len(build.upload_frames),
                "verify": len(build.verify_frames),
                "prelude_source": build.prelude_source,
            }
        )

        execute_live_build(
            build,
            transport=transport,
            strict_live=args.strict_live,
            heartbeat_before_send=args.heartbeat_before_send,
            post_verify_maintenance_pairs=max(0, args.maintenance_pairs),
            maintenance_delay_s=max(0.0, args.maintenance_delay_s),
        )
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
