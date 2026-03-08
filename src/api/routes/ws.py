"""WebSocket endpoint for real-time events."""

from __future__ import annotations

import asyncio

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
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        for connection in self.active_connections:
            await connection.send_json(message)


manager = ConnectionManager()


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
