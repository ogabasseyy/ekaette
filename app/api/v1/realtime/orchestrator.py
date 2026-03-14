"""Realtime websocket task orchestration."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocketDisconnect

from app.api.v1.realtime.live_media_bridge import active_live_media_task
from app.api.v1.realtime.models import SessionInitContext
from app.api.v1.realtime.stream_tasks import (
    create_initial_silence_state,
    downstream_task,
    keepalive_task,
    silence_nudge_task,
    upstream_task,
)

logger = logging.getLogger(__name__)


def _log_background_task_failure(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except Exception:
        logger.error("Background task failure could not be inspected", exc_info=True)
        return
    if exc is not None and not isinstance(exc, WebSocketDisconnect):
        logger.error("Background task %s failed: %s", task.get_name(), exc, exc_info=exc)


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
            silence_nudge_task(live_request_queue, session_alive, silence_state, ctx),
            name="silence_nudge_task",
        )
        nudge.add_done_callback(_log_background_task_failure)
        live_media = asyncio.create_task(
            active_live_media_task(ctx, live_request_queue, session_alive, silence_state),
            name="active_live_media_task",
        )
        live_media.add_done_callback(_log_background_task_failure)
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

        for task in pending | {keepalive, nudge, live_media}:
            task.cancel()
        remaining = pending | {keepalive, nudge, live_media}
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
    except WebSocketDisconnect:
        logger.debug("Client disconnected normally")
    except Exception as e:
        logger.error("Streaming error: %s", e, exc_info=True)
    finally:
        session_alive.clear()
        live_request_queue.close()
