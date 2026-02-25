import os
import re
import sys
import threading
import time
from datetime import datetime, timezone


from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import db.schema as db

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


# ── Background flash threads ──────────────────────────────────────────────────

def _run_boot_and_flash(file_id: str, display_name: str, history_id: str) -> None:
    """
    Simulates the boot + flash sequence.
    Replace the time.sleep() calls with real CAN bus communication when ready.
    """
    logs: list[str] = []

    def log(msg: str) -> None:
        logs.append(f"[{_now()}] {msg}")
        db.update_flash_history_entry(history_id, logs=list(logs))

    try:
        log(f"Starting boot + flash sequence for {display_name}")
        time.sleep(5)
        log("Entering bootloader mode...")
        time.sleep(8)
        log("Bootloader acknowledged")
        time.sleep(3)
        log(f"Uploading binary ({_size_kb(file_id):.1f} KB)...")
        time.sleep(14)
        log("Flash complete.")
        log("VCU returned to idle state")

        db.update_hex_file_after_flash(file_id, "success")
        db.update_flash_history_entry(history_id, status="success", logs=list(logs))

    except Exception as exc:  # noqa: BLE001
        log(f"Error: {exc}")
        db.update_flash_history_entry(history_id, status="failed", error=str(exc), logs=list(logs))
        db.update_hex_file_after_flash(file_id, "failed")

    finally:
        db.set_vcu_state("idle")


def _run_flash_only(file_id: str, display_name: str, history_id: str) -> None:
    """
    Simulates a flash-only sequence (VCU already in bootloading mode).
    Replace the time.sleep() calls with real CAN bus communication when ready.
    """
    logs: list[str] = []

    def log(msg: str) -> None:
        logs.append(f"[{_now()}] {msg}")
        db.update_flash_history_entry(history_id, logs=list(logs))

    try:
        log(f"Starting flash-only sequence for {display_name}")
        time.sleep(2)
        log("VCU already in bootloader mode")
        time.sleep(3)
        log(f"Uploading binary ({_size_kb(file_id):.1f} KB)...")
        time.sleep(25)
        log("Flash complete.")
        log("VCU returned to idle state")

        db.update_hex_file_after_flash(file_id, "success")
        db.update_flash_history_entry(history_id, status="success", logs=list(logs))

    except Exception as exc:  # noqa: BLE001
        log(f"Error: {exc}")
        db.update_flash_history_entry(history_id, status="failed", error=str(exc), logs=list(logs))
        db.update_hex_file_after_flash(file_id, "failed")

    finally:
        db.set_vcu_state("idle")


# ── VCU State ─────────────────────────────────────────────────────────────────

@app.get("/api/vcu-state")
def get_vcu_state():
    return jsonify({"state": db.get_vcu_state()})


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


# ── Flash History ─────────────────────────────────────────────────────────────

@app.get("/api/flash-history")
def list_flash_history():
    return jsonify(db.list_flash_history())


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

    # Simulate 3-second handshake, then stay in bootloading until flash arrives.
    def _handshake():
        time.sleep(3)
        # Intentionally does NOT reset state - stays in "bootloading"

    threading.Thread(target=_handshake, daemon=True).start()
    return jsonify({"state": "bootloading"})


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

    # Atomic: only succeeds if state is exactly "bootloading" right now.
    # If two requests race, SQLite serialises the writes — only one wins.
    if not db.try_transition_vcu_state("bootloading", "flashing"):
        current = db.get_vcu_state()
        if current == "flashing":
            return jsonify({"error": "VCU is currently flashing. Wait for it to finish."}), 409
        return jsonify({"error": f"VCU must be in bootloading mode before flashing (currently: {current})."}), 409

    f = request.files["file"]
    if not _is_hex_upload(f.filename):
        db.set_vcu_state("bootloading")
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
