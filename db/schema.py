from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(os.getenv("VCU_DB_PATH", Path(__file__).with_name("vcu.db")))

_CREATE_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    operator_name TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    client_ip TEXT,
    user_agent TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS hex_files (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_name TEXT,
    stored_name TEXT,
    size INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    uploaded_by TEXT,
    uploaded_by_session_id TEXT,
    last_flashed_at TEXT,
    last_flashed_by TEXT,
    last_flash_session_id TEXT,
    last_history_id TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    flash_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT,
    crc32 TEXT,
    sha256 TEXT,
    metadata_json TEXT,
    FOREIGN KEY (uploaded_by_session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS flash_history (
    id TEXT PRIMARY KEY,
    file_id TEXT,
    name TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL,
    action TEXT NOT NULL,
    phase TEXT,
    progress_pct REAL,
    notes TEXT,
    error TEXT,
    duration_ms INTEGER,
    operator TEXT,
    session_id TEXT,
    file_size INTEGER,
    file_crc32 TEXT,
    result_json TEXT,
    metadata_json TEXT,
    FOREIGN KEY (file_id) REFERENCES hex_files(id) ON DELETE SET NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS flash_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    line_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (history_id) REFERENCES flash_history(id) ON DELETE CASCADE,
    UNIQUE (history_id, line_no)
);

CREATE TABLE IF NOT EXISTS vcu_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL DEFAULT 'idle',
    phase TEXT,
    progress_pct REAL,
    active_history_id TEXT,
    power_cycle INTEGER NOT NULL DEFAULT 0,
    imd_waiting INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    locked_by_session_id TEXT,
    priority_session_id TEXT,
    priority_operator_name TEXT,
    priority_until TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (active_history_id) REFERENCES flash_history(id) ON DELETE SET NULL,
    FOREIGN KEY (locked_by_session_id) REFERENCES sessions(id) ON DELETE SET NULL,
    FOREIGN KEY (priority_session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

INSERT OR IGNORE INTO vcu_state (id, state, updated_at) VALUES (1, 'idle', CURRENT_TIMESTAMP);

CREATE INDEX IF NOT EXISTS idx_hex_files_uploaded_at ON hex_files(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_hex_files_crc32 ON hex_files(crc32);
CREATE INDEX IF NOT EXISTS idx_flash_history_requested_at ON flash_history(requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_flash_history_file_id ON flash_history(file_id);
CREATE INDEX IF NOT EXISTS idx_flash_logs_history_line ON flash_logs(history_id, line_no);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def transaction() -> Iterable[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn: sqlite3.Connection) -> None:
    # Add missing columns for iterative development / older DBs.
    sessions_cols = _table_columns(conn, "sessions")
    if "metadata_json" not in sessions_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN metadata_json TEXT")

    hex_cols = _table_columns(conn, "hex_files")
    additions = {
        "display_name": "TEXT",
        "stored_name": "TEXT",
        "uploaded_by": "TEXT",
        "uploaded_by_session_id": "TEXT",
        "last_flashed_by": "TEXT",
        "last_flash_session_id": "TEXT",
        "last_history_id": "TEXT",
        "last_success_at": "TEXT",
        "last_failure_at": "TEXT",
        "flash_count": "INTEGER NOT NULL DEFAULT 0",
        "crc32": "TEXT",
        "sha256": "TEXT",
        "metadata_json": "TEXT",
    }
    for column, decl in additions.items():
        if column not in hex_cols:
            conn.execute(f"ALTER TABLE hex_files ADD COLUMN {column} {decl}")

    history_cols = _table_columns(conn, "flash_history")
    history_additions = {
        "requested_at": "TEXT",
        "started_at": "TEXT",
        "completed_at": "TEXT",
        "phase": "TEXT",
        "progress_pct": "REAL",
        "operator": "TEXT",
        "session_id": "TEXT",
        "file_size": "INTEGER",
        "file_crc32": "TEXT",
        "result_json": "TEXT",
        "metadata_json": "TEXT",
    }
    for column, decl in history_additions.items():
        if column not in history_cols:
            conn.execute(f"ALTER TABLE flash_history ADD COLUMN {column} {decl}")

    vcu_cols = _table_columns(conn, "vcu_state")
    vcu_additions = {
        "phase": "TEXT",
        "progress_pct": "REAL",
        "active_history_id": "TEXT",
        "power_cycle": "INTEGER NOT NULL DEFAULT 0",
        "imd_waiting": "INTEGER NOT NULL DEFAULT 0",
        "last_error": "TEXT",
        "locked_by_session_id": "TEXT",
        "priority_session_id": "TEXT",
        "priority_operator_name": "TEXT",
        "priority_until": "TEXT",
        "updated_at": "TEXT",
    }
    for column, decl in vcu_additions.items():
        if column not in vcu_cols:
            conn.execute(f"ALTER TABLE vcu_state ADD COLUMN {column} {decl}")
    conn.execute("UPDATE vcu_state SET updated_at = COALESCE(updated_at, ?)", (_now(),))

    # Create flash_logs if older DB did not have it.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS flash_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            history_id TEXT NOT NULL,
            line_no INTEGER NOT NULL,
            line_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (history_id) REFERENCES flash_history(id) ON DELETE CASCADE,
            UNIQUE (history_id, line_no)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flash_logs_history_line ON flash_logs(history_id, line_no)")

    # Backfill requested_at from older timestamp column if needed.
    if "timestamp" in history_cols:
        conn.execute("UPDATE flash_history SET requested_at = COALESCE(requested_at, timestamp)")
    conn.execute("UPDATE flash_history SET requested_at = COALESCE(requested_at, ?)", (_now(),))


# ── Row adapters ─────────────────────────────────────────────────────────────


def _session_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "operatorName": row["operator_name"],
        "createdAt": row["created_at"],
        "lastSeenAt": row["last_seen_at"],
        "clientIp": row["client_ip"],
        "userAgent": row["user_agent"],
        "metadata": _parse_json(row["metadata_json"]),
    }


def _hex_file_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "displayName": row["display_name"],
        "storedName": row["stored_name"],
        "size": row["size"],
        "uploadedAt": row["uploaded_at"],
        "uploadedBy": row["uploaded_by"],
        "uploadedBySessionId": row["uploaded_by_session_id"],
        "lastFlashedAt": row["last_flashed_at"],
        "lastFlashedBy": row["last_flashed_by"],
        "lastFlashSessionId": row["last_flash_session_id"],
        "lastHistoryId": row["last_history_id"],
        "lastSuccessAt": row["last_success_at"],
        "lastFailureAt": row["last_failure_at"],
        "flashCount": row["flash_count"],
        "status": row["status"],
        "notes": row["notes"],
        "crc32": row["crc32"],
        "sha256": row["sha256"],
        "metadata": _parse_json(row["metadata_json"]),
    }


def _history_row(row: sqlite3.Row | None, *, include_logs: bool = False) -> dict[str, Any] | None:
    if row is None:
        return None
    payload: dict[str, Any] = {
        "id": row["id"],
        "fileId": row["file_id"],
        "name": row["name"],
        "timestamp": row["requested_at"],
        "requestedAt": row["requested_at"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "status": row["status"],
        "action": row["action"],
        "phase": row["phase"],
        "progressPct": row["progress_pct"],
        "notes": row["notes"],
        "error": row["error"],
        "durationMs": row["duration_ms"],
        "operator": row["operator"],
        "sessionId": row["session_id"],
        "fileSize": row["file_size"],
        "fileCrc32": row["file_crc32"],
        "result": _parse_json(row["result_json"]),
        "metadata": _parse_json(row["metadata_json"]),
    }
    if include_logs:
        payload["logs"] = get_flash_logs(row["id"])["logs"]
    return payload


def _vcu_row(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "state": "idle",
            "phase": None,
            "progressPct": None,
            "activeHistoryId": None,
            "powerCycle": False,
            "imdWaiting": False,
            "lastError": None,
            "lockedBySessionId": None,
            "prioritySessionId": None,
            "priorityOperatorName": None,
            "priorityUntil": None,
            "updatedAt": _now(),
        }
    return {
        "state": row["state"],
        "phase": row["phase"],
        "progressPct": row["progress_pct"],
        "activeHistoryId": row["active_history_id"],
        "powerCycle": bool(row["power_cycle"]),
        "imdWaiting": bool(row["imd_waiting"]),
        "lastError": row["last_error"],
        "lockedBySessionId": row["locked_by_session_id"],
        "prioritySessionId": row["priority_session_id"],
        "priorityOperatorName": row["priority_operator_name"],
        "priorityUntil": row["priority_until"],
        "updatedAt": row["updated_at"],
    }


# ── Initialization / maintenance ─────────────────────────────────────────────


def init_db() -> None:
    with transaction() as conn:
        conn.executescript(_CREATE_SQL)
        _migrate(conn)
        # If the process restarts mid-operation, mark the job as failed and reset state.
        restarted_at = _now()
        conn.execute(
            """
            UPDATE flash_history
            SET status = CASE WHEN status = 'pending' THEN 'failed' ELSE status END,
                completed_at = COALESCE(completed_at, ?),
                error = CASE
                    WHEN status = 'pending' AND (error IS NULL OR error = '') THEN 'Backend restarted while operation was running.'
                    ELSE error
                END
            WHERE status = 'pending'
            """,
            (restarted_at,),
        )
        conn.execute(
            """
            UPDATE vcu_state
            SET state = 'idle',
                phase = 'idle',
                progress_pct = NULL,
                active_history_id = NULL,
                power_cycle = 0,
                imd_waiting = 0,
                locked_by_session_id = NULL,
                updated_at = ?
            WHERE id = 1
            """,
            (restarted_at,),
        )
        clear_priority_if_expired(conn=conn)


# ── Sessions ─────────────────────────────────────────────────────────────────


def touch_session(
    session_id: str,
    *,
    operator_name: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()
    with transaction() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO sessions (id, operator_name, created_at, last_seen_at, client_ip, user_agent, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, operator_name, now, now, client_ip, user_agent, _json(metadata)),
            )
        else:
            next_operator = operator_name.strip() if isinstance(operator_name, str) and operator_name.strip() else row["operator_name"]
            next_client_ip = client_ip or row["client_ip"]
            next_user_agent = user_agent or row["user_agent"]
            next_metadata = metadata if metadata is not None else _parse_json(row["metadata_json"])
            conn.execute(
                """
                UPDATE sessions
                SET operator_name = ?,
                    last_seen_at = ?,
                    client_ip = ?,
                    user_agent = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (next_operator, now, next_client_ip, next_user_agent, _json(next_metadata), session_id),
            )
        refreshed = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _session_row(refreshed)  # type: ignore[return-value]


def get_session(session_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _session_row(row)


def create_session_id() -> str:
    return _new_id("sess")


# ── VCU state ────────────────────────────────────────────────────────────────


def get_vcu_state_snapshot() -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM vcu_state WHERE id = 1").fetchone()
        return _vcu_row(row)


def get_vcu_state() -> str:
    return get_vcu_state_snapshot()["state"]


def update_vcu_state(**fields: Any) -> dict[str, Any]:
    allowed = {
        "state": "state",
        "phase": "phase",
        "progress_pct": "progress_pct",
        "active_history_id": "active_history_id",
        "power_cycle": "power_cycle",
        "imd_waiting": "imd_waiting",
        "last_error": "last_error",
        "locked_by_session_id": "locked_by_session_id",
        "priority_session_id": "priority_session_id",
        "priority_operator_name": "priority_operator_name",
        "priority_until": "priority_until",
    }
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key in allowed:
            col = allowed[key]
            if col in {"power_cycle", "imd_waiting"} and value is not None:
                updates[col] = 1 if value else 0
            else:
                updates[col] = value
    updates["updated_at"] = _now()
    if not updates:
        return get_vcu_state_snapshot()
    set_clause = ", ".join(f"{column} = ?" for column in updates)
    values = list(updates.values()) + [1]
    with transaction() as conn:
        conn.execute(f"UPDATE vcu_state SET {set_clause} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM vcu_state WHERE id = 1").fetchone()
        return _vcu_row(row)


def set_vcu_state(state: str) -> dict[str, Any]:
    return update_vcu_state(state=state)


def clear_priority_if_expired(*, conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute("SELECT priority_until FROM vcu_state WHERE id = 1").fetchone()
        priority_until = row["priority_until"] if row else None
        if priority_until:
            try:
                expiry = datetime.fromisoformat(priority_until.replace("Z", "+00:00"))
            except Exception:
                expiry = None
            if expiry is None or expiry <= datetime.now(timezone.utc):
                conn.execute(
                    """
                    UPDATE vcu_state
                    SET priority_session_id = NULL,
                        priority_operator_name = NULL,
                        priority_until = NULL,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (_now(),),
                )
                if owns_conn:
                    conn.commit()
    finally:
        if owns_conn:
            conn.close()


# ── Hex files ────────────────────────────────────────────────────────────────


def list_hex_files(limit: int = 250) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM hex_files ORDER BY uploaded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_hex_file_row(row) for row in rows if row is not None]  # type: ignore[list-item]


def get_hex_file(file_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM hex_files WHERE id = ?", (file_id,)).fetchone()
        return _hex_file_row(row)


def upsert_hex_file(
    *,
    name: str,
    display_name: str | None,
    size: int,
    notes: str | None,
    crc32: str | None = None,
    sha256: str | None = None,
    uploaded_by: str | None = None,
    uploaded_by_session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    stored_name: str | None = None,
) -> tuple[dict[str, Any], bool]:
    now = _now()
    lookup_name = (display_name or name).strip() or name
    with transaction() as conn:
        existing: sqlite3.Row | None = None
        if crc32:
            existing = conn.execute(
                "SELECT * FROM hex_files WHERE crc32 = ? AND size = ? LIMIT 1",
                (crc32, size),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT * FROM hex_files WHERE COALESCE(display_name, name) = ? AND size = ? LIMIT 1",
                (lookup_name, size),
            ).fetchone()

        if existing is not None:
            merged_notes = notes if notes is not None else existing["notes"]
            merged_metadata = metadata if metadata is not None else _parse_json(existing["metadata_json"])
            conn.execute(
                """
                UPDATE hex_files
                SET name = ?,
                    display_name = ?,
                    notes = ?,
                    crc32 = COALESCE(?, crc32),
                    sha256 = COALESCE(?, sha256),
                    uploaded_by = COALESCE(?, uploaded_by),
                    uploaded_by_session_id = COALESCE(?, uploaded_by_session_id),
                    metadata_json = ?,
                    stored_name = COALESCE(?, stored_name)
                WHERE id = ?
                """,
                (
                    name,
                    display_name,
                    merged_notes,
                    crc32,
                    sha256,
                    uploaded_by,
                    uploaded_by_session_id,
                    _json(merged_metadata),
                    stored_name,
                    existing["id"],
                ),
            )
            row = conn.execute("SELECT * FROM hex_files WHERE id = ?", (existing["id"],)).fetchone()
            return _hex_file_row(row), False  # type: ignore[return-value]

        file_id = _new_id("hf")
        conn.execute(
            """
            INSERT INTO hex_files (
                id, name, display_name, stored_name, size, uploaded_at,
                uploaded_by, uploaded_by_session_id, status, notes, crc32, sha256, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                file_id,
                name,
                display_name,
                stored_name,
                size,
                now,
                uploaded_by,
                uploaded_by_session_id,
                notes,
                crc32,
                sha256,
                _json(metadata),
            ),
        )
        row = conn.execute("SELECT * FROM hex_files WHERE id = ?", (file_id,)).fetchone()
        return _hex_file_row(row), True  # type: ignore[return-value]


def bind_hex_file_storage(file_id: str, stored_name: str) -> dict[str, Any] | None:
    with transaction() as conn:
        conn.execute(
            "UPDATE hex_files SET stored_name = ? WHERE id = ?",
            (stored_name, file_id),
        )
        row = conn.execute("SELECT * FROM hex_files WHERE id = ?", (file_id,)).fetchone()
        return _hex_file_row(row)


def update_hex_file_after_flash(
    file_id: str,
    status: str,
    *,
    flashed_by: str | None = None,
    session_id: str | None = None,
    history_id: str | None = None,
) -> dict[str, Any] | None:
    now = _now()
    with transaction() as conn:
        row = conn.execute("SELECT * FROM hex_files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            return None
        flash_count = int(row["flash_count"] or 0) + 1
        success_at = now if status == "success" else row["last_success_at"]
        failure_at = now if status == "failed" else row["last_failure_at"]
        conn.execute(
            """
            UPDATE hex_files
            SET last_flashed_at = ?,
                last_flashed_by = ?,
                last_flash_session_id = ?,
                last_history_id = ?,
                last_success_at = ?,
                last_failure_at = ?,
                flash_count = ?,
                status = ?
            WHERE id = ?
            """,
            (now, flashed_by, session_id, history_id, success_at, failure_at, flash_count, status, file_id),
        )
        refreshed = conn.execute("SELECT * FROM hex_files WHERE id = ?", (file_id,)).fetchone()
        return _hex_file_row(refreshed)


def update_hex_file_notes(file_id: str, notes: str) -> dict[str, Any] | None:
    trimmed = notes.strip() if notes else ""
    final = trimmed if trimmed else None
    with transaction() as conn:
        row = conn.execute("SELECT * FROM hex_files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE hex_files SET notes = ? WHERE id = ?", (final, file_id))
        refreshed = conn.execute("SELECT * FROM hex_files WHERE id = ?", (file_id,)).fetchone()
        return _hex_file_row(refreshed)


# ── Flash history / logs ─────────────────────────────────────────────────────


def list_flash_history(
    limit: int = 250,
    *,
    file_id: str | None = None,
    include_logs: bool = False,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if file_id:
            rows = conn.execute(
                "SELECT * FROM flash_history WHERE file_id = ? ORDER BY requested_at DESC LIMIT ?",
                (file_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM flash_history ORDER BY requested_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_history_row(row, include_logs=include_logs) for row in rows if row is not None]  # type: ignore[list-item]


def add_flash_history(
    *,
    file_id: str | None,
    name: str,
    status: str,
    action: str,
    notes: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    operator: str | None = None,
    session_id: str | None = None,
    phase: str | None = None,
    progress_pct: float | None = None,
    file_size: int | None = None,
    file_crc32: str | None = None,
    requested_at: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    result: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    logs: list[str] | None = None,
) -> dict[str, Any]:
    history_id = _new_id("fh")
    requested_at = requested_at or _now()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO flash_history (
                id, file_id, name, requested_at, started_at, completed_at, status,
                action, phase, progress_pct, notes, error, duration_ms, operator,
                session_id, file_size, file_crc32, result_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                history_id,
                file_id,
                name,
                requested_at,
                started_at,
                completed_at,
                status,
                action,
                phase,
                progress_pct,
                notes,
                error,
                duration_ms,
                operator,
                session_id,
                file_size,
                file_crc32,
                _json(result),
                _json(metadata),
            ),
        )
        if logs:
            for idx, line in enumerate(logs, start=1):
                conn.execute(
                    "INSERT INTO flash_logs (history_id, line_no, line_text, created_at) VALUES (?, ?, ?, ?)",
                    (history_id, idx, line, requested_at),
                )
        row = conn.execute("SELECT * FROM flash_history WHERE id = ?", (history_id,)).fetchone()
        return _history_row(row)  # type: ignore[return-value]


def get_flash_history_entry(history_id: str, *, include_logs: bool = True) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM flash_history WHERE id = ?", (history_id,)).fetchone()
        return _history_row(row, include_logs=include_logs)


def append_flash_log(history_id: str, line_text: str, *, created_at: str | None = None) -> int:
    created_at = created_at or _now()
    with transaction() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(line_no), 0) + 1 AS next_line FROM flash_logs WHERE history_id = ?",
            (history_id,),
        ).fetchone()
        next_line = int(row["next_line"] if row else 1)
        conn.execute(
            "INSERT INTO flash_logs (history_id, line_no, line_text, created_at) VALUES (?, ?, ?, ?)",
            (history_id, next_line, line_text, created_at),
        )
        return next_line


def get_flash_logs(history_id: str, *, after_line_no: int | None = None, limit: int = 4000) -> dict[str, Any]:
    with _connect() as conn:
        entry = conn.execute("SELECT status FROM flash_history WHERE id = ?", (history_id,)).fetchone()
        if entry is None:
            return {"logs": [], "status": None}
        if after_line_no is None:
            rows = conn.execute(
                "SELECT line_no, line_text FROM flash_logs WHERE history_id = ? ORDER BY line_no ASC LIMIT ?",
                (history_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT line_no, line_text FROM flash_logs WHERE history_id = ? AND line_no > ? ORDER BY line_no ASC LIMIT ?",
                (history_id, after_line_no, limit),
            ).fetchall()
        return {
            "logs": [row["line_text"] for row in rows],
            "lines": [{"lineNo": row["line_no"], "line": row["line_text"]} for row in rows],
            "status": entry["status"],
        }


def update_flash_history_entry(history_id: str, **kwargs: Any) -> dict[str, Any] | None:
    allowed = {
        "file_id": "file_id",
        "name": "name",
        "requested_at": "requested_at",
        "started_at": "started_at",
        "completed_at": "completed_at",
        "status": "status",
        "action": "action",
        "phase": "phase",
        "progress_pct": "progress_pct",
        "notes": "notes",
        "error": "error",
        "duration_ms": "duration_ms",
        "operator": "operator",
        "session_id": "session_id",
        "file_size": "file_size",
        "file_crc32": "file_crc32",
        "result": "result_json",
        "metadata": "metadata_json",
    }
    updates: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in allowed:
            column = allowed[key]
            if column in {"result_json", "metadata_json"}:
                updates[column] = _json(value)
            else:
                updates[column] = value
    if not updates:
        return get_flash_history_entry(history_id)
    set_clause = ", ".join(f"{column} = ?" for column in updates)
    values = list(updates.values()) + [history_id]
    with transaction() as conn:
        conn.execute(f"UPDATE flash_history SET {set_clause} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM flash_history WHERE id = ?", (history_id,)).fetchone()
        return _history_row(row)


def update_flash_history_notes(history_id: str, notes: str) -> dict[str, Any] | None:
    trimmed = notes.strip() if notes else ""
    final = trimmed if trimmed else None
    with transaction() as conn:
        row = conn.execute("SELECT * FROM flash_history WHERE id = ?", (history_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE flash_history SET notes = ? WHERE id = ?", (final, history_id))
        if row["file_id"]:
            conn.execute("UPDATE hex_files SET notes = ? WHERE id = ?", (final, row["file_id"]))
        refreshed = conn.execute("SELECT * FROM flash_history WHERE id = ?", (history_id,)).fetchone()
        return _history_row(refreshed)


# ── Cleanup ──────────────────────────────────────────────────────────────────


def prune_orphans(stored_names: Iterable[str]) -> dict[str, Any]:
    stored_set = set(stored_names)
    with transaction() as conn:
        rows = conn.execute("SELECT id, stored_name FROM hex_files WHERE stored_name IS NOT NULL").fetchall()
        missing_ids = [row["id"] for row in rows if row["stored_name"] not in stored_set]
        if missing_ids:
            placeholders = ",".join("?" for _ in missing_ids)
            affected_history_rows = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM flash_history WHERE file_id IN ({placeholders})",
                missing_ids,
            ).fetchone()
            affected_history = int(affected_history_rows["cnt"] if affected_history_rows else 0)
            conn.execute(f"DELETE FROM hex_files WHERE id IN ({placeholders})", missing_ids)
        else:
            affected_history = 0
        return {"removedFiles": len(missing_ids), "removedHistory": affected_history}


def clear_all(*, clear_sessions: bool = False) -> None:
    now = _now()
    with transaction() as conn:
        conn.execute("DELETE FROM flash_logs")
        conn.execute("DELETE FROM flash_history")
        conn.execute("DELETE FROM hex_files")
        if clear_sessions:
            conn.execute("DELETE FROM sessions")
        conn.execute(
            """
            UPDATE vcu_state
            SET state = 'idle', phase = 'idle', progress_pct = NULL, active_history_id = NULL,
                power_cycle = 0, imd_waiting = 0, last_error = NULL, locked_by_session_id = NULL,
                priority_session_id = NULL, priority_operator_name = NULL, priority_until = NULL,
                updated_at = ?
            WHERE id = 1
            """,
            (now,),
        )
