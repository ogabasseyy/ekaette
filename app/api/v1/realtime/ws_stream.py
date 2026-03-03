"""Realtime websocket stream entrypoint extracted from main.py.

This module keeps the stable public surface used by main.py while delegating
session initialization and streaming tasks to focused submodules.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

from app.api.v1.realtime.orchestrator import run_stream_loop
from app.api.v1.realtime.runtime_cache import configure_runtime as configure_runtime_cache
from app.api.v1.realtime.session_init import (
    configure_runtime as configure_session_runtime,
    initialize_session,
)
from app.api.v1.realtime.stream_tasks import (
    configure_runtime as configure_stream_runtime,
)

logger = logging.getLogger(__name__)


def configure_runtime(**kwargs: Any) -> None:
    """Inject runtime dependencies from main module.

    main.py calls this before each websocket request so test monkeypatches
    applied on main symbols remain effective.
    """
    globals().update(kwargs)
    configure_runtime_cache(**kwargs)
    configure_session_runtime(**kwargs)
    configure_stream_runtime(**kwargs)


async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """WebSocket endpoint for bidirectional streaming with ADK."""
    session_ctx = await initialize_session(websocket, user_id, session_id)
    if session_ctx is None:
        return

    live_request_queue = LiveRequestQueue()
    await run_stream_loop(session_ctx, live_request_queue)
