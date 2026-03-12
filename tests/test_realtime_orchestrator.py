from __future__ import annotations

import asyncio
import logging

import pytest


@pytest.mark.asyncio
async def test_log_background_task_failure_logs_exception(caplog: pytest.LogCaptureFixture) -> None:
    from app.api.v1.realtime.orchestrator import _log_background_task_failure

    async def _boom() -> None:
        raise RuntimeError("media bridge exploded")

    task = asyncio.create_task(_boom(), name="active_live_media_task")
    with pytest.raises(RuntimeError):
        await task

    with caplog.at_level(logging.ERROR):
        _log_background_task_failure(task)

    assert "Background task active_live_media_task failed" in caplog.text


@pytest.mark.asyncio
async def test_log_background_task_failure_ignores_cancelled(caplog: pytest.LogCaptureFixture) -> None:
    from app.api.v1.realtime.orchestrator import _log_background_task_failure

    async def _sleep() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(_sleep(), name="active_live_media_task")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with caplog.at_level(logging.ERROR):
        _log_background_task_failure(task)

    assert "Background task" not in caplog.text
