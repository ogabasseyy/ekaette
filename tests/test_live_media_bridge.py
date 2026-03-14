from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.realtime.models import SessionInitContext, SilenceState
from app.api.v1.realtime.voice_state_registry import (
    clear_registered_voice_state,
    get_registered_voice_state,
)


class _FakeBlob:
    def __init__(self, *, mime_type: str, data: bytes):
        self.mime_type = mime_type
        self.data = data


class _FakePart:
    def __init__(self, *, text: str):
        self.text = text


class _FakeContent:
    def __init__(self, *, parts: list[_FakePart]):
        self.parts = parts


class _FakeTypes:
    Blob = _FakeBlob
    Part = _FakePart
    Content = _FakeContent


class _FakeLiveRequestQueue:
    def __init__(self) -> None:
        self.realtime: list[_FakeBlob] = []
        self.contents: list[_FakeContent] = []

    def send_realtime(self, blob: _FakeBlob) -> None:
        self.realtime.append(blob)

    def send_content(self, content: _FakeContent) -> None:
        self.contents.append(content)


class _FakeEventRef:
    pass


@pytest.mark.asyncio
async def test_active_live_media_task_delivers_queued_media_while_generic_busy(monkeypatch):
    import app.api.v1.realtime.live_media_bridge as live_media_bridge

    session_alive = asyncio.Event()
    session_alive.set()
    silence_state = SilenceState(
        last_client_activity=0.0,
        silence_nudge_count=0,
        agent_busy=True,
        silence_nudge_due_at=0.0,
        silence_nudge_interval=0.0,
        assistant_output_active=False,
        greeting_lock_active=False,
    )
    ctx = SessionInitContext(
        websocket=SimpleNamespace(),
        user_id="phone-bridge-user",
        resolved_session_id="wa-session-bridge",
        client_ip="127.0.0.1",
        model_name="gemini-live",
        is_native_audio=False,
        industry="electronics",
        session_industry="electronics",
        company_id="ekaette-electronics",
        tenant_id="public",
        requested_template_id=None,
        session_state={},
        session_voice="Aoede",
        manual_vad_active=False,
        run_config=None,
        caller_phone="+2349169449282",
    )
    queue = _FakeLiveRequestQueue()
    delivered_updates = AsyncMock()
    cached_payload: dict[str, object] = {}
    background_start = MagicMock()
    event_ref = object()
    persisted_updates: list[dict[str, object]] = []

    async def _noop(*_args, **_kwargs) -> None:
        return None

    async def _fake_claim_next_pending_media_event(_ctx):
        session_alive.clear()
        return event_ref, {
            "handoff_summary": "Customer wants a swap valuation for an iPhone XR.",
            "media_kind": "video",
            "provenance_text": "The caller sent this media on WhatsApp during the current call.",
        }

    def _fake_cache_latest_image(**kwargs) -> None:
        cached_payload.update(kwargs)

    async def _fake_persist_session_state_updates(*_args, **kwargs) -> None:
        state_updates = dict(kwargs["state_updates"])
        persisted_updates.append(state_updates)
        ctx.session_state.update(state_updates)
        background_status = state_updates.get("temp:background_vision_status")
        if isinstance(background_status, str) and background_status:
            live_media_bridge.update_voice_state(
                user_id=ctx.user_id,
                session_id=ctx.resolved_session_id,
                **{"temp:background_vision_status": background_status},
            )

    monkeypatch.setattr(live_media_bridge, "_feature_enabled", lambda: True)
    monkeypatch.setattr(
        live_media_bridge,
        "bind_runtime_values",
        lambda *_args: (_FakeTypes, AsyncMock(), object(), "ekaette"),
    )
    monkeypatch.setattr(live_media_bridge, "register_active_live_session", _noop)
    monkeypatch.setattr(live_media_bridge, "heartbeat_active_live_session", _noop)
    monkeypatch.setattr(live_media_bridge, "unregister_active_live_session", _noop)
    monkeypatch.setattr(
        live_media_bridge,
        "_claim_next_pending_media_event",
        _fake_claim_next_pending_media_event,
    )
    monkeypatch.setattr(
        live_media_bridge,
        "_load_media_bytes",
        AsyncMock(return_value=(b"video-bytes", "video/mp4")),
    )
    monkeypatch.setattr(live_media_bridge, "_doc_update", delivered_updates)
    monkeypatch.setattr(live_media_bridge, "cache_latest_image", _fake_cache_latest_image)
    monkeypatch.setattr(
        live_media_bridge,
        "_persist_session_state_updates",
        _fake_persist_session_state_updates,
    )
    monkeypatch.setattr(
        live_media_bridge,
        "_start_background_media_analysis",
        background_start,
    )

    await live_media_bridge.active_live_media_task(
        ctx,
        queue,
        session_alive,
        silence_state,
    )

    assert cached_payload == {
        "user_id": "phone-bridge-user",
        "session_id": "wa-session-bridge",
        "image_data": b"video-bytes",
        "mime_type": "video/mp4",
    }
    assert queue.realtime == []
    assert len(queue.contents) == 1
    guidance = queue.contents[0].parts[0].text
    assert "I've got the video, let me check it now." in guidance
    assert "already running in the background" in guidance
    assert "Do NOT transfer to vision_agent" in guidance
    assert "safe non-visual follow-up question" in guidance
    assert "Never ask the caller to describe colour, cracks, scratches, dents" in guidance
    assert "Prior context: Customer wants a swap valuation for an iPhone XR." in guidance
    assert persisted_updates[0]["temp:background_vision_status"] == "running"
    assert persisted_updates[0]["temp:last_media_blob_path"] == ""
    try:
        registry_state = get_registered_voice_state(
            user_id="phone-bridge-user",
            session_id="wa-session-bridge",
        )
        assert registry_state["temp:background_vision_status"] == "running"
        assert silence_state.agent_busy is True
        assert silence_state.assistant_output_active is False
        assert silence_state.awaiting_agent_response is True
        assert silence_state.pending_media_analysis is True
        background_start.assert_called_once()

        delivered_updates.assert_awaited()
        delivered_call = delivered_updates.await_args_list[0]
        assert delivered_call.args[0] is event_ref
        assert delivered_call.args[1]["status"] == "delivered"
    finally:
        clear_registered_voice_state(
            user_id="phone-bridge-user",
            session_id="wa-session-bridge",
        )


@pytest.mark.asyncio
async def test_enqueue_media_logs_when_at_voice_channel_is_disabled(monkeypatch, caplog):
    import app.api.v1.realtime.live_media_bridge as live_media_bridge

    doc_ref = object()

    monkeypatch.setattr(live_media_bridge, "_feature_enabled", lambda: True)
    monkeypatch.setattr(live_media_bridge, "_get_firestore_db", lambda: object())
    monkeypatch.setattr(live_media_bridge, "_session_doc_ref", lambda *_args: doc_ref)
    monkeypatch.setattr(
        live_media_bridge,
        "_doc_get",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        live_media_bridge,
        "_snapshot_dict",
        lambda _snapshot: {
            "status": "active",
            "voice_channel": "at_voice",
            "session_id": "sip-disabled-123",
            "expires_at": live_media_bridge._to_iso(
                live_media_bridge._utc_now() + timedelta(seconds=60)
            ),
        },
    )
    monkeypatch.setattr(live_media_bridge, "_channel_enabled", lambda _channel: False)

    with caplog.at_level(logging.WARNING):
        result = await live_media_bridge.enqueue_media_for_active_live_session(
            from_="+2349169449282",
            tenant_id="public",
            company_id="ekaette-electronics",
            media_bytes=b"video-bytes",
            mime_type="video/mp4",
            media_type="video",
            caption="",
            handoff_context=None,
        )

    assert result is None
    assert "channel is disabled" in caplog.text
    assert "sip-disabled-123" in caplog.text
    assert "at_voice" in caplog.text


@pytest.mark.asyncio
async def test_background_media_analysis_clears_pending_flag_for_current_generation(monkeypatch):
    import app.api.v1.realtime.live_media_bridge as live_media_bridge

    ctx = SessionInitContext(
        websocket=SimpleNamespace(),
        user_id="phone-bridge-user",
        resolved_session_id="wa-session-bridge",
        client_ip="127.0.0.1",
        model_name="gemini-live",
        is_native_audio=False,
        industry="electronics",
        session_industry="electronics",
        company_id="ekaette-electronics",
        tenant_id="public",
        requested_template_id=None,
        session_state={},
        session_voice="Aoede",
        manual_vad_active=False,
        run_config=None,
        caller_phone="+2349169449282",
    )
    silence_state = SilenceState(
        last_client_activity=0.0,
        silence_nudge_count=0,
        agent_busy=True,
        silence_nudge_due_at=0.0,
        silence_nudge_interval=0.0,
        assistant_output_active=False,
        greeting_lock_active=False,
        pending_media_analysis=True,
    )
    persisted_updates: list[dict[str, object]] = []
    doc_updates: list[dict[str, object]] = []

    async def _fake_persist_session_state_updates(*_args, **kwargs) -> None:
        persisted_updates.append(dict(kwargs["state_updates"]))

    async def _fake_doc_update(_event_ref, payload):
        doc_updates.append(dict(payload))

    monkeypatch.setattr(
        live_media_bridge,
        "analyze_device_media",
        AsyncMock(
            return_value={
                "device_name": "iPhone 14",
                "device_color": "red",
                "condition": "good",
            }
        ),
    )
    monkeypatch.setattr(
        live_media_bridge,
        "_persist_session_state_updates",
        _fake_persist_session_state_updates,
    )
    monkeypatch.setattr(live_media_bridge, "_doc_update", _fake_doc_update)

    generation_ref = {"value": 1}
    await live_media_bridge._run_background_media_analysis(
        ctx=ctx,
        event_ref=_FakeEventRef(),
        event_payload={"event_id": "evt-123"},
        media_bytes=b"video-bytes",
        mime_type="video/mp4",
        silence_state=silence_state,
        generation_ref=generation_ref,
        generation=1,
        async_save_session_state_fn=None,
        session_service_obj=None,
        session_app_name="ekaette",
    )

    assert persisted_updates[0]["temp:background_vision_status"] == "ready"
    assert silence_state.pending_media_analysis is False
    assert doc_updates[-1]["analysis_status"] == "ready"


@pytest.mark.asyncio
async def test_background_media_analysis_times_out_with_terminal_state(monkeypatch):
    import app.api.v1.realtime.live_media_bridge as live_media_bridge

    ctx = SessionInitContext(
        websocket=SimpleNamespace(),
        user_id="phone-bridge-user",
        resolved_session_id="wa-session-timeout",
        client_ip="127.0.0.1",
        model_name="gemini-live",
        is_native_audio=False,
        industry="electronics",
        session_industry="electronics",
        company_id="ekaette-electronics",
        tenant_id="public",
        requested_template_id=None,
        session_state={},
        session_voice="Aoede",
        manual_vad_active=False,
        run_config=None,
        caller_phone="+2349169449282",
    )
    silence_state = SilenceState(
        last_client_activity=0.0,
        silence_nudge_count=0,
        agent_busy=True,
        silence_nudge_due_at=0.0,
        silence_nudge_interval=0.0,
        assistant_output_active=False,
        greeting_lock_active=False,
        pending_media_analysis=True,
    )
    persisted_updates: list[dict[str, object]] = []
    doc_updates: list[dict[str, object]] = []

    async def _slow_analysis(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return {"device_name": "iPhone 14", "condition": "good"}

    async def _fake_persist_session_state_updates(*_args, **kwargs) -> None:
        persisted_updates.append(dict(kwargs["state_updates"]))

    async def _fake_doc_update(_event_ref, payload):
        doc_updates.append(dict(payload))

    monkeypatch.setattr(live_media_bridge, "analyze_device_media", _slow_analysis)
    monkeypatch.setattr(live_media_bridge, "_background_analysis_timeout_seconds", lambda: 0.01)
    monkeypatch.setattr(
        live_media_bridge,
        "_persist_session_state_updates",
        _fake_persist_session_state_updates,
    )
    monkeypatch.setattr(live_media_bridge, "_doc_update", _fake_doc_update)

    generation_ref = {"value": 1}
    await live_media_bridge._run_background_media_analysis(
        ctx=ctx,
        event_ref=_FakeEventRef(),
        event_payload={"event_id": "evt-timeout"},
        media_bytes=b"video-bytes",
        mime_type="video/mp4",
        silence_state=silence_state,
        generation_ref=generation_ref,
        generation=1,
        async_save_session_state_fn=None,
        session_service_obj=None,
        session_app_name="ekaette",
    )

    assert persisted_updates[0]["temp:background_vision_status"] == "failed"
    assert silence_state.pending_media_analysis is False
    assert doc_updates[-1]["analysis_status"] == "timeout"
    assert doc_updates[-1]["analysis_error"] == "timeout"


@pytest.mark.asyncio
async def test_background_media_analysis_uses_live_media_model_candidates(monkeypatch):
    import app.api.v1.realtime.live_media_bridge as live_media_bridge

    ctx = SessionInitContext(
        websocket=SimpleNamespace(),
        user_id="phone-bridge-user",
        resolved_session_id="wa-session-live-model",
        client_ip="127.0.0.1",
        model_name="gemini-live",
        is_native_audio=False,
        industry="electronics",
        session_industry="electronics",
        company_id="ekaette-electronics",
        tenant_id="public",
        requested_template_id=None,
        session_state={},
        session_voice="Aoede",
        manual_vad_active=False,
        run_config=None,
        caller_phone="+2349169449282",
    )
    silence_state = SilenceState(
        last_client_activity=0.0,
        silence_nudge_count=0,
        agent_busy=True,
        silence_nudge_due_at=0.0,
        silence_nudge_interval=0.0,
        assistant_output_active=False,
        greeting_lock_active=False,
        pending_media_analysis=True,
    )
    captured: dict[str, object] = {}

    async def _fake_analyze_device_media(*, media_data, mime_type, model_candidates=None):
        captured["media_data"] = media_data
        captured["mime_type"] = mime_type
        captured["model_candidates"] = list(model_candidates or [])
        return {"device_name": "iPhone XR", "device_color": "red", "condition": "Good"}

    async def _fake_persist_session_state_updates(*_args, **_kwargs) -> None:
        return None

    async def _fake_doc_update(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(live_media_bridge, "analyze_device_media", _fake_analyze_device_media)
    monkeypatch.setattr(
        live_media_bridge,
        "get_live_media_vision_model_candidates",
        lambda: ["gemini-2.5-pro"],
    )
    monkeypatch.setattr(
        live_media_bridge,
        "_persist_session_state_updates",
        _fake_persist_session_state_updates,
    )
    monkeypatch.setattr(live_media_bridge, "_doc_update", _fake_doc_update)

    generation_ref = {"value": 1}
    await live_media_bridge._run_background_media_analysis(
        ctx=ctx,
        event_ref=_FakeEventRef(),
        event_payload={"event_id": "evt-live-model"},
        media_bytes=b"video-bytes",
        mime_type="video/mp4",
        silence_state=silence_state,
        generation_ref=generation_ref,
        generation=1,
        async_save_session_state_fn=None,
        session_service_obj=None,
        session_app_name="ekaette",
    )

    assert captured["media_data"] == b"video-bytes"
    assert captured["mime_type"] == "video/mp4"
    assert captured["model_candidates"] == ["gemini-2.5-pro"]
