"""Tests for voice call control tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools.call_control_tools import end_call


@pytest.mark.asyncio
async def test_end_call_returns_ok_for_voice_channel():
    ctx = SimpleNamespace(state={"app:channel": "voice"})

    result = await end_call("goodbye_complete", tool_context=ctx)

    assert result["status"] == "ok"
    assert result["action"] == "end_after_speaking"
    assert result["reason"] == "goodbye_complete"


@pytest.mark.asyncio
async def test_end_call_rejects_non_voice_channel():
    ctx = SimpleNamespace(state={"app:channel": "text"})

    result = await end_call("goodbye_complete", tool_context=ctx)

    assert result["status"] == "error"
    assert result["error"] == "not_voice_channel"


@pytest.mark.asyncio
async def test_end_call_reports_existing_request():
    ctx = SimpleNamespace(
        state={
            "app:channel": "voice",
            "temp:call_end_after_speaking_requested": True,
        }
    )

    result = await end_call(tool_context=ctx)

    assert result["status"] == "ok"
    assert result["already_requested"] is True
