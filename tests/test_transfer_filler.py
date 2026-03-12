"""Tests for transfer filler: response latency watchdog and channel-aware nudges."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect

from app.api.v1.realtime.models import SessionInitContext, SilenceState


class TestSilenceStateResponseLatencyFields:
    """Step 3: SilenceState has response latency tracking fields."""

    def test_defaults(self):
        state = SilenceState(
            last_client_activity=0.0,
            silence_nudge_count=0,
            agent_busy=False,
            silence_nudge_due_at=0.0,
            silence_nudge_interval=8.0,
        )
        assert state.awaiting_agent_response is False
        assert state.user_spoke_at == 0.0
        assert state.response_nudge_count == 0

    def test_can_set_awaiting_agent_response(self):
        state = SilenceState(
            last_client_activity=0.0,
            silence_nudge_count=0,
            agent_busy=False,
            silence_nudge_due_at=0.0,
            silence_nudge_interval=8.0,
        )
        state.awaiting_agent_response = True
        state.user_spoke_at = time.monotonic()
        state.response_nudge_count = 0
        assert state.awaiting_agent_response is True

    def test_response_nudge_count_independent_of_silence_nudge_count(self):
        """response_nudge_count and silence_nudge_count are separate fields."""
        state = SilenceState(
            last_client_activity=0.0,
            silence_nudge_count=2,
            agent_busy=False,
            silence_nudge_due_at=0.0,
            silence_nudge_interval=8.0,
        )
        state.response_nudge_count = 1
        assert state.silence_nudge_count == 2
        assert state.response_nudge_count == 1


class TestVoiceSupplementInstruction:
    """Step 8: _VOICE_SUPPLEMENT contains mandatory filler instruction."""

    def test_voice_supplement_contains_mandatory_filler(self):
        from app.agents.ekaette_router.agent import _VOICE_SUPPLEMENT

        assert "MANDATORY FILLER" in _VOICE_SUPPLEMENT

    def test_voice_supplement_mentions_transfer(self):
        from app.agents.ekaette_router.agent import _VOICE_SUPPLEMENT

        assert "transfer" in _VOICE_SUPPLEMENT.lower()

    def test_voice_supplement_includes_nigerian_english_style(self):
        from app.agents.ekaette_router.agent import _VOICE_SUPPLEMENT

        assert "Nigerian English" in _VOICE_SUPPLEMENT
        assert "Pidgin" in _VOICE_SUPPLEMENT


class TestChannelGatingInCallbacks:
    """Step 2: Latency policy gated on app:channel == 'voice'."""

    @pytest.mark.asyncio
    async def test_injects_latency_policy_for_voice_channel(self):
        from google.adk.models.llm_request import LlmRequest

        from app.agents.callbacks import before_model_inject_config

        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {"name": "Electronics", "greeting": "Hi!"},
                "app:company_profile": {"name": "Test Co"},
                "app:channel": " Voice ",
                "temp:greeted": True,
            },
            agent_name="catalog_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])
        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "VOICE STYLE" in system_instruction
        assert "Nigerian English" in system_instruction
        assert "CRITICAL latency policy" in system_instruction
        assert "tool call or agent transfer" in system_instruction

    @pytest.mark.asyncio
    async def test_no_latency_policy_for_text_channel(self):
        from google.adk.models.llm_request import LlmRequest

        from app.agents.callbacks import before_model_inject_config

        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {"name": "Electronics", "greeting": "Hi!"},
                "app:company_profile": {"name": "Test Co"},
                "app:channel": "text",
                "temp:greeted": True,
            },
            agent_name="catalog_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])
        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction or "")
        assert "latency policy" not in system_instruction.lower()
        assert "CRITICAL" not in system_instruction

    @pytest.mark.asyncio
    async def test_no_latency_policy_when_channel_absent(self):
        """Pre-existing sessions without app:channel should NOT get filler."""
        from google.adk.models.llm_request import LlmRequest

        from app.agents.callbacks import before_model_inject_config

        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {"name": "Electronics", "greeting": "Hi!"},
                "app:company_profile": {"name": "Test Co"},
                "temp:greeted": True,
                # NOTE: no "app:channel" key
            },
            agent_name="catalog_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])
        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction or "")
        assert "CRITICAL latency policy" not in system_instruction


class TestTransferHandoffInjection:
    @pytest.mark.asyncio
    async def test_transfer_persists_continuation_context_for_new_agent(self):
        user_turn = _make_live_event(
            input_transcription=SimpleNamespace(
                text="I want the iPhone 15 Pro 128GB.",
                finished=True,
            )
        )
        transfer = _make_live_event(
            actions=SimpleNamespace(transfer_to_agent="catalog_agent", state_delta=None)
        )
        ctx = _make_ctx(_FakeWebSocket())

        await _run_downstream_events(user_turn, transfer, ctx=ctx)

        assert ctx.session_state["temp:pending_handoff_target_agent"] == "catalog_agent"
        assert ctx.session_state["temp:pending_handoff_latest_user"] == (
            "I want the iPhone 15 Pro 128GB."
        )
        assert "iPhone 15 Pro 128GB" in ctx.session_state["temp:pending_handoff_recent_customer_context"]

    @pytest.mark.asyncio
    async def test_finished_user_transcription_persists_last_user_turn(self):
        ctx = _make_ctx(_FakeWebSocket())

        await _run_downstream_events(
            _make_live_event(
                input_transcription=SimpleNamespace(
                    text="I need the iPhone 14 128GB.",
                    finished=True,
                )
            ),
            ctx=ctx,
        )

        assert ctx.session_state["temp:last_user_turn"] == "I need the iPhone 14 128GB."
        assert "iPhone 14 128GB" in ctx.session_state["temp:recent_customer_context"]

    @pytest.mark.asyncio
    async def test_first_transferred_agent_final_clears_pending_handoff_state(self):
        ctx = _make_ctx(_FakeWebSocket())
        ctx.session_state.update(
            {
                "temp:active_agent": "catalog_agent",
                "temp:pending_handoff_target_agent": "catalog_agent",
                "temp:pending_handoff_latest_user": "I want the iPhone 14 128GB.",
                "temp:pending_handoff_latest_agent": "Sure, let me connect you.",
                "temp:pending_handoff_recent_customer_context": (
                    "  Customer: I want the iPhone 14 128GB."
                ),
            }
        )

        await _run_downstream_events(
            _make_live_event(
                output_transcription=SimpleNamespace(
                    text="We have that in stock.",
                    finished=True,
                )
            ),
            ctx=ctx,
        )

        assert ctx.session_state["temp:pending_handoff_target_agent"] == ""
        assert ctx.session_state["temp:pending_handoff_latest_user"] == ""

    @pytest.mark.asyncio
    async def test_transfer_event_with_first_agent_output_clears_pending_handoff(self):
        ctx = _make_ctx(_FakeWebSocket())
        ctx.session_state.update(
            {
                "temp:pending_handoff_target_agent": "catalog_agent",
                "temp:pending_handoff_latest_user": "I want the iPhone 14 128GB.",
                "temp:pending_handoff_latest_agent": "Sure, let me connect you.",
                "temp:pending_handoff_recent_customer_context": (
                    "  Customer: I want the iPhone 14 128GB."
                ),
            }
        )

        await _run_downstream_events(
            _make_live_event(
                output_transcription=SimpleNamespace(
                    text="Which storage size do you want?",
                    finished=True,
                ),
                actions=SimpleNamespace(
                    transfer_to_agent="catalog_agent",
                    state_delta=None,
                ),
            ),
            ctx=ctx,
        )

        assert ctx.session_state["temp:active_agent"] == "catalog_agent"
        assert ctx.session_state["temp:pending_handoff_target_agent"] == ""


class TestVoiceCallbackIntentRegistration:
    @pytest.mark.asyncio
    async def test_finished_user_transcription_queues_callback_request(self):
        ctx = _make_ctx(_FakeWebSocket())
        ctx.session_state.update(
            {
                "app:channel": "voice",
                "user:caller_phone": "+2348012345678",
            }
        )

        with patch(
            "app.api.v1.at.service_voice.register_callback_request"
        ) as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            await _run_downstream_events(
                _make_live_event(
                    input_transcription=SimpleNamespace(
                        text="Can you call me back please?",
                        finished=True,
                    )
                ),
                ctx=ctx,
            )

        mock_register.assert_called_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="acme-co",
            source="voice_user_callback_intent",
            reason="Can you call me back please?",
            trigger_after_hangup=True,
        )
        assert ctx.session_state["temp:callback_requested"] is True

    @pytest.mark.asyncio
    async def test_unrelated_user_transcription_does_not_queue_callback(self):
        ctx = _make_ctx(_FakeWebSocket())
        ctx.session_state.update(
            {
                "app:channel": "voice",
                "user:caller_phone": "+2348012345678",
            }
        )

        with patch(
            "app.api.v1.at.service_voice.register_callback_request"
        ) as mock_register:
            await _run_downstream_events(
                _make_live_event(
                    input_transcription=SimpleNamespace(
                        text="I want the iPhone 14 128GB.",
                        finished=True,
                    )
                ),
                ctx=ctx,
            )

        mock_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_noisy_user_transcription_still_queues_callback_request(self):
        ctx = _make_ctx(_FakeWebSocket())
        ctx.session_state.update(
            {
                "app:channel": "voice",
                "user:caller_phone": "+2348012345678",
            }
        )

        with patch(
            "app.api.v1.at.service_voice.register_callback_request"
        ) as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            await _run_downstream_events(
                _make_live_event(
                    input_transcription=SimpleNamespace(
                        text="you call me bug.",
                        finished=True,
                    )
                ),
                ctx=ctx,
            )

        mock_register.assert_called_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="acme-co",
            source="voice_user_callback_intent",
            reason="you call me bug.",
            trigger_after_hangup=True,
        )
        assert ctx.session_state["temp:callback_requested"] is True


# ─── Stream tasks: watchdog arming / clearing / nudge ──────────────


def _make_silence_state(**overrides) -> SilenceState:
    defaults = dict(
        last_client_activity=time.monotonic(),
        silence_nudge_count=0,
        agent_busy=False,
        silence_nudge_due_at=time.monotonic() + 999,
        silence_nudge_interval=8.0,
    )
    defaults.update(overrides)
    return SilenceState(**defaults)


def _inject_stream_tasks_globals():
    """Inject the runtime globals that stream_tasks expects."""
    import app.api.v1.realtime.stream_tasks as st

    st.SILENCE_NUDGE_SECONDS = 8
    st.SILENCE_NUDGE_MAX = 2
    st.SILENCE_NUDGE_BACKOFF_MULTIPLIER = 1.5
    st.SILENCE_NUDGE_MAX_INTERVAL_SECONDS = 30
    return st


def _configure_downstream_runtime(*events):
    import app.api.v1.realtime.stream_tasks as st

    st.configure_runtime(
        runner=_FakeRunner(events),
        _extract_server_message_from_state_delta=lambda delta: None,
        _usage_int=lambda *args: 0,
        TOKEN_PRICE_PROMPT_PER_MILLION=0.0,
        TOKEN_PRICE_COMPLETION_PER_MILLION=0.0,
        DEBUG_TELEMETRY=False,
        _sanitize_log=lambda value: value,
    )
    return st


class _FakeTypes:
    """Minimal stand-in for google.genai.types used in nudge task."""

    class Blob:
        def __init__(self, mime_type="", data=b""):
            self.mime_type = mime_type
            self.data = data

    class Content:
        def __init__(self, parts=None):
            self.parts = parts or []

    class Part:
        def __init__(self, text=""):
            self.text = text


class _FakeRequestQueue:
    """Records send_content calls for assertion."""

    def __init__(self):
        self.sent: list = []
        self.realtime: list = []
        self.activity_start_calls = 0
        self.activity_end_calls = 0

    def send_content(self, content):
        self.sent.append(content)

    def send_realtime(self, blob):
        self.realtime.append(blob)

    def send_activity_start(self):
        self.activity_start_calls += 1

    def send_activity_end(self):
        self.activity_end_calls += 1


class _SignalRequestQueue(_FakeRequestQueue):
    """Records the first emitted nudge so tests can await it explicitly."""

    def __init__(self):
        super().__init__()
        self.sent_event = asyncio.Event()

    def send_content(self, content):
        super().send_content(content)
        self.sent_event.set()


@contextlib.asynccontextmanager
async def _run_nudge_task(st, silence_state: SilenceState):
    queue = _SignalRequestQueue()
    session_alive = asyncio.Event()
    session_alive.set()
    original_bind = st.bind_runtime_values

    def fake_bind(*names):
        if names == ("types",):
            return (_FakeTypes,)
        return original_bind(*names)

    st.bind_runtime_values = fake_bind
    task = asyncio.create_task(st.silence_nudge_task(queue, session_alive, silence_state))
    try:
        yield queue
    finally:
        session_alive.clear()
        task.cancel()
        task_results = await asyncio.gather(task, return_exceptions=True)
        task_error = task_results[0] if task_results else None
        if isinstance(task_error, BaseException) and not isinstance(
            task_error, asyncio.CancelledError,
        ):
            raise task_error
        st.bind_runtime_values = original_bind


class _FakeWebSocket:
    """Simple websocket stand-in for upstream/downstream unit tests."""

    def __init__(self, incoming=None):
        self._incoming = list(incoming or [])
        self.sent_texts: list[dict] = []
        self.sent_bytes: list[bytes] = []
        self.client = SimpleNamespace(host="127.0.0.1")

    async def receive(self):
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, payload: str):
        self.sent_texts.append(json.loads(payload))

    async def send_bytes(self, payload: bytes):
        self.sent_bytes.append(payload)


class _FakeRunner:
    def __init__(self, events):
        self._events = list(events)

    async def run_live(self, **kwargs):
        for event in self._events:
            yield event
        await asyncio.sleep(0)


class _RetryableFailureRunner:
    def __init__(self, events):
        self._events = list(events)
        self.call_count = 0

    async def run_live(self, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            inner = RuntimeError(
                "received 1011 (internal error) The service is currently unavailable.; "
                "then sent 1011 (internal error) The service is currently unavailable."
            )
            raise RuntimeError("1011 None. The service is currently unavailable.") from inner
        for event in self._events:
            yield event
        await asyncio.sleep(0)


class _IntermittentRetryRunner:
    def __init__(self, recovered_events, final_events):
        self._recovered_events = list(recovered_events)
        self._final_events = list(final_events)
        self.call_count = 0

    async def run_live(self, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            inner = RuntimeError(
                "received 1011 (internal error) The service is currently unavailable.; "
                "then sent 1011 (internal error) The service is currently unavailable."
            )
            raise RuntimeError("1011 None. The service is currently unavailable.") from inner
        if self.call_count == 2:
            for event in self._recovered_events:
                yield event
            inner = RuntimeError(
                "received 1011 (internal error) The service is currently unavailable.; "
                "then sent 1011 (internal error) The service is currently unavailable."
            )
            raise RuntimeError("1011 None. The service is currently unavailable.") from inner
        for event in self._final_events:
            yield event
        await asyncio.sleep(0)


def _make_live_event(
    *,
    content=None,
    input_transcription=None,
    output_transcription=None,
    interrupted: bool = False,
    actions=None,
    turn_complete: bool = False,
    usage_metadata=None,
    live_session_resumption_update=None,
    author: str = "ekaette_router",
):
    if actions is None:
        actions = SimpleNamespace(transfer_to_agent=None, state_delta=None)
    return SimpleNamespace(
        content=content,
        input_transcription=input_transcription,
        output_transcription=output_transcription,
        interrupted=interrupted,
        actions=actions,
        turn_complete=turn_complete,
        usage_metadata=usage_metadata,
        live_session_resumption_update=live_session_resumption_update,
        author=author,
    )


def _make_content_event(
    *,
    text: str | None = None,
    audio_bytes: bytes | None = None,
    mime_type: str = "audio/pcm",
    turn_complete: bool = False,
):
    inline_data = None
    if audio_bytes is not None:
        inline_data = SimpleNamespace(data=audio_bytes, mime_type=mime_type)
    part = SimpleNamespace(text=text, inline_data=inline_data)
    return _make_live_event(
        content=SimpleNamespace(parts=[part]),
        turn_complete=turn_complete,
    )


def _make_ctx(
    websocket,
    *,
    is_native_audio: bool = True,
    manual_vad_active: bool = False,
) -> SessionInitContext:
    return SessionInitContext(
        websocket=websocket,
        user_id="user-1",
        resolved_session_id="session-1",
        client_ip="127.0.0.1",
        model_name="gemini-live-2.5-flash-preview-native-audio",
        is_native_audio=is_native_audio,
        industry="electronics",
        session_industry="electronics",
        company_id="acme-co",
        tenant_id="public",
        requested_template_id=None,
        session_state={},
        session_voice="Aoede",
        manual_vad_active=manual_vad_active,
        run_config=SimpleNamespace(),
    )


async def _run_downstream_events(
    *events,
    ctx: SessionInitContext | None = None,
    silence_state: SilenceState | None = None,
):
    st = _configure_downstream_runtime(*events)
    if ctx is None:
        ctx = _make_ctx(_FakeWebSocket())
    if silence_state is None:
        silence_state = _make_silence_state()
    queue = _FakeRequestQueue()
    session_alive = asyncio.Event()
    session_alive.set()
    await st.downstream_task(ctx, queue, session_alive, silence_state)
    return ctx.websocket, queue, silence_state


class TestWatchdogArmingInDownstream:
    """Verify watchdog arms on accepted transcription and NOT on suppressed late partials."""

    @pytest.mark.asyncio
    async def test_accepted_partial_arms_watchdog(self):
        """An accepted partial transcription should arm the watchdog."""
        websocket, _, ss = await _run_downstream_events(
            _make_live_event(
                input_transcription=SimpleNamespace(text="I need pricing", finished=False)
            )
        )

        assert ss.awaiting_agent_response is True
        assert ss.user_spoke_at > 0
        assert ss.response_nudge_count == 0
        user_transcripts = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "transcription" and msg.get("role") == "user"
        ]
        assert user_transcripts == [{
            "type": "transcription",
            "role": "user",
            "text": "I need pricing",
            "partial": True,
        }]

    @pytest.mark.asyncio
    async def test_accepted_finished_arms_watchdog(self):
        """An accepted finished=True transcription should arm the watchdog."""
        websocket, _, ss = await _run_downstream_events(
            _make_live_event(
                input_transcription=SimpleNamespace(text="I need pricing", finished=True)
            )
        )

        assert ss.awaiting_agent_response is True
        assert ss.user_spoke_at > 0
        assert ss.response_nudge_count == 0
        user_transcripts = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "transcription" and msg.get("role") == "user"
        ]
        assert user_transcripts == [{
            "type": "transcription",
            "role": "user",
            "text": "I need pricing",
            "partial": False,
        }]

    @pytest.mark.asyncio
    async def test_suppressed_late_partial_does_not_arm(self):
        """Late partials (input_finalized=True, finished=False) must NOT arm watchdog."""
        websocket, _, ss = await _run_downstream_events(
            _make_live_event(
                input_transcription=SimpleNamespace(text="I need pricing", finished=True)
            ),
            _make_live_event(
                input_transcription=SimpleNamespace(text="I need", finished=False)
            ),
        )

        user_transcripts = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "transcription" and msg.get("role") == "user"
        ]
        assert len(user_transcripts) == 1
        assert user_transcripts[0]["text"] == "I need pricing"
        assert ss.awaiting_agent_response is True
        assert ss.response_nudge_count == 0


class TestDownstreamRetry:
    """Transient Live API errors should be retried in-process."""

    def test_parse_env_helpers_fallback_to_defaults(self, monkeypatch):
        import app.api.v1.realtime.stream_tasks as st

        original_retry_max = os.environ.get("LIVE_STREAM_MAX_RETRIES")
        original_retry_base = os.environ.get("LIVE_STREAM_RETRY_BASE_SECONDS")
        monkeypatch.setenv("LIVE_STREAM_MAX_RETRIES", "oops")
        monkeypatch.setenv("LIVE_STREAM_RETRY_BASE_SECONDS", "bad")
        importlib.reload(st)

        try:
            assert st.LIVE_STREAM_MAX_RETRIES == 2
            assert st.LIVE_STREAM_RETRY_BASE_SECONDS == 0.5
        finally:
            if original_retry_max is None:
                monkeypatch.delenv("LIVE_STREAM_MAX_RETRIES", raising=False)
            else:
                monkeypatch.setenv("LIVE_STREAM_MAX_RETRIES", original_retry_max)
            if original_retry_base is None:
                monkeypatch.delenv("LIVE_STREAM_RETRY_BASE_SECONDS", raising=False)
            else:
                monkeypatch.setenv("LIVE_STREAM_RETRY_BASE_SECONDS", original_retry_base)
            importlib.reload(st)

    @pytest.mark.asyncio
    async def test_retryable_live_error_retries_stream(self, monkeypatch):
        import app.api.v1.realtime.stream_tasks as st

        runner = _RetryableFailureRunner([_make_content_event(audio_bytes=b"\x00\x01")])
        st.configure_runtime(
            runner=runner,
            _extract_server_message_from_state_delta=lambda delta: None,
            _usage_int=lambda *args: 0,
            TOKEN_PRICE_PROMPT_PER_MILLION=0.0,
            TOKEN_PRICE_COMPLETION_PER_MILLION=0.0,
            DEBUG_TELEMETRY=False,
            _sanitize_log=lambda value: value,
        )
        monkeypatch.setattr(st, "LIVE_STREAM_MAX_RETRIES", 1)
        monkeypatch.setattr(st, "LIVE_STREAM_RETRY_BASE_SECONDS", 0.01)

        websocket = _FakeWebSocket()
        ctx = _make_ctx(websocket)
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()
        silence_state = _make_silence_state()

        await st.downstream_task(ctx, queue, session_alive, silence_state)

        assert runner.call_count == 2
        assert websocket.sent_bytes == [b"\x00\x01"]

    @pytest.mark.asyncio
    async def test_successful_reconnect_resets_retry_budget(self, monkeypatch):
        import app.api.v1.realtime.stream_tasks as st

        runner = _IntermittentRetryRunner(
            [_make_content_event(audio_bytes=b"\x00\x01")],
            [_make_content_event(audio_bytes=b"\x00\x02")],
        )
        st.configure_runtime(
            runner=runner,
            _extract_server_message_from_state_delta=lambda delta: None,
            _usage_int=lambda *args: 0,
            TOKEN_PRICE_PROMPT_PER_MILLION=0.0,
            TOKEN_PRICE_COMPLETION_PER_MILLION=0.0,
            DEBUG_TELEMETRY=False,
            _sanitize_log=lambda value: value,
        )
        monkeypatch.setattr(st, "LIVE_STREAM_MAX_RETRIES", 1)
        monkeypatch.setattr(st, "LIVE_STREAM_RETRY_BASE_SECONDS", 0.01)

        websocket = _FakeWebSocket()
        ctx = _make_ctx(websocket)
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()
        silence_state = _make_silence_state()

        await st.downstream_task(ctx, queue, session_alive, silence_state)

        assert runner.call_count == 3
        assert websocket.sent_bytes == [b"\x00\x01", b"\x00\x02"]

    @pytest.mark.asyncio
    async def test_connection_errors_are_re_raised(self):
        import app.api.v1.realtime.stream_tasks as st

        class _FailingSendWebSocket(_FakeWebSocket):
            async def send_bytes(self, payload: bytes):
                raise BrokenPipeError("socket closed")

        st.configure_runtime(
            runner=_FakeRunner([_make_content_event(audio_bytes=b"\x00\x01")]),
            _extract_server_message_from_state_delta=lambda delta: None,
            _usage_int=lambda *args: 0,
            TOKEN_PRICE_PROMPT_PER_MILLION=0.0,
            TOKEN_PRICE_COMPLETION_PER_MILLION=0.0,
            DEBUG_TELEMETRY=False,
            _sanitize_log=lambda value: value,
        )

        websocket = _FailingSendWebSocket()
        ctx = _make_ctx(websocket)
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()
        silence_state = _make_silence_state()

        with pytest.raises(BrokenPipeError, match="socket closed"):
            await st.downstream_task(ctx, queue, session_alive, silence_state)


class TestWatchdogClearingInDownstream:
    """Verify watchdog clears on agent output events."""

    @pytest.mark.asyncio
    async def test_audio_output_clears_watchdog(self):
        websocket, _, ss = await _run_downstream_events(
            _make_content_event(audio_bytes=b"\x00\x01"),
            silence_state=_make_silence_state(
                awaiting_agent_response=True,
                user_spoke_at=time.monotonic(),
            ),
        )

        assert ss.awaiting_agent_response is False
        assert ss.agent_busy is True
        assert websocket.sent_bytes == [b"\x00\x01"]

    @pytest.mark.asyncio
    async def test_text_output_clears_watchdog(self):
        ctx = _make_ctx(_FakeWebSocket(), is_native_audio=False)
        websocket, _, ss = await _run_downstream_events(
            _make_content_event(text="Let me help with that"),
            ctx=ctx,
            silence_state=_make_silence_state(
                awaiting_agent_response=True,
                user_spoke_at=time.monotonic(),
            ),
        )

        assert ss.awaiting_agent_response is False
        assert ss.agent_busy is True
        agent_transcripts = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "transcription" and msg.get("role") == "agent"
        ]
        assert agent_transcripts == [{
            "type": "transcription",
            "role": "agent",
            "text": "Let me help with that",
            "partial": True,
        }]

    @pytest.mark.asyncio
    async def test_output_transcription_clears_watchdog(self):
        websocket, _, ss = await _run_downstream_events(
            _make_live_event(
                output_transcription=SimpleNamespace(text="Welcome!", finished=True)
            ),
            silence_state=_make_silence_state(
                awaiting_agent_response=True,
                user_spoke_at=time.monotonic(),
            ),
        )

        assert ss.awaiting_agent_response is False
        assert ss.agent_busy is True
        agent_transcripts = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "transcription" and msg.get("role") == "agent"
        ]
        assert agent_transcripts == [{
            "type": "transcription",
            "role": "agent",
            "text": "Welcome!",
            "partial": False,
        }]

    @pytest.mark.asyncio
    async def test_output_transcription_marks_session_greeted(self):
        ctx = _make_ctx(_FakeWebSocket())

        await _run_downstream_events(
            _make_live_event(
                output_transcription=SimpleNamespace(text="Welcome!", finished=False)
            ),
            ctx=ctx,
        )

        assert ctx.session_state["temp:greeted"] is True

    @pytest.mark.asyncio
    async def test_session_server_message_fallback_emits_call_control_without_state_delta(self):
        ctx = _make_ctx(_FakeWebSocket())
        ctx.session_state["temp:last_server_message"] = {
            "id": 7,
            "type": "call_control",
            "action": "end_after_speaking",
            "reason": "callback_acknowledged",
        }

        websocket, _, _ = await _run_downstream_events(
            _make_live_event(turn_complete=True),
            ctx=ctx,
        )

        assert {
            "type": "call_control",
            "action": "end_after_speaking",
            "reason": "callback_acknowledged",
        } in websocket.sent_texts

    @pytest.mark.asyncio
    async def test_audio_output_marks_session_greeted(self):
        ctx = _make_ctx(_FakeWebSocket())

        await _run_downstream_events(
            _make_content_event(audio_bytes=b"\x00\x01"),
            ctx=ctx,
        )

        assert ctx.session_state["temp:greeted"] is True

    @pytest.mark.asyncio
    async def test_interrupted_clears_watchdog(self):
        websocket, _, ss = await _run_downstream_events(
            _make_live_event(interrupted=True),
            silence_state=_make_silence_state(
                awaiting_agent_response=True,
                user_spoke_at=time.monotonic(),
                agent_busy=True,
            ),
        )

        assert ss.awaiting_agent_response is False
        assert ss.agent_busy is False
        interrupted_messages = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "interrupted"
        ]
        assert interrupted_messages == [{
            "type": "interrupted",
            "interrupted": True,
        }]

    @pytest.mark.asyncio
    async def test_turn_complete_does_not_clear_watchdog(self):
        """turn_complete must NOT clear awaiting_agent_response (critical for transfers)."""
        websocket, _, ss = await _run_downstream_events(
            _make_live_event(turn_complete=True),
            silence_state=_make_silence_state(
                awaiting_agent_response=True,
                user_spoke_at=time.monotonic(),
                agent_busy=True,
            ),
        )

        assert ss.awaiting_agent_response is True
        assert ss.agent_busy is False
        agent_status_messages = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "agent_status"
        ]
        assert agent_status_messages == [{
            "type": "agent_status",
            "agent": "ekaette_router",
            "status": "idle",
        }]

    @pytest.mark.asyncio
    async def test_stale_output_partial_does_not_clear_watchdog(self):
        websocket, _, silence_state = await _run_downstream_events(
            _make_live_event(
                output_transcription=SimpleNamespace(text="Welcome!", finished=True)
            ),
            _make_live_event(
                input_transcription=SimpleNamespace(text="I need pricing", finished=True)
            ),
            _make_live_event(
                output_transcription=SimpleNamespace(text="Welc", finished=False)
            ),
        )

        assert silence_state.awaiting_agent_response is True
        agent_transcripts = [
            msg for msg in websocket.sent_texts
            if msg.get("type") == "transcription" and msg.get("role") == "agent"
        ]
        assert len(agent_transcripts) == 1
        assert agent_transcripts[0]["text"] == "Welcome!"


class TestWatchdogSuspensionOnNewInput:
    """Fresh caller input should cancel the prior response-latency watchdog."""

    @pytest.mark.asyncio
    async def test_binary_audio_clears_watchdog(self):
        st = _inject_stream_tasks_globals()

        st.configure_runtime(
            types=_FakeTypes,
            _check_rate_limit=lambda *args: True,
            UPLOAD_RATE_LIMIT=10,
            _validate_upload_bytes=lambda *args: None,
            MAX_UPLOAD_BYTES=1024 * 1024,
            cache_latest_image=lambda **kwargs: None,
            _normalize_company_id=lambda value: value,
            _append_canonical_lock_fields=lambda payload, session_state: payload,
            _voice_for_industry=lambda industry: "Aoede",
            _build_session_started_message=lambda **kwargs: kwargs,
        )

        websocket = _FakeWebSocket(
            incoming=[
                {"bytes": b"\x00\x01"},
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )
        ctx = _make_ctx(websocket)
        silence_state = _make_silence_state(
            awaiting_agent_response=True,
            user_spoke_at=time.monotonic() - 5.0,
            response_nudge_count=1,
        )
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()

        with pytest.raises(WebSocketDisconnect):
            await st.upstream_task(ctx, queue, session_alive, silence_state)

        assert silence_state.awaiting_agent_response is False
        assert silence_state.response_nudge_count == 0
        assert len(queue.realtime) == 1

    @pytest.mark.asyncio
    async def test_activity_start_clears_watchdog(self):
        st = _inject_stream_tasks_globals()

        st.configure_runtime(
            types=_FakeTypes,
            _check_rate_limit=lambda *args: True,
            UPLOAD_RATE_LIMIT=10,
            _validate_upload_bytes=lambda *args: None,
            MAX_UPLOAD_BYTES=1024 * 1024,
            cache_latest_image=lambda **kwargs: None,
            _normalize_company_id=lambda value: value,
            _append_canonical_lock_fields=lambda payload, session_state: payload,
            _voice_for_industry=lambda industry: "Aoede",
            _build_session_started_message=lambda **kwargs: kwargs,
        )

        websocket = _FakeWebSocket(
            incoming=[
                {"text": json.dumps({"type": "activity_start"})},
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )
        ctx = _make_ctx(websocket, manual_vad_active=True)
        silence_state = _make_silence_state(
            awaiting_agent_response=True,
            user_spoke_at=time.monotonic() - 5.0,
            response_nudge_count=1,
        )
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()

        with pytest.raises(WebSocketDisconnect):
            await st.upstream_task(ctx, queue, session_alive, silence_state)

        assert silence_state.awaiting_agent_response is False
        assert silence_state.response_nudge_count == 0
        assert queue.activity_start_calls == 1


class TestResponseLatencyNudge:
    """Verify the fast-path nudge fires at the configured thresholds."""

    @pytest.mark.asyncio
    async def test_nudge_fires_at_3s(self):
        st = _inject_stream_tasks_globals()
        ss = _make_silence_state(
            awaiting_agent_response=True,
            user_spoke_at=time.monotonic() - 4.0,  # 4s ago
        )

        async with _run_nudge_task(st, ss) as queue:
            await asyncio.wait_for(queue.sent_event.wait(), timeout=2.5)

        assert ss.response_nudge_count == 1
        assert len(queue.sent) >= 1
        # Verify the nudge text mentions filler
        first_nudge_text = queue.sent[0].parts[0].text
        assert "filler" in first_nudge_text.lower() or "check that" in first_nudge_text.lower()

    @pytest.mark.asyncio
    async def test_nudge_not_gated_on_agent_busy(self):
        """Response nudge must fire even when agent_busy=True."""
        st = _inject_stream_tasks_globals()
        ss = _make_silence_state(
            awaiting_agent_response=True,
            user_spoke_at=time.monotonic() - 4.0,
            agent_busy=True,  # This must NOT block response nudge
        )

        async with _run_nudge_task(st, ss) as queue:
            await asyncio.wait_for(queue.sent_event.wait(), timeout=2.5)

        assert ss.response_nudge_count == 1
        assert len(queue.sent) >= 1

    @pytest.mark.asyncio
    async def test_customer_silence_nudge_suppressed_when_awaiting_response(self):
        """Customer-silence nudge must NOT fire when awaiting_agent_response=True."""
        st = _inject_stream_tasks_globals()
        now = time.monotonic()
        ss = _make_silence_state(
            awaiting_agent_response=True,
            user_spoke_at=now - 0.1,  # Still safely below the 2s response-latency threshold
            agent_busy=False,
            silence_nudge_due_at=now - 1.0,  # Due in the past -> would fire
        )

        async with _run_nudge_task(st, ss) as queue:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(queue.sent_event.wait(), timeout=1.8)

        # No customer-silence nudge should have fired (below response-latency threshold,
        # and customer-silence path is suppressed by awaiting_agent_response)
        assert ss.silence_nudge_count == 0


class TestChannelBootstrap:
    """Verify app:channel is set correctly in session initialization."""

    @pytest.mark.asyncio
    async def test_text_adapter_preserves_specific_channel(self):
        """adk_text_adapter._ensure_session stores the concrete text channel."""
        from app.channels.adk_text_adapter import _ensure_session

        created_session = SimpleNamespace(id="session-1")
        session_service = SimpleNamespace(
            get_session=AsyncMock(return_value=None),
            create_session=AsyncMock(return_value=created_session),
        )

        resolved_id = await _ensure_session(
            session_service=session_service,
            app_name="ekaette",
            user_id="wa_user",
            session_id="session-1",
            tenant_id="public",
            company_id="ekaette-electronics",
            channel="sms",
        )

        assert resolved_id == "session-1"
        create_kwargs = session_service.create_session.await_args.kwargs
        assert create_kwargs["state"]["app:channel"] == "sms"
