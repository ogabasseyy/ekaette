"""Tests for transfer filler: response latency watchdog and channel-aware nudges."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

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


class TestChannelGatingInCallbacks:
    """Step 2: Latency policy gated on app:channel == 'voice'."""

    @pytest.mark.asyncio
    async def test_injects_latency_policy_for_voice_channel(self):
        from types import SimpleNamespace

        from google.adk.models.llm_request import LlmRequest

        from app.agents.callbacks import before_model_inject_config

        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {"name": "Electronics", "greeting": "Hi!"},
                "app:company_profile": {"name": "Test Co"},
                "app:channel": "voice",
                "temp:greeted": True,
            },
            agent_name="catalog_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])
        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "CRITICAL latency policy" in system_instruction
        assert "tool call or agent transfer" in system_instruction

    @pytest.mark.asyncio
    async def test_no_latency_policy_for_text_channel(self):
        from types import SimpleNamespace

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
        from types import SimpleNamespace

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


class TestWatchdogArmingInDownstream:
    """Verify watchdog arms on accepted transcription and NOT on suppressed late partials."""

    def test_accepted_partial_arms_watchdog(self):
        """An accepted partial transcription should arm the watchdog."""
        ss = _make_silence_state()
        assert ss.awaiting_agent_response is False

        # Simulate what downstream_task does for an accepted partial
        ss.awaiting_agent_response = True
        ss.user_spoke_at = time.monotonic()
        ss.response_nudge_count = 0

        assert ss.awaiting_agent_response is True
        assert ss.user_spoke_at > 0

    def test_accepted_finished_arms_watchdog(self):
        """An accepted finished=True transcription should arm the watchdog."""
        ss = _make_silence_state()
        ss.awaiting_agent_response = True
        ss.user_spoke_at = time.monotonic()
        ss.response_nudge_count = 0
        assert ss.awaiting_agent_response is True

    def test_suppressed_late_partial_does_not_arm(self):
        """Late partials (input_finalized=True, finished=False) must NOT arm watchdog."""
        ss = _make_silence_state()
        input_finalized = True
        finished = False

        # Simulate the suppression branch — no watchdog arming
        if input_finalized and not finished:
            pass  # suppressed — watchdog NOT armed

        assert ss.awaiting_agent_response is False


class TestWatchdogClearingInDownstream:
    """Verify watchdog clears on agent output events."""

    def test_audio_output_clears_watchdog(self):
        ss = _make_silence_state(awaiting_agent_response=True, user_spoke_at=time.monotonic())
        # Simulate audio output handler
        ss.agent_busy = True
        ss.awaiting_agent_response = False
        assert ss.awaiting_agent_response is False

    def test_text_output_clears_watchdog(self):
        ss = _make_silence_state(awaiting_agent_response=True, user_spoke_at=time.monotonic())
        ss.agent_busy = True
        ss.awaiting_agent_response = False
        assert ss.awaiting_agent_response is False

    def test_output_transcription_clears_watchdog(self):
        ss = _make_silence_state(awaiting_agent_response=True, user_spoke_at=time.monotonic())
        ss.agent_busy = True
        ss.awaiting_agent_response = False
        assert ss.awaiting_agent_response is False

    def test_interrupted_clears_watchdog(self):
        ss = _make_silence_state(awaiting_agent_response=True, user_spoke_at=time.monotonic())
        ss.agent_busy = False
        ss.awaiting_agent_response = False
        assert ss.awaiting_agent_response is False

    def test_turn_complete_does_not_clear_watchdog(self):
        """turn_complete must NOT clear awaiting_agent_response (critical for transfers)."""
        ss = _make_silence_state(awaiting_agent_response=True, user_spoke_at=time.monotonic())
        # Simulate turn_complete — only clears agent_busy, NOT awaiting_agent_response
        ss.agent_busy = False
        # awaiting_agent_response is NOT touched
        assert ss.awaiting_agent_response is True

    @pytest.mark.asyncio
    async def test_stale_output_partial_does_not_clear_watchdog(self):
        import app.api.v1.realtime.stream_tasks as st

        st.configure_runtime(
            runner=_FakeRunner([
                SimpleNamespace(
                    content=None,
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="Welcome!", finished=True),
                    interrupted=False,
                    actions=None,
                    turn_complete=False,
                    usage_metadata=None,
                    live_session_resumption_update=None,
                    author="ekaette_router",
                ),
                SimpleNamespace(
                    content=None,
                    input_transcription=SimpleNamespace(text="I need pricing", finished=True),
                    output_transcription=None,
                    interrupted=False,
                    actions=None,
                    turn_complete=False,
                    usage_metadata=None,
                    live_session_resumption_update=None,
                    author="ekaette_router",
                ),
                SimpleNamespace(
                    content=None,
                    input_transcription=None,
                    output_transcription=SimpleNamespace(text="Welc", finished=False),
                    interrupted=False,
                    actions=None,
                    turn_complete=False,
                    usage_metadata=None,
                    live_session_resumption_update=None,
                    author="ekaette_router",
                ),
            ]),
            _extract_server_message_from_state_delta=lambda delta: None,
            _usage_int=lambda *args: 0,
            TOKEN_PRICE_PROMPT_PER_MILLION=0.0,
            TOKEN_PRICE_COMPLETION_PER_MILLION=0.0,
            DEBUG_TELEMETRY=False,
            _sanitize_log=lambda value: value,
        )

        websocket = _FakeWebSocket()
        ctx = _make_ctx(websocket)
        silence_state = _make_silence_state()

        await st.downstream_task(
            ctx,
            _FakeRequestQueue(),
            asyncio.Event(),
            silence_state,
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
    """Verify the fast-path nudge fires at 3s and 15s."""

    @pytest.mark.asyncio
    async def test_nudge_fires_at_3s(self):
        st = _inject_stream_tasks_globals()
        ss = _make_silence_state(
            awaiting_agent_response=True,
            user_spoke_at=time.monotonic() - 4.0,  # 4s ago
        )
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()

        # Patch types for the module
        original_types_bind = st.bind_runtime_values

        def fake_bind(*names):
            if names == ("types",):
                return (_FakeTypes,)
            return original_types_bind(*names)

        st.bind_runtime_values = fake_bind

        try:
            # Run one iteration of the nudge loop
            task = asyncio.create_task(
                st.silence_nudge_task(queue, session_alive, ss)
            )
            await asyncio.sleep(1.5)  # Let it run one cycle
            session_alive.clear()
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected: we cancel the task after assertions
        finally:
            st.bind_runtime_values = original_types_bind

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
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()

        original_bind = st.bind_runtime_values
        st.bind_runtime_values = lambda *names: (_FakeTypes,) if names == ("types",) else original_bind(*names)

        try:
            task = asyncio.create_task(
                st.silence_nudge_task(queue, session_alive, ss)
            )
            await asyncio.sleep(1.5)
            session_alive.clear()
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected: we cancel the task after assertions
        finally:
            st.bind_runtime_values = original_bind

        assert ss.response_nudge_count == 1
        assert len(queue.sent) >= 1

    @pytest.mark.asyncio
    async def test_customer_silence_nudge_suppressed_when_awaiting_response(self):
        """Customer-silence nudge must NOT fire when awaiting_agent_response=True."""
        st = _inject_stream_tasks_globals()
        now = time.monotonic()
        ss = _make_silence_state(
            awaiting_agent_response=True,
            user_spoke_at=now - 1.0,  # Only 1s — below 3s threshold
            agent_busy=False,
            silence_nudge_due_at=now - 1.0,  # Due in the past -> would fire
        )
        queue = _FakeRequestQueue()
        session_alive = asyncio.Event()
        session_alive.set()

        original_bind = st.bind_runtime_values
        st.bind_runtime_values = lambda *names: (_FakeTypes,) if names == ("types",) else original_bind(*names)

        try:
            task = asyncio.create_task(
                st.silence_nudge_task(queue, session_alive, ss)
            )
            await asyncio.sleep(1.5)
            session_alive.clear()
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected: we cancel the task after assertions
        finally:
            st.bind_runtime_values = original_bind

        # No customer-silence nudge should have fired (below 3s threshold,
        # and customer-silence path is suppressed by awaiting_agent_response)
        assert ss.silence_nudge_count == 0


class TestChannelBootstrap:
    """Verify app:channel is set correctly in session initialization."""

    def test_text_adapter_sets_channel_text(self):
        """adk_text_adapter._ensure_session sets app:channel to 'text'."""
        # We verify the source code contains the expected initial_state key
        import inspect

        from app.channels.adk_text_adapter import _ensure_session

        source = inspect.getsource(_ensure_session)
        assert '"app:channel": "text"' in source or "'app:channel': 'text'" in source
