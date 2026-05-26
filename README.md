# VCU FastAPI WebSocket Backend

This package replaces the old `runner.py` flow with a long-running FastAPI backend that keeps VCU state, flash history, sessions, uploads, and live progress in SQLite and broadcasts live updates over WebSockets.

## What is included

- `main.py` – FastAPI app with REST + WebSocket endpoints
- `backend/` – operation manager, session logic, concurrency control, progress mapping, hardware/simulated flash orchestration
- `db/` – upgraded SQLite schema for sessions, files, history, logs, and global VCU state
- `latest/` – your existing flashing stack, reused by the backend in hardware mode
- `frontend/web/` – your current frontend source, copied in for reference/integration

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Docker

This package now includes a one-container Docker setup that builds the React frontend and serves it from the same FastAPI container.

### Simulated mode (no CAN hardware)

```bash
docker compose up --build
```

Then open:

- `http://localhost:8000` – frontend
- `http://localhost:8000/api/health` – backend health

Simulation mode is the default in `docker-compose.yml`, so bootload / boot+flash / flash-only can be tested without a CAN adapter.

### Hardware mode (real CAN / VCU)

If the host already has a working `can0` interface:

```bash
docker compose -f docker-compose.yml -f docker-compose.hardware.yml up --build
```

That override switches `FLASH_SIMULATE=0`, uses host networking, and adds raw/network capabilities so the container can open SocketCAN on the host.

### Persisted data

The compose setup bind-mounts `./db` into `/app/db`, so uploaded HEX files, flash history, sessions, and logs persist across restarts.

Default mode is `FLASH_SIMULATE=1` so the backend is testable without CAN hardware.
Set `FLASH_SIMULATE=0` on the device to use the real VCU flow.

## Main behavior

- real-time multi-client updates over `/ws` or `/api/ws`
- server-side session tracking with operator name + session cookie
- first flash request wins; concurrent flash requests are rejected
- 5-second reflash priority after a flash attempt completes
- bootload is universal, but still serialized
- `flash_kernel.py` always runs before flashing
- `finalization.py` always runs after every flash
- `flash-only` is only allowed when the VCU state is `bootloaded`
- `bootload` is blocked once the VCU is already `bootloaded`
- live progress, phase, IMD wait, and power-cycle state are persisted and broadcast

## Important endpoints

- `GET /api/bootstrap` – full initial snapshot for a client
- `GET /api/vcu-state` – current persisted VCU state
- `POST /api/session` – register/update operator name for the session
- `POST /api/bootload`
- `POST /api/boot-and-flash`
- `POST /api/flash-only`
- `POST /api/imd-confirm`
- `GET /api/hex-files`
- `POST /api/hex-files/upload`
- `GET /api/flash-history`
- `GET /api/flash-history/{id}/logs`

## WebSocket payloads

The server sends:

- `snapshot` – full current state/history/files
- `delta` – incremental updates with any of:
  - `state`
  - `historyEntry`
  - `file`
  - `log`
  - `error`
- `imd.confirmed`
- `session`
- `pong`

Client messages supported:

- `{ "type": "hello", "operatorName": "Alice" }`
- `{ "type": "session.update", "operatorName": "Alice" }`
- `{ "type": "snapshot.get" }`
- `{ "type": "imd.confirm" }`
- `{ "type": "action.bootload" }`
- `{ "type": "ping" }`

## Database fields added

Files now persist:

- upload time
- last flashed time
- uploaded by / flashed by
- file size
- file notes
- actual stored file name
- flash count
- CRC32 placeholder field
- SHA-256
- success/failure status

History now persists:

- action
- phase
- progress percent
- duration
- operator/session
- logs in append-only rows
- error/result metadata

## Notes

- The current frontend source is still polling-based. The backend is already ready for live WebSocket consumption.
- If you build the frontend into `frontend/web/dist`, the backend will serve it from `/` automatically.
