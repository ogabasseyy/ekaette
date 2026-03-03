"""Realtime websocket task orchestration."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocketDisconnect

from app.api.v1.realtime.models import SessionInitContext
from app.api.v1.realtime.stream_tasks import (
    create_initial_silence_state,
    downstream_task,
    keepalive_task,
    silence_nudge_task,
    upstream_task,
)

logger = logging.getLogger(__name__)


async def run_stream_loop(ctx: SessionInitContext, live_request_queue) -> None:
    """Run upstream/downstream/keepalive/silence tasks until one stream side ends."""
    session_alive = asyncio.Event()
    session_alive.set()
    silence_state = create_initial_silence_state()

    try:
        upstream = asyncio.create_task(
            upstream_task(ctx, live_request_queue, session_alive, silence_state),
            name="upstream_task",
        )
        downstream = asyncio.create_task(
            downstream_task(ctx, live_request_queue, session_alive, silence_state),
            name="downstream_task",
        )
        keepalive = asyncio.create_task(
            keepalive_task(ctx.websocket, session_alive),
            name="keepalive_task",
        )
        nudge = asyncio.create_task(
            silence_nudge_task(live_request_queue, session_alive, silence_state),
            name="silence_nudge_task",
        )
        streaming_tasks = {upstream, downstream}

        # The bidi session should end when either streaming side finishes:
        # if downstream ends naturally (for example, Live session ends), we
        # must cancel upstream rather than waiting forever for client input.
        done, pending = await asyncio.wait(
            streaming_tasks, return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc

        for task in pending | {keepalive, nudge}:
            task.cancel()
        remaining = pending | {keepalive, nudge}
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
    except WebSocketDisconnect:
        logger.debug("Client disconnected normally")
    except Exception as e:
        logger.error("Streaming error: %s", e, exc_info=True)
    finally:
        session_alive.clear()
        live_request_queue.close()
