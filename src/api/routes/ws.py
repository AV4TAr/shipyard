"""WebSocket endpoint for real-time events and activity feed REST endpoint."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for conn in dead:
            self.disconnect(conn)


manager = ConnectionManager()

# The dispatcher is wired in by the app factory via ``set_dispatcher``.
_dispatcher: Any = None


def set_dispatcher(dispatcher: Any) -> None:
    """Called once at startup to wire the EventDispatcher into this module."""
    global _dispatcher  # noqa: PLW0603
    _dispatcher = dispatcher
    dispatcher.add_live_listener(_broadcast_event)


async def _broadcast_event(event_dict: dict[str, Any]) -> None:
    """Live-listener callback: relay every dispatched event to WS clients."""
    await manager.broadcast(event_dict)


@router.get("/api/activity")
def get_activity(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent activity events for initial page load."""
    if _dispatcher is None:
        return []
    return _dispatcher.get_recent_events(min(limit, 200))


@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket that sends heartbeat events and manages connections."""
    await manager.connect(websocket)
    try:
        while True:
            # Send heartbeat every 30 seconds
            await websocket.send_json({"type": "heartbeat", "status": "ok"})
            # Wait for client message or timeout
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo back any client messages as acknowledgements
                await websocket.send_json({"type": "ack", "data": data})
            except asyncio.TimeoutError:
                # No client message — just continue heartbeat loop
                continue
    except WebSocketDisconnect:
        manager.disconnect(websocket)
