"""Cloud Run entrypoint for the dedicated live-audio websocket service.

This service isolates long-lived realtime websocket traffic from the
short-lived AT/HTTP ingress service so telephony webhooks remain responsive.
"""

from __future__ import annotations

from fastapi import FastAPI, WebSocket

import main as full_main

app = FastAPI(title="Ekaette Live")


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check for the dedicated realtime service."""
    return {"status": "ok", "app": "ekaette-live", "mode": "realtime"}


@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """Dedicated websocket endpoint for SIP/WA gateway traffic."""
    full_main._sync_realtime_runtime()
    await full_main.realtime_ws.websocket_endpoint(websocket, user_id, session_id)
