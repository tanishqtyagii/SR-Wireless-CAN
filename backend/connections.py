from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from backend.utils import safe_json


@dataclass(slots=True)
class ClientConnection:
    websocket: WebSocket
    session_id: str | None = None
    operator_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, ClientConnection] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        *,
        session_id: str | None = None,
        operator_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ClientConnection:
        await websocket.accept()
        client = ClientConnection(
            websocket=websocket,
            session_id=session_id,
            operator_name=operator_name,
            metadata=metadata or {},
        )
        async with self._lock:
            self._connections[id(websocket)] = client
        return client

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(id(websocket), None)

    async def update_session(self, websocket: WebSocket, *, session_id: str | None, operator_name: str | None) -> None:
        async with self._lock:
            client = self._connections.get(id(websocket))
            if client is not None:
                client.session_id = session_id
                client.operator_name = operator_name

    async def connection_count(self) -> int:
        async with self._lock:
            return len(self._connections)

    async def _send_one(self, client: ClientConnection, payload: dict[str, Any]) -> bool:
        try:
            await client.websocket.send_json(safe_json(payload))
            return True
        except Exception:
            return False

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._connections.values())
        if not clients:
            return
        dead: list[WebSocket] = []
        for client in clients:
            ok = await self._send_one(client, payload)
            if not ok:
                dead.append(client.websocket)
        if dead:
            async with self._lock:
                for websocket in dead:
                    self._connections.pop(id(websocket), None)

    async def send_to_session(self, session_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = [client for client in self._connections.values() if client.session_id == session_id]
        dead: list[WebSocket] = []
        for client in clients:
            ok = await self._send_one(client, payload)
            if not ok:
                dead.append(client.websocket)
        if dead:
            async with self._lock:
                for websocket in dead:
                    self._connections.pop(id(websocket), None)
