from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import db.schema as db
from backend.connections import ConnectionManager
from backend.firmware import FirmwareFlasher
from backend.settings import Settings
from backend.utils import crc32_hex, format_log_line, now_iso, sanitize_download_name, sha256_hex


@dataclass(slots=True)
class OperationRejected(Exception):
    code: str
    message: str
    status_code: int = 409
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "error": self.message}
        if self.details:
            payload.update(self.details)
        return payload


@dataclass(slots=True)
class SessionIdentity:
    id: str
    operator_name: str | None
    client_ip: str | None = None
    user_agent: str | None = None


@dataclass(slots=True)
class PreparedHex:
    record: dict[str, Any]
    path: Path
    display_name: str
    notes: str | None
    size: int
    crc32: str
    sha256: str


@dataclass(slots=True)
class OperationContext:
    history_id: str
    action: str
    session_id: str | None
    operator_name: str | None
    file_id: str | None
    file_name: str
    file_path: Path | None
    file_size: int | None
    file_crc32: str | None
    started_at_iso: str
    started_monotonic: float
    last_log_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class OperationManager:
    def __init__(self, settings: Settings, connections: ConnectionManager) -> None:
        self.settings = settings
        self.connections = connections
        self.firmware = FirmwareFlasher(settings)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.RLock()
        self._imd_event = threading.Event()

    # ── Event loop / broadcasting ────────────────────────────────────────────

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _schedule(self, coro: Any) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:
            return

    def _broadcast(
        self,
        *,
        event_type: str = "delta",
        state: dict[str, Any] | None = None,
        entry: dict[str, Any] | None = None,
        file_record: dict[str, Any] | None = None,
        log: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"type": event_type, "serverTime": now_iso()}
        if state is not None:
            payload["state"] = state
        if entry is not None:
            payload["historyEntry"] = entry
        if file_record is not None:
            payload["file"] = file_record
        if log is not None:
            payload["log"] = log
        if error is not None:
            payload["error"] = error
        if extra:
            payload.update(extra)
        if self._loop is None:
            return
        self._schedule(self.connections.broadcast(payload))

    def _send_to_session(self, session_id: str, payload: dict[str, Any]) -> None:
        if self._loop is None:
            return
        self._schedule(self.connections.send_to_session(session_id, payload))

    def build_snapshot(self, *, session_id: str | None = None) -> dict[str, Any]:
        db.clear_priority_if_expired()
        state = db.get_vcu_state_snapshot()
        history = db.list_flash_history(limit=120, include_logs=False)
        files = db.list_hex_files(limit=120)
        active_entry = db.get_flash_history_entry(state["activeHistoryId"], include_logs=False) if state.get("activeHistoryId") else None
        active_logs = db.get_flash_logs(state["activeHistoryId"]) if state.get("activeHistoryId") else {"logs": [], "status": None}
        return {
            "type": "snapshot",
            "serverTime": now_iso(),
            "state": state,
            "history": history,
            "files": files,
            "activeEntry": active_entry,
            "activeLogs": active_logs,
            "session": db.get_session(session_id) if session_id else None,
        }

    def broadcast_full_snapshot(self) -> None:
        if self._loop is None:
            return
        self._schedule(self.connections.broadcast(self.build_snapshot()))

    # ── Session helpers ───────────────────────────────────────────────────────

    def touch_session(
        self,
        session_id: str,
        *,
        operator_name: str | None,
        client_ip: str | None,
        user_agent: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return db.touch_session(
            session_id,
            operator_name=operator_name,
            client_ip=client_ip,
            user_agent=user_agent,
            metadata=metadata,
        )

    # ── File helpers ──────────────────────────────────────────────────────────

    def _validate_hex_payload(self, filename: str, data: bytes) -> None:
        if not filename.lower().endswith(".hex"):
            raise OperationRejected("INVALID_FILE_TYPE", "Only .hex files are allowed.", 400)
        if not data:
            raise OperationRejected("EMPTY_FILE", "Empty file payload.", 400)
        if len(data) > self.settings.max_hex_bytes:
            raise OperationRejected(
                "FILE_TOO_LARGE",
                f"File exceeds maximum allowed size of {self.settings.max_hex_bytes} bytes.",
                413,
            )

    def _store_hex_bytes(
        self,
        *,
        filename: str,
        data: bytes,
        display_name: str | None,
        notes: str | None,
        session: SessionIdentity,
    ) -> PreparedHex:
        self._validate_hex_payload(filename, data)
        safe_name = sanitize_download_name(filename)
        clean_display = (display_name or safe_name).strip() or safe_name
        clean_notes = notes.strip() if notes and notes.strip() else None
        crc32 = crc32_hex(data)
        sha256 = sha256_hex(data)
        record, _is_new = db.upsert_hex_file(
            name=safe_name,
            display_name=clean_display,
            size=len(data),
            notes=clean_notes,
            crc32=crc32,
            sha256=sha256,
            uploaded_by=session.operator_name,
            uploaded_by_session_id=session.id,
            metadata={"originalFilename": filename},
        )
        path = self.settings.upload_dir / f"{record['id']}.hex"
        path.write_bytes(data)
        db.bind_hex_file_storage(record["id"], path.name)
        refreshed = db.get_hex_file(record["id"]) or record
        self._broadcast(file_record=refreshed)
        return PreparedHex(
            record=refreshed,
            path=path,
            display_name=refreshed.get("displayName") or refreshed.get("name") or safe_name,
            notes=clean_notes or refreshed.get("notes"),
            size=len(data),
            crc32=crc32,
            sha256=sha256,
        )

    def upload_hex(
        self,
        session: SessionIdentity,
        *,
        filename: str,
        data: bytes,
        display_name: str | None,
        notes: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            prepared = self._store_hex_bytes(
                filename=filename,
                data=data,
                display_name=display_name,
                notes=notes,
                session=session,
            )
            return prepared.record

    def get_file_record(self, file_id: str) -> dict[str, Any] | None:
        return db.get_hex_file(file_id)

    def get_file_path(self, file_id: str) -> Path | None:
        record = db.get_hex_file(file_id)
        if not record:
            return None
        stored_name = record.get("storedName") or f"{file_id}.hex"
        path = self.settings.upload_dir / stored_name
        return path if path.is_file() else None

    def list_stored_names(self) -> list[str]:
        return [path.name for path in self.settings.upload_dir.glob("*.hex")]

    # ── Public read/update helpers ────────────────────────────────────────────

    def get_state_payload(self) -> dict[str, Any]:
        db.clear_priority_if_expired()
        return db.get_vcu_state_snapshot()

    def update_hex_notes(self, file_id: str, notes: str) -> dict[str, Any] | None:
        updated = db.update_hex_file_notes(file_id, notes)
        if updated:
            self._broadcast(file_record=updated)
        return updated

    def update_history_notes(self, entry_id: str, notes: str) -> dict[str, Any] | None:
        updated = db.update_flash_history_notes(entry_id, notes)
        if updated:
            file_record = db.get_hex_file(updated["fileId"]) if updated.get("fileId") else None
            self._broadcast(entry=updated, file_record=file_record)
        return updated

    def confirm_imd(self, *, session: SessionIdentity | None = None) -> dict[str, Any]:
        self._imd_event.set()
        state = db.get_vcu_state_snapshot()
        log_payload = None
        if state.get("activeHistoryId") and state.get("imdWaiting"):
            line = format_log_line("IMD confirmation received")
            line_no = db.append_flash_log(state["activeHistoryId"], line)
            log_payload = {"historyId": state["activeHistoryId"], "lineNo": line_no, "line": line}
        snapshot = db.update_vcu_state(imd_waiting=False)
        self._broadcast(
            event_type="imd.confirmed",
            state=snapshot,
            log=log_payload,
            extra={
                "confirmedBySessionId": session.id if session else None,
                "confirmedBy": session.operator_name if session else None,
            },
        )
        return {"ok": True}

    def prune_orphans(self) -> dict[str, Any]:
        result = db.prune_orphans(self.list_stored_names())
        self.broadcast_full_snapshot()
        return result

    def clear_all(self, *, clear_sessions: bool = False) -> None:
        db.clear_all(clear_sessions=clear_sessions)
        for path in self.settings.upload_dir.glob("*.hex"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._imd_event.clear()
        self.broadcast_full_snapshot()

    # ── Acceptance rules ──────────────────────────────────────────────────────

    def _grant_priority_unlocked(self, session_id: str | None, operator_name: str | None) -> dict[str, Any]:
        if not session_id:
            return db.get_vcu_state_snapshot()
        until = (datetime.now(timezone.utc) + timedelta(seconds=self.settings.session_priority_seconds)).isoformat()
        return db.update_vcu_state(
            priority_session_id=session_id,
            priority_operator_name=operator_name,
            priority_until=until,
        )

    def _enforce_priority_unlocked(self, session: SessionIdentity, *, allow_bootload_universal: bool) -> dict[str, Any]:
        db.clear_priority_if_expired()
        snapshot = db.get_vcu_state_snapshot()
        if allow_bootload_universal:
            return snapshot
        priority_session_id = snapshot.get("prioritySessionId")
        if priority_session_id and priority_session_id != session.id:
            holder = snapshot.get("priorityOperatorName") or "Another user"
            raise OperationRejected(
                "SESSION_PRIORITY_ACTIVE",
                f"{holder} has temporary reflash priority.",
                423,
                {
                    "prioritySessionId": priority_session_id,
                    "priorityOperatorName": snapshot.get("priorityOperatorName"),
                    "priorityUntil": snapshot.get("priorityUntil"),
                },
            )
        return snapshot

    def _reject_invalid_state(self, state: str, *, action: str) -> None:
        if action == "bootload":
            if state == "bootloaded":
                raise OperationRejected(
                    "ALREADY_BOOTLOADED",
                    "Bootload is not available because the VCU is already bootloaded.",
                    409,
                )
            if state == "flashing":
                raise OperationRejected(
                    "OPERATION_ALREADY_RUNNING",
                    "A flash operation is already running.",
                    409,
                )
            if state == "bootloading":
                raise OperationRejected(
                    "BOOTLOAD_IN_PROGRESS",
                    "The VCU is already bootloading.",
                    409,
                )
        if action == "flash_only" and state == "idle":
            raise OperationRejected(
                "VCU_NOT_BOOTLOADED",
                "Flash-only is only allowed after the VCU has been bootloaded.",
                409,
            )
        if state == "flashing":
            raise OperationRejected(
                "OPERATION_ALREADY_RUNNING",
                "Another flash operation is already running.",
                409,
            )
        if state == "bootloading":
            raise OperationRejected(
                "BOOTLOAD_IN_PROGRESS",
                "The VCU is currently bootloading.",
                409,
            )
        if state == "bootloaded":
            raise OperationRejected(
                "INVALID_VCU_STATE",
                "This operation is not allowed while the VCU is already bootloaded.",
                409,
            )
        raise OperationRejected(
            "INVALID_VCU_STATE",
            f"Operation is not allowed while the VCU is '{state}'.",
            409,
        )

    def _create_context_and_history(
        self,
        *,
        action: str,
        session: SessionIdentity,
        prepared_hex: PreparedHex | None,
        initial_state: str,
        initial_phase: str,
    ) -> OperationContext:
        started_iso = now_iso()
        name = prepared_hex.display_name if prepared_hex else "Bootload"
        history = db.add_flash_history(
            file_id=prepared_hex.record["id"] if prepared_hex else None,
            name=name,
            status="pending",
            action=action,
            notes=(prepared_hex.notes if prepared_hex else None),
            operator=session.operator_name,
            phase=initial_phase,
            progress_pct=0.0,
            session_id=session.id,
            file_size=prepared_hex.size if prepared_hex else None,
            file_crc32=prepared_hex.crc32 if prepared_hex else None,
            started_at=started_iso,
            requested_at=started_iso,
            metadata={
                "sha256": prepared_hex.sha256 if prepared_hex else None,
                "originalState": initial_state,
            },
        )
        snapshot = db.update_vcu_state(
            state=initial_state,
            phase=initial_phase,
            progress_pct=0.0,
            active_history_id=history["id"],
            power_cycle=False,
            imd_waiting=False,
            last_error=None,
            locked_by_session_id=session.id,
        )
        self._broadcast(state=snapshot, entry=history)
        return OperationContext(
            history_id=history["id"],
            action=action,
            session_id=session.id,
            operator_name=session.operator_name,
            file_id=prepared_hex.record["id"] if prepared_hex else None,
            file_name=name,
            file_path=prepared_hex.path if prepared_hex else None,
            file_size=prepared_hex.size if prepared_hex else None,
            file_crc32=prepared_hex.crc32 if prepared_hex else None,
            started_at_iso=started_iso,
            started_monotonic=time.monotonic(),
            metadata={"sha256": prepared_hex.sha256 if prepared_hex else None},
        )

    def start_bootload(self, session: SessionIdentity) -> dict[str, Any]:
        with self._lock:
            snapshot = self._enforce_priority_unlocked(session, allow_bootload_universal=True)
            state = snapshot.get("state") or "idle"
            if state != "idle":
                self._reject_invalid_state(state, action="bootload")
            ctx = self._create_context_and_history(
                action="bootload",
                session=session,
                prepared_hex=None,
                initial_state="bootloading",
                initial_phase="bootloading",
            )
            self._imd_event.clear()
            threading.Thread(target=self._run_worker, args=(ctx,), daemon=True, name=f"bootload-{ctx.history_id}").start()
            return {"ok": True, "historyId": ctx.history_id, "state": "bootloading"}

    def start_boot_and_flash(
        self,
        session: SessionIdentity,
        *,
        filename: str,
        data: bytes,
        display_name: str | None,
        notes: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            snapshot = self._enforce_priority_unlocked(session, allow_bootload_universal=False)
            state = snapshot.get("state") or "idle"
            if state != "idle":
                self._reject_invalid_state(state, action="boot_and_flash")
            prepared_hex = self._store_hex_bytes(
                filename=filename,
                data=data,
                display_name=display_name,
                notes=notes,
                session=session,
            )
            ctx = self._create_context_and_history(
                action="boot_and_flash",
                session=session,
                prepared_hex=prepared_hex,
                initial_state="flashing",
                initial_phase="bootloading",
            )
            self._imd_event.clear()
            threading.Thread(target=self._run_worker, args=(ctx,), daemon=True, name=f"bootflash-{ctx.history_id}").start()
            return {"ok": True, "historyId": ctx.history_id, "state": "flashing"}

    def start_flash_only(
        self,
        session: SessionIdentity,
        *,
        filename: str,
        data: bytes,
        display_name: str | None,
        notes: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            snapshot = self._enforce_priority_unlocked(session, allow_bootload_universal=False)
            state = snapshot.get("state") or "idle"
            if state != "bootloaded":
                self._reject_invalid_state(state, action="flash_only")
            prepared_hex = self._store_hex_bytes(
                filename=filename,
                data=data,
                display_name=display_name,
                notes=notes,
                session=session,
            )
            ctx = self._create_context_and_history(
                action="flash_only",
                session=session,
                prepared_hex=prepared_hex,
                initial_state="flashing",
                initial_phase="preparing_flash",
            )
            self._imd_event.clear()
            threading.Thread(target=self._run_worker, args=(ctx,), daemon=True, name=f"flash-{ctx.history_id}").start()
            return {"ok": True, "historyId": ctx.history_id, "state": "flashing"}

    # ── Progress mapping ──────────────────────────────────────────────────────

    def _stage_ranges(self, action: str) -> dict[str, tuple[float, float]]:
        if action == "bootload":
            stages = [("validation", 5.0), ("bootload", 95.0)]
        elif action == "boot_and_flash":
            stages = [
                ("validation", 5.0),
                ("bootload", 20.0),
                ("imd", 5.0 if self.settings.flash_require_imd_confirm else 0.0),
                ("flash_kernel", 10.0),
                ("erase", 10.0 if self.settings.flash_do_erase else 0.0),
                ("flash_hex", 40.0 if self.settings.flash_do_erase else 50.0),
                ("finalize", 10.0),
            ]
        else:
            stages = [
                ("validation", 5.0),
                ("flash_kernel", 15.0),
                ("erase", 10.0 if self.settings.flash_do_erase else 0.0),
                ("flash_hex", 60.0 if self.settings.flash_do_erase else 70.0),
                ("finalize", 10.0),
            ]
        total = sum(weight for _, weight in stages if weight > 0) or 100.0
        ranges: dict[str, tuple[float, float]] = {}
        start = 0.0
        for stage, weight in stages:
            if weight <= 0:
                continue
            width = 100.0 * (weight / total)
            ranges[stage] = (start, start + width)
            start += width
        return ranges

    def _overall_progress(self, ctx: OperationContext, stage: str, stage_progress: float | None) -> float | None:
        ranges = self._stage_ranges(ctx.action)
        if stage not in ranges:
            return stage_progress
        start, end = ranges[stage]
        if stage_progress is None:
            return start
        bounded = max(0.0, min(100.0, stage_progress))
        return round(start + ((end - start) * bounded / 100.0), 1)

    def _coarse_state_for_ctx(self, ctx: OperationContext) -> str:
        return "bootloading" if ctx.action == "bootload" else "flashing"

    def _handle_progress_event(self, ctx: OperationContext, event: dict[str, Any]) -> None:
        stage = str(event.get("stage") or event.get("phase") or "working")
        phase = str(event.get("phase") or stage)
        raw_progress = event.get("progress")
        try:
            stage_progress = float(raw_progress) if raw_progress is not None else None
        except Exception:
            stage_progress = None
        overall_progress = self._overall_progress(ctx, stage, stage_progress)
        power_cycle = bool(event.get("powerCycle", False))
        imd_waiting = bool(event.get("imdWaiting", False))
        message = str(event.get("message", "")).strip() or None

        log_payload = None
        with self._lock:
            db.update_flash_history_entry(
                ctx.history_id,
                phase=phase,
                progress_pct=overall_progress,
            )
            snapshot = db.update_vcu_state(
                state=self._coarse_state_for_ctx(ctx),
                phase=phase,
                progress_pct=overall_progress,
                active_history_id=ctx.history_id,
                power_cycle=power_cycle,
                imd_waiting=imd_waiting,
                last_error=None,
                locked_by_session_id=ctx.session_id,
            )
            entry = db.get_flash_history_entry(ctx.history_id, include_logs=False)
            if message and message != ctx.last_log_message:
                line = format_log_line(message)
                line_no = db.append_flash_log(ctx.history_id, line)
                ctx.last_log_message = message
                log_payload = {"historyId": ctx.history_id, "lineNo": line_no, "line": line}
        self._broadcast(state=snapshot, entry=entry, log=log_payload)

    def _wait_for_imd(self) -> bool:
        self._imd_event.clear()
        return self._imd_event.wait(timeout=self.settings.flash_imd_timeout_seconds)

    # ── Completion handling ───────────────────────────────────────────────────

    def _finish_success(self, ctx: OperationContext, result: dict[str, Any]) -> None:
        duration_ms = int((time.monotonic() - ctx.started_monotonic) * 1000)
        completed_at = now_iso()
        file_record = None
        with self._lock:
            entry = db.update_flash_history_entry(
                ctx.history_id,
                status="success",
                phase="bootloaded" if ctx.action == "bootload" else "complete",
                progress_pct=100.0,
                completed_at=completed_at,
                duration_ms=duration_ms,
                result=result,
                error=None,
            )
            line = format_log_line("Operation completed successfully")
            line_no = db.append_flash_log(ctx.history_id, line)
            log_payload = {"historyId": ctx.history_id, "lineNo": line_no, "line": line}
            if ctx.file_id:
                file_record = db.update_hex_file_after_flash(
                    ctx.file_id,
                    "success",
                    flashed_by=ctx.operator_name,
                    session_id=ctx.session_id,
                    history_id=ctx.history_id,
                )
            if ctx.action == "bootload":
                snapshot = db.update_vcu_state(
                    state="bootloaded",
                    phase="bootloaded",
                    progress_pct=100.0,
                    active_history_id=None,
                    power_cycle=False,
                    imd_waiting=False,
                    last_error=None,
                    locked_by_session_id=None,
                )
            else:
                self._grant_priority_unlocked(ctx.session_id, ctx.operator_name)
                snapshot = db.update_vcu_state(
                    state="idle",
                    phase="idle",
                    progress_pct=None,
                    active_history_id=None,
                    power_cycle=False,
                    imd_waiting=False,
                    last_error=None,
                    locked_by_session_id=None,
                )
        self._broadcast(state=snapshot, entry=entry, file_record=file_record, log=log_payload)

    def _finish_failure(self, ctx: OperationContext, error_message: str) -> None:
        duration_ms = int((time.monotonic() - ctx.started_monotonic) * 1000)
        completed_at = now_iso()
        error_message = error_message.strip() or "Unknown flash error"
        file_record = None
        with self._lock:
            line = format_log_line(f"Error: {error_message}")
            line_no = db.append_flash_log(ctx.history_id, line)
            log_payload = {"historyId": ctx.history_id, "lineNo": line_no, "line": line}
            entry = db.update_flash_history_entry(
                ctx.history_id,
                status="failed",
                phase="failed",
                completed_at=completed_at,
                duration_ms=duration_ms,
                error=error_message,
            )
            if ctx.file_id:
                file_record = db.update_hex_file_after_flash(
                    ctx.file_id,
                    "failed",
                    flashed_by=ctx.operator_name,
                    session_id=ctx.session_id,
                    history_id=ctx.history_id,
                )
                self._grant_priority_unlocked(ctx.session_id, ctx.operator_name)
            snapshot = db.update_vcu_state(
                state="idle",
                phase="idle",
                progress_pct=None,
                active_history_id=None,
                power_cycle=False,
                imd_waiting=False,
                last_error=error_message,
                locked_by_session_id=None,
            )
        self._broadcast(
            state=snapshot,
            entry=entry,
            file_record=file_record,
            log=log_payload,
            error={"code": "OPERATION_FAILED", "message": error_message},
        )

    # ── Worker ────────────────────────────────────────────────────────────────

    def _run_worker(self, ctx: OperationContext) -> None:
        try:
            if ctx.action == "bootload":
                result = self.firmware.run_bootload_only(on_event=lambda event: self._handle_progress_event(ctx, event))
            elif ctx.action == "boot_and_flash":
                if ctx.file_path is None:
                    raise RuntimeError("No firmware file available for boot-and-flash job.")
                result = self.firmware.run_boot_and_flash(
                    file_path=ctx.file_path,
                    on_event=lambda event: self._handle_progress_event(ctx, event),
                    wait_for_imd=self._wait_for_imd,
                )
            elif ctx.action == "flash_only":
                if ctx.file_path is None:
                    raise RuntimeError("No firmware file available for flash-only job.")
                result = self.firmware.run_flash_only(
                    file_path=ctx.file_path,
                    on_event=lambda event: self._handle_progress_event(ctx, event),
                )
            else:
                raise RuntimeError(f"Unsupported action '{ctx.action}'")
            self._finish_success(ctx, result)
        except Exception as exc:  # noqa: BLE001
            self._finish_failure(ctx, str(exc))
