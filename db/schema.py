"""
Tables
------
hex_files      - metadata for every uploaded .hex file
flash_history  - record of every flash/bootload attempt
vcu_state      - single-row table holding current VCU state
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "vcu.db")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS hex_files (
    id           TEXT    PRIMARY KEY,
    name         TEXT    NOT NULL,
    display_name TEXT,
    size         INTEGER NOT NULL,
    uploaded_at  TEXT    NOT NULL,
    last_flashed_at TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending',
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS flash_history (
    id          TEXT    PRIMARY KEY,
    file_id     TEXT,
    name        TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    notes       TEXT,
    action      TEXT    NOT NULL,
    error       TEXT,
    duration_ms INTEGER,
    logs        TEXT,
    operator    TEXT,
    FOREIGN KEY (file_id) REFERENCES hex_files(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS vcu_state (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT    NOT NULL DEFAULT 'idle'
);

INSERT OR IGNORE INTO vcu_state (id, state) VALUES (1, 'idle');
"""


# ── Connection ────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call multiple times."""
    with _connect() as conn:
        conn.executescript(_CREATE_SQL)
        # Add operator column to existing DBs that predate this column.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(flash_history)").fetchall()}
        if "operator" not in existing_cols:
            conn.execute("ALTER TABLE flash_history ADD COLUMN operator TEXT")
        # Reset any stale in-progress state left over from a previous crash or
        # restart. Background flash threads don't survive a process restart, so
        # any "bootloading" or "flashing" value in the DB is guaranteed stale.
        conn.execute(
            "UPDATE vcu_state SET state = 'idle' WHERE id = 1 AND state != 'idle'"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_file_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 4) AS INTEGER)) AS seq FROM hex_files WHERE id LIKE 'hf_%'"
    ).fetchone()
    return f"hf_{(row['seq'] or 0) + 1}"


def _next_history_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 4) AS INTEGER)) AS seq FROM flash_history WHERE id LIKE 'fh_%'"
    ).fetchone()
    return f"fh_{(row['seq'] or 0) + 1}"


def _hex_file_row(row: sqlite3.Row) -> dict:
    return {
        "id":            row["id"],
        "name":          row["name"],
        "displayName":   row["display_name"],
        "size":          row["size"],
        "uploadedAt":    row["uploaded_at"],
        "lastFlashedAt": row["last_flashed_at"],
        "status":        row["status"],
        "notes":         row["notes"],
    }


def _history_row(row: sqlite3.Row) -> dict:
    logs = None
    if row["logs"]:
        try:
            logs = json.loads(row["logs"])
        except Exception:
            logs = []
    return {
        "id":        row["id"],
        "fileId":    row["file_id"],
        "name":      row["name"],
        "timestamp": row["timestamp"],
        "status":    row["status"],
        "notes":     row["notes"],
        "operator":  row["operator"],
        "logs":      logs,
    }


# ── VCU State ─────────────────────────────────────────────────────────────────

def get_vcu_state() -> str:
    with _connect() as conn:
        row = conn.execute("SELECT state FROM vcu_state WHERE id = 1").fetchone()
        return row["state"] if row else "idle"


def set_vcu_state(state: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE vcu_state SET state = ? WHERE id = 1", (state,))


def try_transition_vcu_state(expected: str, new: str) -> bool:
    """
    Atomically change state from `expected` → `new`.
    Returns True if the row was updated (we won the race), False if another
    request already moved the state away from `expected`.

    Because SQLite serialises writes, exactly one concurrent caller will see
    rowcount == 1; all others see 0 and know they lost.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE vcu_state SET state = ? WHERE id = 1 AND state = ?",
            (new, expected),
        )
        return cursor.rowcount == 1


# ── Hex Files ─────────────────────────────────────────────────────────────────

def list_hex_files() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM hex_files ORDER BY uploaded_at DESC"
        ).fetchall()
        return [_hex_file_row(r) for r in rows]


def get_hex_file(file_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM hex_files WHERE id = ?", (file_id,)
        ).fetchone()
        return _hex_file_row(row) if row else None


def upsert_hex_file(
    name: str,
    display_name: str | None,
    size: int,
    notes: str | None,
) -> tuple[dict, bool]:
    """
    Return (record, is_new).
    If a file with the same lookup name and size already exists, return it
    (updating notes if provided).  Otherwise insert a new record.
    """
    lookup = display_name or name
    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM hex_files WHERE COALESCE(display_name, name) = ? AND size = ?",
            (lookup, size),
        ).fetchone()

        if existing:
            if notes is not None:
                conn.execute(
                    "UPDATE hex_files SET notes = ? WHERE id = ?",
                    (notes, existing["id"]),
                )
                conn.commit()
            refreshed = conn.execute(
                "SELECT * FROM hex_files WHERE id = ?", (existing["id"],)
            ).fetchone()
            return _hex_file_row(refreshed), False

        new_id = _next_file_id(conn)
        conn.execute(
            """INSERT INTO hex_files
               (id, name, display_name, size, uploaded_at, status, notes)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (new_id, name, display_name, size, _now(), notes),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM hex_files WHERE id = ?", (new_id,)
        ).fetchone()
        return _hex_file_row(row), True


def update_hex_file_after_flash(file_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE hex_files SET last_flashed_at = ?, status = ? WHERE id = ?",
            (_now(), status, file_id),
        )


# ── Flash History ─────────────────────────────────────────────────────────────

def list_flash_history(limit: int = 250) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM flash_history ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_history_row(r) for r in rows]


def add_flash_history(
    *,
    file_id: str,
    name: str,
    status: str,
    action: str,
    notes: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    logs: list[str] | None = None,
    operator: str | None = None,
) -> dict:
    logs_json = json.dumps(logs) if logs is not None else None
    with _connect() as conn:
        new_id = _next_history_id(conn)
        conn.execute(
            """INSERT INTO flash_history
               (id, file_id, name, timestamp, status, notes, action, error, duration_ms, logs, operator)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id, file_id, name, _now(), status, notes, action, error, duration_ms, logs_json, operator),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM flash_history WHERE id = ?", (new_id,)
        ).fetchone()
        return _history_row(row)


def update_flash_history_entry(history_id: str, **kwargs) -> None:
    """
    Update any subset of: status, notes, error, duration_ms, logs.
    logs should be a list[str]; it will be JSON-encoded automatically.
    """
    _ALLOWED = {"status", "notes", "error", "duration_ms", "logs"}
    fields: dict[str, object] = {}
    for k, v in kwargs.items():
        if k in _ALLOWED:
            fields[k] = json.dumps(v) if k == "logs" else v
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [history_id]
    with _connect() as conn:
        conn.execute(
            f"UPDATE flash_history SET {set_clause} WHERE id = ?", values
        )


def update_flash_history_notes(history_id: str, notes: str) -> dict | None:
    """
    Update notes on a history entry and mirror the change to the linked hex file.
    Returns the updated history row or None if not found.
    """
    trimmed = notes.strip() if notes else ""
    final = trimmed if trimmed else None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM flash_history WHERE id = ?", (history_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE flash_history SET notes = ? WHERE id = ?", (final, history_id)
        )
        if row["file_id"]:
            conn.execute(
                "UPDATE hex_files SET notes = ? WHERE id = ?",
                (final, row["file_id"]),
            )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM flash_history WHERE id = ?", (history_id,)
        ).fetchone()
        return _history_row(updated)


# ── Maintenance ───────────────────────────────────────────────────────────────

def prune_orphans(stored_ids: list[str]) -> dict:
    """
    Remove hex_file rows whose binary is missing from disk, and any flash_history
    rows that referenced them.
    """
    stored_set = set(stored_ids)
    with _connect() as conn:
        all_files = conn.execute("SELECT id FROM hex_files").fetchall()
        in_db = {r["id"] for r in all_files}

        remove_files = [fid for fid in in_db if fid not in stored_set]
        for fid in remove_files:
            conn.execute("DELETE FROM hex_files WHERE id = ?", (fid,))

        valid = in_db - set(remove_files)
        if valid:
            placeholders = ",".join("?" for _ in valid)
            stale_history = conn.execute(
                f"SELECT id FROM flash_history WHERE file_id IS NOT NULL AND file_id NOT IN ({placeholders})",
                list(valid),
            ).fetchall()
        else:
            stale_history = conn.execute(
                "SELECT id FROM flash_history WHERE file_id IS NOT NULL"
            ).fetchall()
        remove_hist = [r["id"] for r in stale_history]
        for hid in remove_hist:
            conn.execute("DELETE FROM flash_history WHERE id = ?", (hid,))

        conn.commit()

    return {"removedFiles": len(remove_files), "removedHistory": len(remove_hist)}


def clear_all() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM flash_history")
        conn.execute("DELETE FROM hex_files")
        conn.execute("UPDATE vcu_state SET state = 'idle' WHERE id = 1")
