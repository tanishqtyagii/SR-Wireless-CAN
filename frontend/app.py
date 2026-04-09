import os
import re
import sys
import threading
import time
import ast
import io
import hashlib
from datetime import datetime, timezone


from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import db.schema as db

LATEST_DIR = os.path.join(ROOT_DIR, "latest")
if LATEST_DIR not in sys.path:
    sys.path.insert(0, LATEST_DIR)

from CAN_controller import CANController
from bootloader import bootload as run_bootload
from finalization import finalize as run_finalize
from hex_transfer import flash_hex as run_hex_transfer
from intelhex import IntelHex

# ── Power-cycle flag ──────────────────────────────────────────────────────────

_power_cycle_lock = threading.Lock()
_power_cycle_active: bool = False


def _set_power_cycle(val: bool) -> None:
    global _power_cycle_active
    with _power_cycle_lock:
        _power_cycle_active = val


def _get_power_cycle() -> bool:
    with _power_cycle_lock:
        return _power_cycle_active


# ── IMD confirmation gate ─────────────────────────────────────────────────────

_imd_lock = threading.Lock()
_imd_waiting: bool = False
_imd_event = threading.Event()


def _set_imd_waiting(val: bool) -> None:
    global _imd_waiting
    with _imd_lock:
        _imd_waiting = val


def _get_imd_waiting() -> bool:
    with _imd_lock:
        return _imd_waiting


class _PowerCycleInterceptor:
    """Wraps sys.stdout; fires _set_power_cycle(True) on the first power-cycle print."""

    def __init__(self, underlying, on_line=None):
        self._underlying = underlying
        self._on_line = on_line

    def write(self, text: str):
        if "POWER CYCLE" in text.upper():
            _set_power_cycle(True)
        if self._on_line and text:
            for line in text.splitlines():
                cleaned = line.strip()
                if cleaned:
                    self._on_line(cleaned)
        return self._underlying.write(text)

    def flush(self):
        return self._underlying.flush()

    def __getattr__(self, name):
        return getattr(self._underlying, name)


# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})
MAX_HEX_BYTES = int(os.getenv("MAX_HEX_BYTES", str(8 * 1024 * 1024)))
app.config["MAX_CONTENT_LENGTH"] = MAX_HEX_BYTES

UPLOAD_FOLDER = os.path.join(ROOT_DIR, "db", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hex_path(file_id: str) -> str:
    if not re.fullmatch(r"hf_\d+", file_id):
        raise ValueError("Invalid file id")
    return os.path.join(UPLOAD_FOLDER, f"{file_id}.hex")


def _stored_ids() -> list[str]:
    """Return file IDs that have a binary present in uploads/."""
    return [
        fname[:-4]
        for fname in os.listdir(UPLOAD_FOLDER)
        if fname.endswith(".hex")
    ]


def _save_file_bytes(file_id: str, data: bytes) -> None:
    if len(data) == 0:
        raise ValueError("Empty file payload")
    if len(data) > MAX_HEX_BYTES:
        raise ValueError(f"File exceeds maximum allowed size of {MAX_HEX_BYTES} bytes")
    with open(_hex_path(file_id), "wb") as f:
        f.write(data)


def _size_kb(file_id: str) -> float:
    p = _hex_path(file_id)
    return os.path.getsize(p) / 1024 if os.path.isfile(p) else 0.0


def _is_hex_upload(filename: str | None) -> bool:
    return bool(filename) and filename.lower().endswith(".hex")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_header80() -> list[int]:
    runner_path = os.path.join(LATEST_DIR, "runner.py")
    with open(runner_path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=runner_path)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "header80":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, list) and len(value) == 0x80:
                        return [int(x) & 0xFF for x in value]
    raise ValueError("Could not load header80 from latest/runner.py")


def _load_hex_lenient(path: str) -> IntelHex:
    try:
        return IntelHex(path)
    except Exception:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        fixed_lines: list[str] = []
        for line in content.splitlines():
            raw = line.strip()
            if not raw:
                continue
            if raw.startswith(":"):
                fixed_lines.append("".join(raw.split()))

        if not fixed_lines:
            raise

        ih = IntelHex()
        ih.loadhex(io.StringIO("\n".join(fixed_lines) + "\n"))
        return ih


def _library_group_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _hex_fingerprint(file_id: str) -> str | None:
    path = _hex_path(file_id)
    if not os.path.isfile(path):
        return None

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _build_library_snapshot(limit: int = 250) -> dict:
    files = db.list_hex_files()
    flashes = db.list_flash_history(limit=limit, include_logs=False)

    groups: dict[str, dict] = {}
    name_to_key: dict[str, str] = {}
    file_id_to_key: dict[str, str] = {}

    for file in files:
        display_name = file.get("displayName") or file["name"]
        fingerprint = _hex_fingerprint(file["id"])
        key = f"content:{fingerprint}" if fingerprint else f"file:{file['id']}"
        existing = groups.get(key)

        if not existing:
            groups[key] = {
                "id": key,
                "fileId": file["id"],
                "fileIds": [file["id"]],
                "name": file["name"],
                "displayName": display_name,
                "aliasNames": [display_name],
                "size": file.get("size"),
                "uploadedAt": file.get("uploadedAt"),
                "lastFlashedAt": file.get("lastFlashedAt"),
                "lastFlashedBy": file.get("lastFlashedBy"),
                "status": file.get("status", "pending"),
                "notes": file.get("notes"),
                "hasPayload": True,
                "fileVariants": [{
                    "fileId": file["id"],
                    "name": file["name"],
                    "displayName": display_name,
                    "notes": file.get("notes"),
                    "uploadedAt": file.get("uploadedAt"),
                    "lastFlashedAt": file.get("lastFlashedAt"),
                    "lastFlashedBy": file.get("lastFlashedBy"),
                    "status": file.get("status", "pending"),
                }],
            }
        else:
            if file["id"] not in existing["fileIds"]:
                existing["fileIds"].append(file["id"])
            if display_name not in existing["aliasNames"]:
                existing["aliasNames"].append(display_name)
            if not any(variant["fileId"] == file["id"] for variant in existing["fileVariants"]):
                existing["fileVariants"].append({
                    "fileId": file["id"],
                    "name": file["name"],
                    "displayName": display_name,
                    "notes": file.get("notes"),
                    "uploadedAt": file.get("uploadedAt"),
                    "lastFlashedAt": file.get("lastFlashedAt"),
                    "lastFlashedBy": file.get("lastFlashedBy"),
                    "status": file.get("status", "pending"),
                })

            uploaded_at = file.get("uploadedAt")
            current_uploaded_at = existing.get("uploadedAt")
            if uploaded_at and (not current_uploaded_at or uploaded_at >= current_uploaded_at):
                existing["fileId"] = file["id"]
                existing["name"] = file["name"]
                existing["displayName"] = display_name
                existing["size"] = file.get("size")
                existing["uploadedAt"] = uploaded_at
                if file.get("notes"):
                    existing["notes"] = file.get("notes")

            last_flashed_at = file.get("lastFlashedAt")
            current_last_flashed_at = existing.get("lastFlashedAt")
            if last_flashed_at and (not current_last_flashed_at or last_flashed_at >= current_last_flashed_at):
                existing["lastFlashedAt"] = last_flashed_at
                existing["lastFlashedBy"] = file.get("lastFlashedBy")
                existing["status"] = file.get("status", existing.get("status", "pending"))

        file_id_to_key[file["id"]] = key
        name_to_key.setdefault(_library_group_name(display_name), key)

    for entry in flashes:
        if entry.get("action") == "bootload":
            continue

        entry_name = (entry.get("name") or "").strip() or "Unknown"
        normalized_name = _library_group_name(entry_name)
        explicit_key = file_id_to_key.get(entry["fileId"]) if entry.get("fileId") else None
        key = explicit_key or name_to_key.get(normalized_name)

        if not key:
            key = f"history:{normalized_name or entry['id']}"
            groups[key] = {
                "id": key,
                "fileId": None,
                "fileIds": [],
                "name": entry_name,
                "displayName": entry_name,
                "aliasNames": [entry_name],
                "size": None,
                "uploadedAt": entry.get("timestamp"),
                "lastFlashedAt": entry.get("timestamp"),
                "lastFlashedBy": entry.get("operator"),
                "status": entry.get("status", "unknown"),
                "notes": entry.get("notes"),
                "hasPayload": False,
                "fileVariants": [],
            }
            if normalized_name:
                name_to_key[normalized_name] = key

        group = groups[key]
        if entry.get("fileId") and entry["fileId"] not in group["fileIds"]:
            group["fileIds"].append(entry["fileId"])
        if entry_name not in group["aliasNames"]:
            group["aliasNames"].append(entry_name)
        timestamp = entry.get("timestamp")

        if timestamp and (not group.get("uploadedAt") or timestamp < group["uploadedAt"]):
            group["uploadedAt"] = timestamp

        if timestamp and (not group.get("lastFlashedAt") or timestamp >= group["lastFlashedAt"]):
            group["lastFlashedAt"] = timestamp
            group["lastFlashedBy"] = entry.get("operator")
            group["status"] = entry.get("status", group.get("status", "unknown"))
            if entry.get("notes"):
                group["notes"] = entry["notes"]
        elif not group.get("notes") and entry.get("notes"):
            group["notes"] = entry["notes"]

    for group in groups.values():
        if group.get("fileVariants"):
            group["fileVariants"].sort(
                key=lambda variant: variant.get("uploadedAt") or "",
                reverse=True,
            )

    return {
        "grouped": list(groups.values()),
        "flashes": flashes,
    }


# ── Background flash threads ──────────────────────────────────────────────────

def _run_boot_and_flash(file_id: str, display_name: str, history_id: str) -> None:
    """Run real boot + flash using latest/ pipeline."""
    logs: list[str] = []
    started_at = time.monotonic()

    interface = os.getenv("FLASH_CAN_INTERFACE", "socketcan")
    channel = os.getenv("FLASH_CAN_CHANNEL", "can0")
    do_kernel = _env_flag("FLASH_DO_KERNEL", True)
    do_erase = _env_flag("FLASH_DO_ERASE", True)
    do_finalize = _env_flag("FLASH_DO_FINALIZE", True)

    def log(msg: str) -> None:
        logs.append(f"[{_now()}] {msg}")
        db.update_flash_history_entry(history_id, logs=list(logs))

    try:
        path = _hex_path(file_id)
        log(f"Starting boot + flash sequence for {display_name}")
        log(f"CAN interface={interface}, channel={channel}")

        ctrl = CANController(interface=interface, channel=channel)
        try:
            log("Entering bootloader mode...")
            _old_stdout = sys.stdout
            sys.stdout = _PowerCycleInterceptor(sys.stdout, on_line=log)
            try:
                run_bootload(ctrl)
            finally:
                sys.stdout = _old_stdout
                _set_power_cycle(False)
            log("Bootloader acknowledged")

            log("Waiting for IMD confirmation before flashing...")
            _imd_event.clear()
            _set_imd_waiting(True)
            confirmed = _imd_event.wait(timeout=300)  # 5-minute timeout
            _set_imd_waiting(False)
            if not confirmed:
                raise Exception("IMD confirmation timed out (5 min). Aborting flash.")
            log("IMD confirmed — proceeding with flash")

            log(f"Uploading binary ({_size_kb(file_id):.1f} KB)...")
            ih = _load_hex_lenient(path)
            header80 = _load_header80()
            result = run_hex_transfer(
                ctrl,
                ih,
                header80,
                do_flash_kernel=do_kernel,
                do_erase=do_erase,
            )
            log(f"Flash complete: blocks={result.get('blocks')} total_len={result.get('total_len')}")

            if do_finalize:
                log("Running finalization...")
                run_finalize(ctrl)
                log("Finalization complete")
        finally:
            ctrl.close()

        duration_ms = int((time.monotonic() - started_at) * 1000)
        log("VCU returned to idle state")

        db.update_hex_file_after_flash(file_id, "success")
        db.update_flash_history_entry(
            history_id,
            status="success",
            duration_ms=duration_ms,
            logs=list(logs),
        )

    except Exception as exc:  # noqa: BLE001
        log(f"Error: {exc}")
        duration_ms = int((time.monotonic() - started_at) * 1000)
        db.update_flash_history_entry(
            history_id,
            status="failed",
            error=str(exc),
            duration_ms=duration_ms,
            logs=list(logs),
        )
        db.update_hex_file_after_flash(file_id, "failed")

    finally:
        db.set_vcu_state("idle")


def _run_flash_only(file_id: str, display_name: str, history_id: str) -> None:
    """Run real flash-only using latest/ pipeline (no bootload step)."""
    logs: list[str] = []
    started_at = time.monotonic()

    interface = os.getenv("FLASH_CAN_INTERFACE", "socketcan")
    channel = os.getenv("FLASH_CAN_CHANNEL", "can0")
    do_kernel = _env_flag("FLASH_DO_KERNEL", False)
    do_erase = _env_flag("FLASH_DO_ERASE", True)
    do_finalize = _env_flag("FLASH_DO_FINALIZE", True)

    def log(msg: str) -> None:
        logs.append(f"[{_now()}] {msg}")
        db.update_flash_history_entry(history_id, logs=list(logs))

    try:
        path = _hex_path(file_id)
        log(f"Starting flash-only sequence for {display_name}")
        log(f"CAN interface={interface}, channel={channel}")

        ctrl = CANController(interface=interface, channel=channel)
        try:
            log("VCU already in bootloader mode")
            log(f"Uploading binary ({_size_kb(file_id):.1f} KB)...")
            ih = _load_hex_lenient(path)
            header80 = _load_header80()
            result = run_hex_transfer(
                ctrl,
                ih,
                header80,
                do_flash_kernel=do_kernel,
                do_erase=do_erase,
            )
            log(f"Flash complete: blocks={result.get('blocks')} total_len={result.get('total_len')}")

            if do_finalize:
                log("Running finalization...")
                run_finalize(ctrl)
                log("Finalization complete")
        finally:
            ctrl.close()

        duration_ms = int((time.monotonic() - started_at) * 1000)
        log("VCU returned to idle state")

        db.update_hex_file_after_flash(file_id, "success")
        db.update_flash_history_entry(
            history_id,
            status="success",
            duration_ms=duration_ms,
            logs=list(logs),
        )

    except Exception as exc:  # noqa: BLE001
        log(f"Error: {exc}")
        duration_ms = int((time.monotonic() - started_at) * 1000)
        db.update_flash_history_entry(
            history_id,
            status="failed",
            error=str(exc),
            duration_ms=duration_ms,
            logs=list(logs),
        )
        db.update_hex_file_after_flash(file_id, "failed")

    finally:
        db.set_vcu_state("idle")


# ── VCU State ─────────────────────────────────────────────────────────────────

@app.get("/api/vcu-state")
def get_vcu_state():
    return jsonify({
        "state": db.get_vcu_state(),
        "powerCycle": _get_power_cycle(),
        "imdWaiting": _get_imd_waiting(),
    })


@app.post("/api/imd-confirm")
def imd_confirm():
    _imd_event.set()
    return jsonify({"ok": True})


# ── Hex Files ─────────────────────────────────────────────────────────────────

@app.get("/api/hex-files")
def list_hex_files():
    return jsonify(db.list_hex_files())


@app.get("/api/hex-files/stored-ids")
def stored_ids():
    return jsonify(_stored_ids())


@app.post("/api/hex-files/upload")
def upload_hex():
    if "file" not in request.files:
        return jsonify({"error": "Missing file field"}), 400

    f = request.files["file"]
    if not _is_hex_upload(f.filename):
        return jsonify({"error": "Only .hex files are allowed."}), 400
    display_name = (
        request.form.get("display_name") or request.form.get("displayName") or ""
    ).strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    data = f.read()
    record, _ = db.upsert_hex_file(f.filename, display_name, len(data), notes)
    _save_file_bytes(record["id"], data)

    return jsonify({"item": record})


@app.get("/api/hex-files/<file_id>/content")
def get_hex_content(file_id: str):
    try:
        path = _hex_path(file_id)
    except ValueError:
        return jsonify({"error": "Invalid file id."}), 400

    record = db.get_hex_file(file_id)
    if not record:
        return jsonify({"error": "File not found"}), 404

    if not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404

    raw_name = (record or {}).get('displayName') or (record or {}).get('name') or file_id
    base_name = raw_name[:-4] if raw_name.lower().endswith('.hex') else raw_name
    base_name = os.path.basename(base_name).replace("\\", "_").replace("/", "_")
    filename = f"{base_name}.hex"
    return send_file(path, as_attachment=True, download_name=filename)


@app.get("/api/library")
def library_snapshot():
    limit = request.args.get("limit", type=int) or 250
    return jsonify(_build_library_snapshot(limit=limit))


@app.patch("/api/hex-files/<file_id>/notes")
def update_hex_file_notes(file_id: str):
    body = request.get_json(silent=True) or {}
    notes = body.get("notes", "")
    updated = db.update_hex_file_notes(file_id, notes)
    if not updated:
        return jsonify({"error": "File not found"}), 404
    return jsonify(updated)


# ── Flash History ─────────────────────────────────────────────────────────────

@app.get("/api/flash-history")
def list_flash_history():
    include_logs = request.args.get("includeLogs", "0").strip().lower() in {"1", "true", "yes"}
    file_id = (request.args.get("fileId") or "").strip() or None
    limit = request.args.get("limit", type=int) or 250
    return jsonify(db.list_flash_history(limit=limit, file_id=file_id, include_logs=include_logs))


@app.get("/api/flash-history/<entry_id>/logs")
def get_flash_logs(entry_id: str):
    entry = db.get_flash_history_entry(entry_id)
    if not entry:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"logs": entry.get("logs") or [], "status": entry.get("status")})


@app.patch("/api/flash-history/<entry_id>/notes")
def update_notes(entry_id: str):
    body = request.get_json(silent=True) or {}
    notes = body.get("notes", "")
    updated = db.update_flash_history_notes(entry_id, notes)
    if not updated:
        return jsonify({"error": "History entry not found"}), 404
    return jsonify(updated)


# ── Flash Operations ──────────────────────────────────────────────────────────

@app.post("/api/bootload")
def bootload_only():
    if not db.try_transition_vcu_state("idle", "bootloading"):
        current = db.get_vcu_state()
        return jsonify({"error": f"VCU is currently busy ({current}). Wait for it to return to idle."}), 409

    interface = os.getenv("FLASH_CAN_INTERFACE", "socketcan")
    channel = os.getenv("FLASH_CAN_CHANNEL", "can0")

    logs: list[str] = []
    history = db.add_flash_history(
        file_id=None,
        name="Bootload",
        status="pending",
        action="bootload",
        logs=[f"[{_now()}] Starting bootloader handshake"],
    )
    history_id = history["id"]

    def log(msg: str) -> None:
        logs.append(f"[{_now()}] {msg}")
        db.update_flash_history_entry(history_id, logs=list(logs))

    # Real handshake; on success transition bootloading → bootloaded so the UI
    # shows the VCU is sitting in bootloader mode and ready for a flash-only.
    def _handshake():
        try:
            ctrl = CANController(interface=interface, channel=channel)
            try:
                _old_stdout = sys.stdout
                sys.stdout = _PowerCycleInterceptor(sys.stdout, on_line=log)
                try:
                    run_bootload(ctrl)
                finally:
                    sys.stdout = _old_stdout
                    _set_power_cycle(False)
            finally:
                ctrl.close()
            # Success — VCU is now in bootloader mode, ready for flash
            log("Bootloader acknowledged")
            db.update_flash_history_entry(history_id, status="success", logs=list(logs))
            db.set_vcu_state("bootloaded")
        except Exception as exc:  # noqa: BLE001
            _set_power_cycle(False)
            log(f"Error: {exc}")
            db.update_flash_history_entry(history_id, status="failed", error=str(exc), logs=list(logs))
            db.set_vcu_state("idle")

    threading.Thread(target=_handshake, daemon=True).start()
    return jsonify({"state": "bootloading", "historyId": history_id})


@app.post("/api/boot-and-flash")
def boot_and_flash():
    if "file" not in request.files:
        return jsonify({"error": "Missing file field"}), 400

    # Atomic: only succeeds if state is exactly "idle" right now.
    # If two requests race, SQLite serialises the writes — only one wins.
    if not db.try_transition_vcu_state("idle", "flashing"):
        current = db.get_vcu_state()
        return jsonify({"error": f"VCU is currently busy ({current})."}), 409

    f = request.files["file"]
    if not _is_hex_upload(f.filename):
        db.set_vcu_state("idle")
        return jsonify({"error": "Only .hex files are allowed."}), 400
    display_name = (
        request.form.get("display_name") or request.form.get("displayName") or ""
    ).strip() or None
    notes = (request.form.get("notes") or "").strip() or None
    operator = (request.form.get("operator") or "").strip() or None

    data = f.read()
    record, _ = db.upsert_hex_file(f.filename, display_name, len(data), notes)
    _save_file_bytes(record["id"], data)

    shown_name = record.get("displayName") or record["name"]

    history = db.add_flash_history(
        file_id=record["id"],
        name=shown_name,
        status="pending",
        action="boot_and_flash",
        notes=notes or record.get("notes"),
        logs=[f"[{_now()}] Starting boot + flash sequence for {shown_name}"],
        operator=operator,
    )

    threading.Thread(
        target=_run_boot_and_flash,
        args=(record["id"], shown_name, history["id"]),
        daemon=True,
    ).start()

    return jsonify({"ok": True, "historyId": history["id"]})


@app.post("/api/flash-only")
def flash_only():
    if "file" not in request.files:
        return jsonify({"error": "Missing file field"}), 400

    # Atomic: only succeeds if state is exactly "bootloaded" right now.
    # If two requests race, SQLite serialises the writes — only one wins.
    if not db.try_transition_vcu_state("bootloaded", "flashing"):
        current = db.get_vcu_state()
        if current == "flashing":
            return jsonify({"error": "VCU is currently flashing. Wait for it to finish."}), 409
        return jsonify({"error": f"VCU must be in bootloaded mode before flashing (currently: {current})."}), 409

    f = request.files["file"]
    if not _is_hex_upload(f.filename):
        db.set_vcu_state("bootloaded")
        return jsonify({"error": "Only .hex files are allowed."}), 400
    display_name = (
        request.form.get("display_name") or request.form.get("displayName") or ""
    ).strip() or None
    notes = (request.form.get("notes") or "").strip() or None
    operator = (request.form.get("operator") or "").strip() or None

    data = f.read()
    record, _ = db.upsert_hex_file(f.filename, display_name, len(data), notes)
    _save_file_bytes(record["id"], data)

    shown_name = record.get("displayName") or record["name"]

    history = db.add_flash_history(
        file_id=record["id"],
        name=shown_name,
        status="pending",
        action="flash_only",
        notes=notes or record.get("notes"),
        logs=[f"[{_now()}] Starting flash-only sequence for {shown_name}"],
        operator=operator,
    )

    threading.Thread(
        target=_run_flash_only,
        args=(record["id"], shown_name, history["id"]),
        daemon=True,
    ).start()

    return jsonify({"ok": True, "historyId": history["id"]})


# ── Maintenance ───────────────────────────────────────────────────────────────

@app.post("/api/prune")
def prune():
    result = db.prune_orphans(_stored_ids())
    return jsonify(result)


@app.delete("/api/clear-all")
def clear_all():
    db.clear_all()
    for fname in os.listdir(UPLOAD_FOLDER):
        if fname.endswith(".hex"):
            os.remove(os.path.join(UPLOAD_FOLDER, fname))
    return jsonify({"ok": True})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
    )
