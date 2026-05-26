from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.connections import ConnectionManager
from backend.operations import OperationManager, OperationRejected, SessionIdentity
from backend.settings import Settings
import db.schema as db

settings = Settings()
settings.ensure_dirs()
db.init_db()

connections = ConnectionManager()
manager = OperationManager(settings, connections)

app = FastAPI(title="VCU Flash Backend", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    manager.bind_loop(asyncio.get_running_loop())
    manager.prune_orphans()


# ── Session helpers ──────────────────────────────────────────────────────────


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _pick_operator_name(request: Request, explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    header = request.headers.get("x-operator-name")
    if header and header.strip():
        return header.strip()
    query = request.query_params.get("operator")
    if query and query.strip():
        return query.strip()
    return None


def _ensure_session(request: Request, *, operator_name: str | None = None, session_id_override: str | None = None) -> SessionIdentity:
    session_id = session_id_override or request.cookies.get(settings.session_cookie_name) or request.headers.get("x-session-id")
    if not session_id:
        session_id = db.create_session_id()
    session = manager.touch_session(
        session_id,
        operator_name=_pick_operator_name(request, operator_name),
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"path": str(request.url.path)},
    )
    return SessionIdentity(
        id=session["id"],
        operator_name=session.get("operatorName"),
        client_ip=session.get("clientIp"),
        user_agent=session.get("userAgent"),
    )


def _json_response(payload: Any, *, session_id: str | None = None, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    if session_id:
        _set_session_cookie(response, session_id)
    return response


def _error_response(error: OperationRejected, *, session_id: str | None = None) -> JSONResponse:
    return _json_response(error.as_dict(), session_id=session_id, status_code=error.status_code)


async def _resolve_firmware_payload(
    *,
    upload: UploadFile | None,
    file_id: str | None,
) -> tuple[str, bytes, dict[str, Any] | None]:
    if upload is not None and upload.filename:
        data = await upload.read()
        return upload.filename, data, None
    if file_id:
        record = manager.get_file_record(file_id)
        path = manager.get_file_path(file_id)
        if record is None or path is None:
            raise HTTPException(status_code=404, detail="Stored file not found")
        return record.get("displayName") or record.get("name") or f"{file_id}.hex", path.read_bytes(), record
    raise HTTPException(status_code=400, detail="Missing firmware file")


# ── Health / bootstrap ───────────────────────────────────────────────────────


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return {
        "ok": True,
        "state": manager.get_state_payload(),
        "simulate": settings.flash_simulate,
    }


@app.get("/api/bootstrap")
def api_bootstrap(request: Request) -> JSONResponse:
    session = _ensure_session(request)
    return _json_response(manager.build_snapshot(session_id=session.id), session_id=session.id)


@app.post("/api/session")
async def api_session(request: Request) -> JSONResponse:
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    operator_name = _pick_operator_name(request, body.get("operatorName") if isinstance(body, dict) else None)
    session = _ensure_session(request, operator_name=operator_name)
    payload = {"session": db.get_session(session.id), "state": manager.get_state_payload()}
    return _json_response(payload, session_id=session.id)


# ── VCU state ────────────────────────────────────────────────────────────────


@app.get("/api/vcu-state")
def api_vcu_state(request: Request) -> JSONResponse:
    session = _ensure_session(request)
    return _json_response(manager.get_state_payload(), session_id=session.id)


@app.post("/api/imd-confirm")
def api_imd_confirm(request: Request) -> JSONResponse:
    session = _ensure_session(request)
    return _json_response(manager.confirm_imd(session=session), session_id=session.id)


# ── Hex file library ─────────────────────────────────────────────────────────


@app.get("/api/hex-files")
def api_list_hex_files(request: Request) -> JSONResponse:
    session = _ensure_session(request)
    return _json_response(db.list_hex_files(), session_id=session.id)


@app.get("/api/hex-files/stored-ids")
def api_stored_ids(request: Request) -> JSONResponse:
    session = _ensure_session(request)
    items = [item["id"] for item in db.list_hex_files(limit=5000) if manager.get_file_path(item["id"]) is not None]
    return _json_response(items, session_id=session.id)


@app.post("/api/hex-files/upload")
async def api_upload_hex(
    request: Request,
    file: UploadFile = File(...),
    display_name: str | None = Form(default=None),
    displayName: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    operator: str | None = Form(default=None),
) -> JSONResponse:
    session = _ensure_session(request, operator_name=operator)
    data = await file.read()
    try:
        record = manager.upload_hex(
            session,
            filename=file.filename or "firmware.hex",
            data=data,
            display_name=display_name or displayName,
            notes=notes,
        )
    except OperationRejected as exc:
        return _error_response(exc, session_id=session.id)
    return _json_response({"item": record}, session_id=session.id)


@app.get("/api/hex-files/{file_id}/content")
def api_get_hex_content(file_id: str, request: Request) -> Response:
    session = _ensure_session(request)
    record = manager.get_file_record(file_id)
    path = manager.get_file_path(file_id)
    if record is None or path is None:
        raise HTTPException(status_code=404, detail="File not found")
    raw_name = record.get("displayName") or record.get("name") or file_id
    base_name = raw_name[:-4] if raw_name.lower().endswith(".hex") else raw_name
    download_name = f"{Path(base_name).name}.hex"
    response = FileResponse(path, media_type="application/octet-stream", filename=download_name)
    _set_session_cookie(response, session.id)
    return response


@app.patch("/api/hex-files/{file_id}/notes")
async def api_update_hex_notes(file_id: str, request: Request) -> JSONResponse:
    session = _ensure_session(request)
    body = await request.json()
    updated = manager.update_hex_notes(file_id, str(body.get("notes", "")))
    if not updated:
        raise HTTPException(status_code=404, detail="File not found")
    return _json_response(updated, session_id=session.id)


# ── Flash history ────────────────────────────────────────────────────────────


@app.get("/api/flash-history")
def api_list_flash_history(
    request: Request,
    fileId: str | None = None,
    includeLogs: bool = False,
    limit: int = 250,
) -> JSONResponse:
    session = _ensure_session(request)
    items = db.list_flash_history(limit=limit, file_id=fileId, include_logs=includeLogs)
    return _json_response(items, session_id=session.id)


@app.get("/api/flash-history/{entry_id}/logs")
def api_flash_logs(entry_id: str, request: Request, afterLineNo: int | None = None) -> JSONResponse:
    session = _ensure_session(request)
    if db.get_flash_history_entry(entry_id, include_logs=False) is None:
        raise HTTPException(status_code=404, detail="History entry not found")
    return _json_response(db.get_flash_logs(entry_id, after_line_no=afterLineNo), session_id=session.id)


@app.patch("/api/flash-history/{entry_id}/notes")
async def api_update_history_notes(entry_id: str, request: Request) -> JSONResponse:
    session = _ensure_session(request)
    body = await request.json()
    updated = manager.update_history_notes(entry_id, str(body.get("notes", "")))
    if not updated:
        raise HTTPException(status_code=404, detail="History entry not found")
    return _json_response(updated, session_id=session.id)


# ── Flash operations ─────────────────────────────────────────────────────────


@app.post("/api/bootload")
async def api_bootload(request: Request) -> JSONResponse:
    operator_name = None
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                operator_name = body.get("operator") or body.get("operatorName")
        except Exception:
            operator_name = None
    session = _ensure_session(request, operator_name=operator_name)
    try:
        result = manager.start_bootload(session)
    except OperationRejected as exc:
        return _error_response(exc, session_id=session.id)
    return _json_response(result, session_id=session.id)


@app.post("/api/boot-and-flash")
async def api_boot_and_flash(
    request: Request,
    file: UploadFile | None = File(default=None),
    display_name: str | None = Form(default=None),
    displayName: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    operator: str | None = Form(default=None),
    fileId: str | None = Form(default=None),
) -> JSONResponse:
    session = _ensure_session(request, operator_name=operator)
    try:
        filename, data, stored = await _resolve_firmware_payload(upload=file, file_id=fileId)
        result = manager.start_boot_and_flash(
            session,
            filename=filename,
            data=data,
            display_name=display_name or displayName or (stored.get("displayName") if stored else None),
            notes=notes if notes is not None else (stored.get("notes") if stored else None),
        )
    except HTTPException as exc:
        return _json_response({"error": exc.detail}, session_id=session.id, status_code=exc.status_code)
    except OperationRejected as exc:
        return _error_response(exc, session_id=session.id)
    return _json_response(result, session_id=session.id)


@app.post("/api/flash-only")
async def api_flash_only(
    request: Request,
    file: UploadFile | None = File(default=None),
    display_name: str | None = Form(default=None),
    displayName: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    operator: str | None = Form(default=None),
    fileId: str | None = Form(default=None),
) -> JSONResponse:
    session = _ensure_session(request, operator_name=operator)
    try:
        filename, data, stored = await _resolve_firmware_payload(upload=file, file_id=fileId)
        result = manager.start_flash_only(
            session,
            filename=filename,
            data=data,
            display_name=display_name or displayName or (stored.get("displayName") if stored else None),
            notes=notes if notes is not None else (stored.get("notes") if stored else None),
        )
    except HTTPException as exc:
        return _json_response({"error": exc.detail}, session_id=session.id, status_code=exc.status_code)
    except OperationRejected as exc:
        return _error_response(exc, session_id=session.id)
    return _json_response(result, session_id=session.id)


# ── Maintenance ──────────────────────────────────────────────────────────────


@app.post("/api/prune")
def api_prune(request: Request) -> JSONResponse:
    session = _ensure_session(request)
    return _json_response(manager.prune_orphans(), session_id=session.id)


@app.delete("/api/clear-all")
def api_clear_all(request: Request, clearSessions: bool = False) -> JSONResponse:
    session = _ensure_session(request)
    manager.clear_all(clear_sessions=clearSessions)
    return _json_response({"ok": True}, session_id=session.id)


# ── WebSocket ────────────────────────────────────────────────────────────────


async def _websocket_session(websocket: WebSocket) -> SessionIdentity:
    session_id = websocket.cookies.get(settings.session_cookie_name) or websocket.query_params.get("sessionId") or db.create_session_id()
    operator_name = websocket.query_params.get("operator")
    session = manager.touch_session(
        session_id,
        operator_name=operator_name,
        client_ip=websocket.client.host if websocket.client else None,
        user_agent=websocket.headers.get("user-agent"),
        metadata={"path": websocket.url.path},
    )
    return SessionIdentity(
        id=session["id"],
        operator_name=session.get("operatorName"),
        client_ip=session.get("clientIp"),
        user_agent=session.get("userAgent"),
    )


@app.websocket("/ws")
@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    session = await _websocket_session(websocket)
    await connections.connect(websocket, session_id=session.id, operator_name=session.operator_name)
    try:
        await websocket.send_json(manager.build_snapshot(session_id=session.id))
        await websocket.send_json({"type": "session", "session": db.get_session(session.id), "serverTime": db.get_vcu_state_snapshot().get("updatedAt")})
        while True:
            message = await websocket.receive_json()
            msg_type = str(message.get("type") or "").strip()
            if msg_type in {"ping", "heartbeat"}:
                await websocket.send_json({"type": "pong"})
                continue
            if msg_type in {"session.update", "hello"}:
                operator_name = message.get("operatorName") or message.get("operator")
                session = SessionIdentity(
                    id=message.get("sessionId") or session.id,
                    operator_name=operator_name or session.operator_name,
                    client_ip=session.client_ip,
                    user_agent=session.user_agent,
                )
                updated = manager.touch_session(
                    session.id,
                    operator_name=session.operator_name,
                    client_ip=session.client_ip,
                    user_agent=session.user_agent,
                    metadata={"path": websocket.url.path, "source": "websocket"},
                )
                await connections.update_session(websocket, session_id=session.id, operator_name=updated.get("operatorName"))
                await websocket.send_json({"type": "session", "session": updated})
                continue
            if msg_type in {"imd.confirm", "action.imd_confirm"}:
                manager.confirm_imd(session=session)
                await websocket.send_json({"type": "ack", "action": "imd.confirm"})
                continue
            if msg_type in {"snapshot.get", "bootstrap"}:
                await websocket.send_json(manager.build_snapshot(session_id=session.id))
                continue
            if msg_type in {"action.bootload", "bootload"}:
                try:
                    result = manager.start_bootload(session)
                    await websocket.send_json({"type": "ack", "action": "bootload", **result})
                except OperationRejected as exc:
                    await websocket.send_json({"type": "error", **exc.as_dict()})
                continue
            await websocket.send_json({"type": "error", "code": "UNKNOWN_MESSAGE", "error": f"Unknown websocket message '{msg_type}'"})
    except WebSocketDisconnect:
        await connections.disconnect(websocket)
    except Exception:
        await connections.disconnect(websocket)


# ── Frontend hosting (optional) ──────────────────────────────────────────────


dist_dir = settings.root_dir / "frontend" / "web" / "dist"
if dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="frontend")
else:
    @app.get("/")
    def root() -> PlainTextResponse:
        return PlainTextResponse("VCU Flash backend is running. Build the frontend and place it in frontend/web/dist to serve it here.")
