"""Tests for CallSession + SIPServer gateway mode.

Phase 3 of Single AI Brain — CallSession routes through Cloud Run WebSocket
instead of direct Gemini Live when gateway_mode=True.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from sip_bridge.session import CallSession, SILENCE_FRAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockGatewayClient:
    """Mock GatewayClient for testing gateway mode."""

    def __init__(self):
        self.send_audio = AsyncMock()
        self.send_text = AsyncMock()
        self.close = AsyncMock()
        self.connect = AsyncMock()
        self.reconnect = AsyncMock()
        self.session_id = "mock-session"
        self._canonical_session_id = ""
        self._resumption_token = ""
        self._frames_to_yield: list = []
        self._receive_batches: list[list] | None = None
        self._receive_call_count = 0

    @property
    def canonical_session_id(self) -> str:
        return self._canonical_session_id

    @property
    def resumption_token(self) -> str:
        return self._resumption_token

    def remember_canonical_session_id(self, session_id: str) -> None:
        self._canonical_session_id = session_id

    def remember_resumption_token(self, token: str) -> None:
        self._resumption_token = token

    async def receive(self):
        if self._receive_batches is not None:
            batch_index = min(self._receive_call_count, len(self._receive_batches) - 1)
            frames = self._receive_batches[batch_index]
            self._receive_call_count += 1
        else:
            frames = self._frames_to_yield
        for frame in frames:
            yield frame


# ---------------------------------------------------------------------------
# Gateway mode routing
# ---------------------------------------------------------------------------

class TestCallSessionGatewayMode:
    """CallSession in gateway_mode uses GatewayClient instead of Gemini."""

    def test_caller_phone_field_exists(self):
        """CallSession has _caller_phone field."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        assert hasattr(s, "_caller_phone")
        assert s._caller_phone == ""

    def test_caller_phone_can_be_set(self):
        """CallSession accepts _caller_phone at construction."""
        s = CallSession(
            call_id="c1", tenant_id="public", company_id="acme",
            _caller_phone="+2348012345678",
        )
        assert s._caller_phone == "+2348012345678"

    @pytest.mark.asyncio
    async def test_gateway_send_loop_sends_audio(self):
        """Audio from _gemini_in_queue sent to gateway as binary frames."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        # Shutdown after first send
        original_send = mock_client.send_audio
        async def send_and_stop(data):
            await original_send(data)
            s._shutdown.set()
        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client

        pcm16 = b"\x01\x02" * 320  # 640 bytes
        await s._gemini_in_queue.put(pcm16)
        s._model_speaking = False
        s._model_speech_end_time = 0.0

        await s._gateway_send_loop()
        original_send.assert_called_once_with(pcm16)

    @pytest.mark.asyncio
    async def test_gateway_send_loop_mutes_only_during_greeting_lock(self):
        """Greeting lock should mute caller audio during the non-interruptible greeting."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        original_send = mock_client.send_audio
        async def send_and_stop(data):
            await original_send(data)
            s._shutdown.set()
        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client
        s._greeting_lock_active = True

        pcm16 = b"\x01\x02" * 320
        await s._gemini_in_queue.put(pcm16)

        await s._gateway_send_loop()
        original_send.assert_called_once_with(SILENCE_FRAME)

    @pytest.mark.asyncio
    async def test_gateway_send_loop_keeps_caller_audio_after_greeting(self):
        """Post-greeting speech should remain interruptible."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        original_send = mock_client.send_audio

        async def send_and_stop(data):
            await original_send(data)
            s._shutdown.set()

        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client
        s._model_speaking = True
        s._greeting_lock_active = False

        pcm16 = b"\x01\x02" * 320
        await s._gemini_in_queue.put(pcm16)

        await s._gateway_send_loop()
        original_send.assert_called_once_with(pcm16)

    @pytest.mark.asyncio
    async def test_gateway_send_loop_tolerates_missing_gateway_client(self, monkeypatch):
        """A missing gateway_client should not raise while shutdown is in progress."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        await s._gemini_in_queue.put(b"\x01\x02" * 320)
        s.gateway_client = None

        async def _sleep_and_stop(_delay: float) -> None:
            s._shutdown.set()

        monkeypatch.setattr("sip_bridge.session.asyncio.sleep", _sleep_and_stop)

        await s._gateway_send_loop()

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_routes_audio_to_outbound(self):
        """Audio from gateway goes to outbound_queue."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        audio_data = b"\x00" * 960
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(is_audio=True, audio_data=audio_data),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert not s.outbound_queue.empty()
        assert s.outbound_queue.get_nowait() == audio_data
        assert s._model_speaking is True

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_handles_session_started(self):
        """session_started stores canonical session ID."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-xyz",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert mock_client.canonical_session_id == "canonical-xyz"
        assert s._gateway_session_started.is_set() is True
        assert s._greeting_lock_active is True

    @pytest.mark.asyncio
    async def test_wait_until_answer_ready_returns_when_audio_buffered(self):
        """Pre-answer wait should unblock once outbound greeting audio is ready."""
        s = CallSession(
            call_id="c1",
            tenant_id="public",
            company_id="acme",
            delay_answer_until_ready=True,
        )

        async def _emit_ready():
            await asyncio.sleep(0.01)
            s._first_outbound_audio_ready.set()

        waiter = asyncio.create_task(s.wait_until_answer_ready(0.2))
        emitter = asyncio.create_task(_emit_ready())
        result = await waiter
        await emitter

        assert result is True

    @pytest.mark.asyncio
    async def test_wait_until_answer_ready_returns_when_gateway_session_started_for_deferred_callback(self):
        """Deferred callback greeting sessions can become answer-ready before speech starts."""
        s = CallSession(
            call_id="c1",
            tenant_id="public",
            company_id="acme",
            delay_answer_until_ready=True,
            defer_connect_greeting_until_answer=True,
        )
        s.gateway_client = MockGatewayClient()

        async def _emit_started():
            await asyncio.sleep(0.01)
            s._gateway_session_started.set()

        waiter = asyncio.create_task(s.wait_until_answer_ready(0.2))
        emitter = asyncio.create_task(_emit_started())
        result = await waiter
        await emitter

        assert result is True

    def test_mark_answered_releases_media_gate(self):
        """mark_answered should release outbound media sending after pre-answer warmup."""
        s = CallSession(
            call_id="c1",
            tenant_id="public",
            company_id="acme",
            delay_answer_until_ready=True,
        )
        assert s._media_send_enabled.is_set() is False
        s.mark_answered()
        assert s._media_send_enabled.is_set() is True

    def test_mark_answered_sets_callback_post_answer_release_time(self):
        """Callback-only post-answer grace should start when the SIP leg answers."""
        s = CallSession(
            call_id="c1",
            tenant_id="public",
            company_id="acme",
            delay_answer_until_ready=True,
            callback_post_answer_grace_sec=0.4,
        )

        before = time.monotonic()
        s.mark_answered()

        assert s._media_send_enabled.is_set() is True
        assert s._callback_post_answer_release_at >= before + 0.35
        assert s._callback_post_answer_grace_active() is True

    @pytest.mark.asyncio
    async def test_mark_answered_sends_deferred_gateway_greeting_once(self):
        """Deferred callback greeting should be sent once the SIP leg answers."""
        s = CallSession(
            call_id="c1",
            tenant_id="public",
            company_id="acme",
            delay_answer_until_ready=True,
            defer_connect_greeting_until_answer=True,
            connect_greeting_text="[Callback call connected]",
        )
        mock_client = MockGatewayClient()
        s.gateway_client = mock_client
        s._gateway_session_started.set()

        s.mark_answered()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        mock_client.send_text.assert_awaited_once()
        payload = mock_client.send_text.await_args.args[0]
        assert json.loads(payload)["text"] == "[Callback call connected]"
        assert s._gateway_greeting_sent is True
        assert s._greeting_lock_active is True

    def test_mark_answered_enables_postanswer_agent_suppression_when_greeting_finished_preanswer(self):
        """If the callback greeting completed before answer, suppress repeat agent audio until user speaks."""
        s = CallSession(
            call_id="c1",
            tenant_id="public",
            company_id="acme",
            delay_answer_until_ready=True,
            callback_post_answer_grace_sec=1.0,
        )
        s._preanswer_agent_final_seen = True

        s.mark_answered()

        assert s._suppress_postanswer_agent_audio_until_user_speaks is True

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_handles_interrupted(self):
        """interrupted clears model speaking state."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._model_speaking = True
        s.outbound_queue.put_nowait(b"\x00" * 100)
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "interrupted"}),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert s._model_speaking is False
        assert s.outbound_queue.empty()

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_suppresses_agent_audio_until_user_speaks_after_answer(self):
        """Callback sessions should drop repeated post-answer agent audio until the caller speaks."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._answered_at_monotonic = time.monotonic()
        s._suppress_postanswer_agent_audio_until_user_speaks = True
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(is_audio=True, audio_data=b"\x00" * 960),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert s.outbound_queue.empty()
        assert s._suppressed_agent_audio_frames == 1

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_user_transcription_releases_postanswer_agent_suppression(self):
        """Caller speech after answer should release the callback duplicate-audio guard."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._answered_at_monotonic = time.monotonic()
        s._suppress_postanswer_agent_audio_until_user_speaks = True
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "transcription",
                    "role": "user",
                    "partial": False,
                    "text": "hello",
                }),
            ),
            GatewayFrame(is_audio=True, audio_data=b"\x00" * 960),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert s._suppress_postanswer_agent_audio_until_user_speaks is False
        assert s._user_spoke_after_answer is True
        assert s.outbound_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_session_started_enables_greeting_lock_only(self):
        """session_started should lock interruption without faking model audio."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-xyz",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert s._model_speaking is False
        assert s._gateway_session_started.is_set() is True
        assert s._greeting_lock_active is True

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_session_started_defers_callback_greeting_until_answer(self):
        """Callback prewarm should not speak before the outbound leg answers."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(
            call_id="c1",
            tenant_id="public",
            company_id="acme",
            defer_connect_greeting_until_answer=True,
            connect_greeting_text="[Callback call connected]",
        )
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-xyz",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        mock_client.send_text.assert_not_awaited()
        assert s._gateway_session_started.is_set() is True
        assert s._gateway_greeting_sent is False

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_handles_agent_status_idle(self):
        """agent_status:idle clears model speaking state."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._model_speaking = True
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "agent_status", "status": "idle"}),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert s._model_speaking is False

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_session_ending_live_ended_shuts_down(self):
        """session_ending with reason=live_session_ended triggers shutdown."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_ending",
                    "reason": "live_session_ended",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert s._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_session_resumption_stores_token(self):
        """session_ending with reason=session_resumption stores token."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_ending",
                    "reason": "session_resumption",
                    "resumptionToken": "tok-abc",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert mock_client.resumption_token == "tok-abc"
        assert not s._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_agent_transfer_logs_and_tracks_tokens(self, caplog):
        """agent_transfer should be visible in logs and update reconnect state."""
        from sip_bridge.gateway_client import GatewayFrame

        caplog.set_level("INFO")
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "agent_transfer",
                    "transferType": "handoff",
                    "from": "catalog_agent",
                    "to": "support_agent",
                    "reason": "pricing_question",
                    "sessionId": "canonical-transfer",
                    "resumptionToken": "resume-transfer",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert "Gateway agent transfer" in caplog.text
        assert mock_client.canonical_session_id == "canonical-transfer"
        assert mock_client.resumption_token == "resume-transfer"

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_ignores_malformed_json_and_continues(self, caplog):
        """Malformed JSON should be logged and skipped without aborting the loop."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(is_audio=False, text_data="{not-json"),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_ending",
                    "reason": "live_session_ended",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert "Ignoring malformed gateway JSON" in caplog.text
        assert s._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_logs_transcriptions(self, caplog):
        """Gateway transcriptions should be visible for SIP/AT debugging."""
        from sip_bridge.gateway_client import GatewayFrame

        caplog.set_level("INFO")
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "transcription",
                    "role": "user",
                    "partial": True,
                    "text": "What company do you work for?",
                }),
            ),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "transcription",
                    "role": "agent",
                    "partial": False,
                    "text": "I work for Awgabassey Gadgets.",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert "Gateway user transcription partial=True" in caplog.text
        assert "Gateway agent transcription final" in caplog.text


# ---------------------------------------------------------------------------
# Server — caller phone extraction + user_id derivation
# ---------------------------------------------------------------------------

class TestServerCallerPhone:
    """server.py extracts caller phone from SIP From header."""

    def _make_gateway_config(self):
        from sip_bridge.config import BridgeConfig

        return BridgeConfig(
            sip_host="0.0.0.0",
            sip_port=6060,
            sip_public_ip="34.69.236.219",
            sip_allowed_peers=frozenset(),
            gemini_api_key="test-key",
            live_model_id="gemini-live-2.5-flash-native-audio",
            system_instruction="Test",
            gemini_voice="Aoede",
            company_id="ekaette-electronics",
            tenant_id="public",
            health_port=8081,
            sip_registrar="ng.sip.africastalking.com",
            sip_username="user@sip.example.com",
            sip_password="pass",
            sip_register_interval=300,
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )

    def test_extract_caller_phone_valid(self):
        """Valid SIP From header yields phone number."""
        from sip_bridge.wa_invite_handler import extract_caller_phone

        assert extract_caller_phone('"User" <sip:+2348012345678@example.com>') == "+2348012345678"

    def test_extract_caller_phone_empty(self):
        """Empty From header yields empty string."""
        from sip_bridge.wa_invite_handler import extract_caller_phone

        assert extract_caller_phone("") == ""

    def test_user_id_derivation_from_phone_is_namespaced(self):
        """user_id hash includes tenant/company scope to avoid cross-tenant collisions."""
        from sip_bridge.server import SIPServer

        server = SIPServer(config=self._make_gateway_config())
        session = server.handle_invite(
            "call-1",
            ("1.2.3.4", 5060),
            sip_from_header='"User" <sip:+2348012345678@example.com>',
        )
        user_id = session.gateway_client.user_id
        assert user_id.startswith("phone-")
        assert len(user_id) == 6 + 24  # "phone-" + 24 hex chars

    def test_anonymous_fallback_uses_namespaced_call_id(self):
        """No caller phone still namespaces the call-derived fallback."""
        from sip_bridge.server import SIPServer

        server = SIPServer(config=self._make_gateway_config())
        session = server.handle_invite("abc123@host", ("1.2.3.4", 5060))
        user_id = session.gateway_client.user_id
        assert user_id == "sip-anon-19f4b8c3a16a8156"
        assert user_id.startswith("sip-anon-")
        assert len(user_id) == 9 + 16  # "sip-anon-" + 16 hex chars

    def test_session_id_from_call_id_is_namespaced_and_safe(self):
        """session_id from call_id hash remains path-safe and scope-aware."""
        import re
        from sip_bridge.server import SIPServer

        server = SIPServer(config=self._make_gateway_config())
        session = server.handle_invite("abc123@host.example.com;tag=xyz", ("1.2.3.4", 5060))
        session_id = session.gateway_client.session_id
        assert session_id == "sip-0ca1880d6c0c72383b0b4b7f"
        assert re.match(r"^[A-Za-z0-9._:-]{1,128}$", session_id)


# ---------------------------------------------------------------------------
# Server — handle_invite gateway wiring
# ---------------------------------------------------------------------------

class TestServerHandleInviteGateway:
    """server.py handle_invite creates GatewayClient in gateway mode."""

    def _make_config(self, gateway_mode=False, gateway_ws_url="", gateway_ws_secret=""):
        from sip_bridge.config import BridgeConfig
        return BridgeConfig(
            sip_host="0.0.0.0",
            sip_port=6060,
            sip_public_ip="34.69.236.219",
            sip_allowed_peers=frozenset(),
            gemini_api_key="test-key",
            live_model_id="gemini-live-2.5-flash-native-audio",
            system_instruction="Test",
            gemini_voice="Aoede",
            company_id="ekaette-electronics",
            tenant_id="public",
            health_port=8081,
            sip_registrar="ng.sip.africastalking.com",
            sip_username="user@sip.example.com",
            sip_password="pass",
            sip_register_interval=300,
            gateway_mode=gateway_mode,
            gateway_ws_url=gateway_ws_url,
            gateway_ws_secret=gateway_ws_secret,
        )

    def test_handle_invite_no_gateway_no_client(self):
        """Default config → no gateway_client on session."""
        from sip_bridge.server import SIPServer
        server = SIPServer(config=self._make_config())
        session = server.handle_invite("call-1", ("1.2.3.4", 5060))
        assert session.gateway_client is None

    def test_handle_invite_gateway_mode_creates_client(self):
        """Gateway mode → session has GatewayClient."""
        from sip_bridge.server import SIPServer
        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )
        server = SIPServer(config=config)
        session = server.handle_invite(
            "call-1", ("1.2.3.4", 5060),
            sip_from_header='"User" <sip:+2348012345678@example.com>',
        )
        assert session.gateway_client is not None
        assert session._caller_phone == "+2348012345678"
        assert session.gateway_client.caller_phone == "+2348012345678"
        assert session.gateway_client.user_id.startswith("phone-")

    def test_handle_invite_gateway_uses_neutral_inbound_greeting_seed(self):
        """Gateway mode seeds a neutral inbound turn so the router does not transfer early."""
        from sip_bridge.server import SIPServer

        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )
        server = SIPServer(config=config)
        session = server.handle_invite(
            "call-greeting-seed",
            ("1.2.3.4", 5060),
            sip_from_header='"User" <sip:+2348012345678@example.com>',
        )

        assert session.connect_greeting_text == "[Phone call connected]"

    def test_handle_invite_outbound_callback_skips_preanswer_delay(self, monkeypatch):
        """Recent outbound callback hints should fast-answer the AT SIP leg."""
        from sip_bridge.server import SIPServer

        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )
        server = SIPServer(config=config)
        monkeypatch.setattr("sip_bridge.server.consume_outbound_callback_hint", lambda **_: True)

        session = server.handle_invite(
            "call-fast-answer",
            ("1.2.3.4", 5060),
            sip_from_header='"User" <sip:+2348012345678@example.com>',
        )

        assert session.delay_answer_until_ready is False

    def test_handle_invite_gateway_no_phone_uses_anon(self):
        """Gateway mode without From header → sip-anon user_id."""
        from sip_bridge.server import SIPServer
        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )
        server = SIPServer(config=config)
        session = server.handle_invite("call-2", ("1.2.3.4", 5060))
        assert session.gateway_client is not None
        assert session.gateway_client.user_id.startswith("sip-anon-")

    def test_handle_invite_gateway_passes_ws_secret(self):
        """Gateway mode passes shared HMAC secret to GatewayClient for per-call token minting."""
        from sip_bridge.server import SIPServer
        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )
        server = SIPServer(config=config)
        session = server.handle_invite("call-3", ("1.2.3.4", 5060))
        assert session.gateway_client is not None
        assert session.gateway_client.ws_secret == "shared-hmac-secret"
        # Each URL build mints a fresh token (unique JTI)
        url1 = session.gateway_client._build_connect_url()
        url2 = session.gateway_client._build_connect_url()
        assert "token=" in url1
        assert "token=" in url2
        # Tokens differ (unique JTI per mint)
        assert url1 != url2

    def test_handle_invite_gateway_without_secret_raises(self):
        """Gateway mode must fail closed when the HMAC secret is missing."""
        from sip_bridge.server import SIPServer
        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
        )
        server = SIPServer(config=config)
        with pytest.raises(ValueError, match="GATEWAY_WS_SECRET"):
            server.handle_invite("call-4", ("1.2.3.4", 5060))

    @pytest.mark.asyncio
    async def test_claim_prewarmed_callback_session_reuses_warm_session(self):
        """Outbound callback INVITEs should attach to a warm session instead of creating a cold one."""
        from sip_bridge.server import PrewarmedCallbackSession, SIPServer

        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )
        server = SIPServer(config=config)
        session = CallSession(
            call_id="callback-prewarm-1",
            tenant_id="public",
            company_id="ekaette-electronics",
            local_rtp_port=14567,
            delay_answer_until_ready=True,
            _caller_phone="+2348012345678",
        )
        task = asyncio.create_task(asyncio.sleep(0))
        key = "public:ekaette-electronics:+2348012345678"
        record = PrewarmedCallbackSession(
            key=key,
            tenant_id="public",
            company_id="ekaette-electronics",
            phone="+2348012345678",
            session=session,
            task=task,
            expires_at=9999999999.0,
        )
        server._prewarmed_callbacks[key] = record

        attached = server.claim_prewarmed_callback_session(
            call_id="call-attach-1",
            sip_from_header='"User" <sip:+2348012345678@example.com>',
            remote_rtp_addr=("1.2.3.4", 4000),
        )
        await task

        assert attached is session
        assert attached.call_id == "call-attach-1"
        assert attached.remote_rtp_addr == ("1.2.3.4", 4000)
        assert server._active_sessions["call-attach-1"] is session
        assert server._prewarmed_callbacks[key].attached is True

    def test_start_callback_prewarm_sets_post_answer_grace(self):
        """Prewarmed callback sessions should hold speech briefly after answer."""
        from sip_bridge.server import SIPServer

        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
            gateway_ws_secret="shared-hmac-secret",
        )
        server = SIPServer(config=config)
        original_create_task = asyncio.create_task

        class _DummyTask:
            def __init__(self) -> None:
                self._callbacks = []

            def add_done_callback(self, callback) -> None:
                self._callbacks.append(callback)

            def cancel(self) -> None:
                return None

        created_tasks: list[_DummyTask] = []

        def _fake_create_task(coro):
            task = _DummyTask()
            created_tasks.append(task)
            try:
                coro.close()
            except Exception:
                pass
            return task

        try:
            asyncio.create_task = _fake_create_task  # type: ignore[assignment]
            server._start_callback_prewarm(
                key="public:ekaette-electronics:+2348012345678",
                tenant_id="public",
                company_id="ekaette-electronics",
                phone="+2348012345678",
                expires_at=time.time() + 30.0,
            )
        finally:
            asyncio.create_task = original_create_task  # type: ignore[assignment]

        record = next(iter(server._prewarmed_callbacks.values()))
        assert record.session.prime_outbound_on_answer is True
        assert record.session.defer_connect_greeting_until_answer is True
        assert record.session.callback_post_answer_grace_sec > 0.0
        assert record.session.connect_greeting_text == "[Callback call connected]"
        for task in created_tasks:
            task.cancel()


# ---------------------------------------------------------------------------
# Gateway recv loop — backpressure
# ---------------------------------------------------------------------------

class TestGatewayRecvBackpressure:
    """Gateway recv loop handles QueueFull gracefully."""

    @pytest.mark.asyncio
    async def test_outbound_queue_full_increments_drops(self):
        """QueueFull on outbound increments outbound_drops instead of raising."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        # Fill the outbound queue to capacity
        for _ in range(s.outbound_queue.maxsize):
            s.outbound_queue.put_nowait(b"\x00" * 10)

        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(is_audio=True, audio_data=b"\x00" * 960),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert s.outbound_drops == 1


# ---------------------------------------------------------------------------
# Gateway bidi loop — reconnect
# ---------------------------------------------------------------------------

class TestGatewayBidiReconnect:
    """_gateway_bidi_loop retries on disconnect."""

    @pytest.mark.asyncio
    async def test_bidi_loop_exits_on_shutdown(self):
        """Loop exits immediately when shutdown is set."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s.gateway_client = MockGatewayClient()
        s._shutdown.set()
        await s._gateway_bidi_loop()  # Should return without error

    @pytest.mark.asyncio
    async def test_bidi_loop_stops_after_live_session_ended(self):
        """Loop stops retrying after live_session_ended."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_ending",
                    "reason": "live_session_ended",
                }),
            ),
        ]
        s.gateway_client = mock_client
        await s._gateway_bidi_loop()
        assert s._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_bidi_loop_reconnects_after_disconnect(self, monkeypatch):
        """Loop retries once and calls reconnect before the next receive batch."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._receive_batches = [
            [],
            [
                GatewayFrame(
                    is_audio=False,
                    text_data=json.dumps({
                        "type": "session_ending",
                        "reason": "live_session_ended",
                    }),
                ),
            ],
        ]
        s.gateway_client = mock_client

        async def _no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("sip_bridge.session.asyncio.sleep", _no_sleep)

        await s._gateway_bidi_loop()

        mock_client.reconnect.assert_awaited_once()
        assert s._shutdown.is_set()


# ---------------------------------------------------------------------------
# Server — done-callback session cleanup
# ---------------------------------------------------------------------------

class TestServerDoneCallback:
    """SIPProtocol done-callback cleans up _active_sessions when run() exits."""

    def _make_config(self, gateway_mode=False, gateway_ws_url="", gateway_ws_secret=""):  # noqa: FBT002
        from sip_bridge.config import BridgeConfig
        return BridgeConfig(
            sip_host="0.0.0.0",
            sip_port=6060,
            sip_public_ip="34.69.236.219",
            sip_allowed_peers=frozenset(),
            gemini_api_key="test-key",
            live_model_id="gemini-live-2.5-flash-native-audio",
            system_instruction="Test",
            gemini_voice="Aoede",
            company_id="ekaette-electronics",
            tenant_id="public",
            health_port=8081,
            sip_registrar="ng.sip.africastalking.com",
            sip_username="user@sip.example.com",
            sip_password="pass",
            sip_register_interval=300,
            gateway_mode=gateway_mode,
            gateway_ws_url=gateway_ws_url,
            gateway_ws_secret=gateway_ws_secret,
        )

    @pytest.mark.asyncio
    async def test_done_callback_removes_session_on_success(self, monkeypatch):
        """When session.run() completes, done-callback removes from _active_sessions."""
        from sip_bridge.server import SIPProtocol, SIPServer

        server = SIPServer(config=self._make_config())
        protocol = SIPProtocol(server)
        created_tasks: list[asyncio.Task[None]] = []

        class _DummyTransport:
            def sendto(self, data, addr):
                return None

        server._transport = _DummyTransport()

        async def _fake_run(self):
            return None

        from sip_bridge import server as server_mod

        monkeypatch.setattr(server_mod, "parse_sip_request", lambda message: {
            "headers": {"Call-ID": "call-done-1", "From": ""},
            "body": "v=0",
        })
        monkeypatch.setattr(server_mod, "parse_sdp_g711", lambda body: {"media_ip": "1.2.3.4", "media_port": 4000})
        monkeypatch.setattr(server_mod, "build_sdp_answer", lambda public_ip, local_rtp_port, **kwargs: "v=0")
        monkeypatch.setattr(server_mod, "build_sip_response", lambda code, reason, headers, sdp_body=None, contact_uri="": f"SIP/2.0 {code} {reason}")
        monkeypatch.setattr(server_mod.random, "randint", lambda start, end: 12000)
        monkeypatch.setattr("sip_bridge.session.CallSession.run", _fake_run)
        real_create_task = asyncio.create_task

        def _capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        monkeypatch.setattr(server_mod.asyncio, "create_task", _capture_task)

        await protocol._handle_invite("INVITE sip:test SIP/2.0", ("1.2.3.4", 5060))
        assert "call-done-1" in server._active_sessions
        assert created_tasks
        results = await asyncio.wait_for(
            asyncio.gather(*created_tasks, return_exceptions=True),
            timeout=0.1,
        )
        assert results == [None]
        await asyncio.sleep(0)
        assert "call-done-1" not in server._active_sessions

    @pytest.mark.asyncio
    async def test_done_callback_removes_session_on_exception(self, monkeypatch):
        """When session.run() raises, done-callback still removes from _active_sessions."""
        from sip_bridge.server import SIPProtocol, SIPServer

        server = SIPServer(config=self._make_config())
        protocol = SIPProtocol(server)
        created_tasks: list[asyncio.Task[None]] = []

        class _DummyTransport:
            def sendto(self, data, addr):
                return None

        server._transport = _DummyTransport()

        async def _fake_run(self):
            raise RuntimeError("session crashed")

        from sip_bridge import server as server_mod

        monkeypatch.setattr(server_mod, "parse_sip_request", lambda message: {
            "headers": {"Call-ID": "call-done-2", "From": ""},
            "body": "v=0",
        })
        monkeypatch.setattr(server_mod, "parse_sdp_g711", lambda body: {"media_ip": "1.2.3.4", "media_port": 4000})
        monkeypatch.setattr(server_mod, "build_sdp_answer", lambda public_ip, local_rtp_port, **kwargs: "v=0")
        monkeypatch.setattr(server_mod, "build_sip_response", lambda code, reason, headers, sdp_body=None, contact_uri="": f"SIP/2.0 {code} {reason}")
        monkeypatch.setattr(server_mod.random, "randint", lambda start, end: 12000)
        monkeypatch.setattr("sip_bridge.session.CallSession.run", _fake_run)
        real_create_task = asyncio.create_task

        def _capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        monkeypatch.setattr(server_mod.asyncio, "create_task", _capture_task)

        await protocol._handle_invite("INVITE sip:test SIP/2.0", ("1.2.3.4", 5060))
        assert "call-done-2" in server._active_sessions
        assert created_tasks
        results = await asyncio.wait_for(
            asyncio.gather(*created_tasks, return_exceptions=True),
            timeout=0.1,
        )
        assert len(results) == 1
        assert isinstance(results[0], RuntimeError)
        assert str(results[0]) == "session crashed"
        await asyncio.sleep(0)
        assert "call-done-2" not in server._active_sessions

    @pytest.mark.asyncio
    async def test_handle_invite_waits_for_preanswer_audio_before_200_ok(self, monkeypatch):
        """Gateway sessions should keep ringing until the first outbound audio is ready."""
        from sip_bridge.server import SIPProtocol, SIPServer

        server = SIPServer(config=self._make_config(gateway_mode=True, gateway_ws_url="wss://ekaette.run.app", gateway_ws_secret="secret"))
        protocol = SIPProtocol(server)
        sent_messages: list[str] = []
        ready = asyncio.Event()

        class _DummyTransport:
            def sendto(self, data, addr):
                sent_messages.append(data.decode() if isinstance(data, bytes) else data)

        class _FakeSession:
            delay_answer_until_ready = True
            startup_failed = False

            def __init__(self):
                self.answered = False

            async def wait_until_answer_ready(self, timeout: float) -> bool:
                await ready.wait()
                return True

            def mark_answered(self) -> None:
                self.answered = True

            async def run(self):
                await asyncio.sleep(0)

        server._transport = _DummyTransport()
        fake_session = _FakeSession()

        from sip_bridge import server as server_mod

        monkeypatch.setattr(server_mod, "parse_sip_request", lambda message: {
            "headers": {"Call-ID": "call-ready-1", "From": ""},
            "body": "v=0",
        })
        monkeypatch.setattr(server_mod, "parse_sdp_g711", lambda body: {"media_ip": "1.2.3.4", "media_port": 4000})
        monkeypatch.setattr(server_mod, "build_sdp_answer", lambda public_ip, local_rtp_port, **kwargs: "v=0")
        monkeypatch.setattr(server_mod, "build_sip_response", lambda code, reason, headers, sdp_body=None, contact_uri="": f"SIP/2.0 {code} {reason}")
        monkeypatch.setattr(server_mod.random, "randint", lambda start, end: 12000)
        monkeypatch.setattr(server, "handle_invite", lambda *args, **kwargs: fake_session)

        task = asyncio.create_task(protocol._handle_invite("INVITE sip:test SIP/2.0", ("1.2.3.4", 5060)))
        await asyncio.sleep(0)

        assert "SIP/2.0 100 Trying" in sent_messages
        assert "SIP/2.0 200 OK" not in sent_messages

        ready.set()
        await task

        assert "SIP/2.0 200 OK" in sent_messages
        assert fake_session.answered is True

    def test_bye_response_logs_ack_and_clears_pending(self, caplog):
        from sip_bridge.server import PendingByeTransaction, SIPProtocol, SIPServer

        server = SIPServer(config=self._make_config())
        protocol = SIPProtocol(server)
        server._pending_byes["call-bye-1"] = PendingByeTransaction(
            cseq=2,
            reason="callback_registered",
            remote_addr=("1.2.3.4", 5060),
            request_bytes=b"BYE",
        )

        with caplog.at_level(logging.INFO):
            protocol.datagram_received(
                (
                    "SIP/2.0 200 OK\r\n"
                    "Call-ID: call-bye-1\r\n"
                    "CSeq: 2 BYE\r\n"
                    "Content-Length: 0\r\n\r\n"
                ).encode(),
                ("1.2.3.4", 5060),
            )

        assert "SIP BYE acknowledged" in caplog.text
        assert "call-bye-1" not in server._pending_byes

    def test_request_hangup_schedules_bye_retransmission(self, monkeypatch):
        from sip_bridge.server import ActiveSIPDialog, SIPServer

        server = SIPServer(config=self._make_config())
        transport = MagicMock()
        server._transport = transport
        server._dialogs["call-bye-2"] = ActiveSIPDialog(
            remote_addr=("1.2.3.4", 5060),
            request_uri="sip:+2348012345678@example.com",
            local_from_header="<sip:service@example.com>;tag=local",
            remote_to_header="<sip:+2348012345678@example.com>;tag=remote",
            call_id="call-bye-2",
            next_local_cseq=2,
            contact_uri="<sip:service@example.com>",
        )
        scheduled: list[tuple[float, object, tuple[object, ...], MagicMock]] = []

        class _FakeLoop:
            def call_later(self, delay, callback, *args):
                handle = MagicMock()
                scheduled.append((delay, callback, args, handle))
                return handle

        monkeypatch.setattr("sip_bridge.server.asyncio.get_running_loop", lambda: _FakeLoop())

        server.request_hangup("call-bye-2", reason="callback_registered")

        assert transport.sendto.call_count == 1
        assert scheduled
        delay, callback, args, _handle = scheduled[0]
        assert delay > 0
        callback(*args)
        assert transport.sendto.call_count == 2


# ---------------------------------------------------------------------------
# Greeting lock: data-driven release via outbound RTP drain
# ---------------------------------------------------------------------------

class TestGreetingLockDrainRelease:
    """Greeting lock releases when outbound audio stops flowing, not on a timer."""

    @pytest.mark.asyncio
    async def test_greeting_lock_releases_after_outbound_drain(self):
        """Lock releases when no RTP sent for >0.5s after agent_status=idle."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._greeting_lock_active = True
        s._greeting_lock_pending_release = True
        s._greeting_lock_safety_deadline = time.monotonic() + 10.0
        # Simulate last RTP frame sent 0.6s ago
        s._last_outbound_rtp_sent_at = time.monotonic() - 0.6

        mock_client = MockGatewayClient()
        sent = []

        async def send_and_stop(data):
            sent.append(data)
            s._shutdown.set()

        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client

        pcm16 = b"\x01\x02" * 320
        await s._gemini_in_queue.put(pcm16)
        await s._gateway_send_loop()

        # Lock should have been released — real audio sent, not silence
        assert s._greeting_lock_active is False
        assert s._greeting_lock_pending_release is False
        assert sent[0] == pcm16

    @pytest.mark.asyncio
    async def test_greeting_lock_holds_while_outbound_still_sending(self):
        """Lock stays active when RTP was sent recently (<0.5s ago)."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._greeting_lock_active = True
        s._greeting_lock_pending_release = True
        s._greeting_lock_safety_deadline = time.monotonic() + 10.0
        # Last RTP sent just 0.1s ago — still draining
        s._last_outbound_rtp_sent_at = time.monotonic() - 0.1

        mock_client = MockGatewayClient()
        sent = []

        async def send_and_stop(data):
            sent.append(data)
            s._shutdown.set()

        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client

        pcm16 = b"\x01\x02" * 320
        await s._gemini_in_queue.put(pcm16)
        await s._gateway_send_loop()

        # Lock still active — silence sent, not real audio
        assert s._greeting_lock_active is True
        assert sent[0] == SILENCE_FRAME

    @pytest.mark.asyncio
    async def test_greeting_lock_safety_timeout_releases(self):
        """Safety deadline releases lock even if no RTP was ever sent."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._greeting_lock_active = True
        s._greeting_lock_pending_release = True
        # Safety deadline already passed
        s._greeting_lock_safety_deadline = time.monotonic() - 1.0
        # No outbound RTP ever sent
        s._last_outbound_rtp_sent_at = 0.0

        mock_client = MockGatewayClient()
        sent = []

        async def send_and_stop(data):
            sent.append(data)
            s._shutdown.set()

        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client

        pcm16 = b"\x01\x02" * 320
        await s._gemini_in_queue.put(pcm16)
        await s._gateway_send_loop()

        # Safety timeout fired — lock released
        assert s._greeting_lock_active is False
        assert sent[0] == pcm16

    @pytest.mark.asyncio
    async def test_agent_status_idle_sets_pending_release_with_safety_deadline(self):
        """agent_status:idle should defer release, not release immediately."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._greeting_lock_active = True
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "agent_status", "status": "idle"}),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        # Lock still active — only pending release set
        assert s._greeting_lock_active is True
        assert s._greeting_lock_pending_release is True
        assert s._greeting_lock_safety_deadline > time.monotonic()

    @pytest.mark.asyncio
    async def test_interrupted_clears_greeting_lock_immediately(self):
        """interrupted event should clear lock and pending release instantly."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._greeting_lock_active = True
        s._greeting_lock_pending_release = True
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "interrupted"}),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert s._greeting_lock_active is False
        assert s._greeting_lock_pending_release is False


class TestCallbackEndAfterSpeaking:
    """Callback acknowledgement should end the call after speech drains."""

    @pytest.mark.asyncio
    async def test_call_control_end_after_speaking_sets_shutdown_after_idle_and_drain(self):
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        hangups: list[str] = []
        s.request_hangup = lambda reason: hangups.append(reason)
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps(
                    {
                        "type": "call_control",
                        "action": "end_after_speaking",
                        "reason": "callback_registered",
                    }
                ),
            ),
            GatewayFrame(is_audio=True, audio_data=b"\x00" * 960),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "agent_status", "status": "idle"}),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert s._end_after_speaking_pending is True
        assert s._end_after_speaking_audio_seen is True
        assert s._end_after_speaking_idle_seen is True

        s._last_outbound_rtp_sent_at = time.monotonic() - 0.6
        s._maybe_finish_end_after_speaking()

        assert s._shutdown.is_set() is True
        assert hangups == ["outbound audio drained"]

    @pytest.mark.asyncio
    async def test_call_control_end_after_speaking_times_out_without_audio(self):
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        hangups: list[str] = []
        s.request_hangup = lambda reason: hangups.append(reason)
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps(
                    {
                        "type": "call_control",
                        "action": "end_after_speaking",
                        "reason": "callback_registered",
                    }
                ),
            ),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "agent_status", "status": "idle"}),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert s._end_after_speaking_audio_seen is False
        s._end_after_speaking_deadline = time.monotonic() - 0.1
        s._maybe_finish_end_after_speaking()

        assert s._shutdown.is_set() is True
        assert hangups == ["safety timeout"]

    @pytest.mark.asyncio
    async def test_call_control_end_after_speaking_hangs_up_when_audio_finished_before_control(self):
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        hangups: list[str] = []
        s.request_hangup = lambda reason: hangups.append(reason)
        s._last_outbound_rtp_sent_at = time.monotonic()
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps(
                    {
                        "type": "call_control",
                        "action": "end_after_speaking",
                        "reason": "callback_acknowledged",
                    }
                ),
            ),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "agent_status", "status": "idle"}),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert s._end_after_speaking_pending is True
        assert s._end_after_speaking_audio_seen is False
        assert s._end_after_speaking_idle_seen is True

        s._last_outbound_rtp_sent_at = time.monotonic() - 0.6
        s._maybe_finish_end_after_speaking()

        assert s._shutdown.is_set() is True
        assert hangups == ["outbound audio drained"]


class TestInputDenoise:
    def test_init_uses_conservative_gate_when_webrtc_apm_unavailable(self, monkeypatch):
        import sip_bridge.session as session_mod

        monkeypatch.setenv("SIP_DENOISE_ENABLED", "1")
        monkeypatch.setenv("SIP_WEBRTC_APM_ENABLED", "1")
        monkeypatch.delenv("SIP_DENOISE_GATE_MULTIPLIER", raising=False)
        monkeypatch.delenv("SIP_DENOISE_MIN_RMS", raising=False)
        monkeypatch.delenv("SIP_DENOISE_ATTACK_RMS", raising=False)
        monkeypatch.delenv("SIP_DENOISE_ATTENUATION", raising=False)
        monkeypatch.setattr(session_mod, "AudioProcessor", None)

        s = session_mod.CallSession(call_id="c1", tenant_id="public", company_id="acme")

        assert s._webrtc_apm_enabled is True
        assert s._webrtc_apm is None
        assert s._noise_gate_multiplier == 1.25
        assert s._noise_gate_min_rms == 45.0
        assert s._noise_gate_attack_rms == 90.0
        assert s._noise_gate_attenuation == 0.35

    def test_init_uses_conservative_gate_when_webrtc_apm_init_fails(self, monkeypatch):
        import sip_bridge.session as session_mod

        class _BrokenAPM:
            def __init__(self, **_kwargs):
                raise RuntimeError("boom")

        monkeypatch.setenv("SIP_DENOISE_ENABLED", "1")
        monkeypatch.setenv("SIP_WEBRTC_APM_ENABLED", "1")
        monkeypatch.delenv("SIP_DENOISE_GATE_MULTIPLIER", raising=False)
        monkeypatch.delenv("SIP_DENOISE_MIN_RMS", raising=False)
        monkeypatch.delenv("SIP_DENOISE_ATTACK_RMS", raising=False)
        monkeypatch.delenv("SIP_DENOISE_ATTENUATION", raising=False)
        monkeypatch.setattr(session_mod, "AudioProcessor", _BrokenAPM)

        s = session_mod.CallSession(call_id="c1", tenant_id="public", company_id="acme")

        assert s._webrtc_apm is None
        assert s._webrtc_apm_frame_size_bytes == 0
        assert s._noise_gate_multiplier == 1.25
        assert s._noise_gate_min_rms == 45.0
        assert s._noise_gate_attack_rms == 90.0
        assert s._noise_gate_attenuation == 0.35

    def test_webrtc_apm_processes_20ms_frame_in_two_10ms_chunks(self):
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")

        class _FakeAPM:
            def __init__(self):
                self.calls: list[bytes] = []

            def process_stream(self, chunk: bytes) -> bytes:
                self.calls.append(chunk)
                return chunk

        fake_apm = _FakeAPM()
        s._webrtc_apm = fake_apm
        s._webrtc_apm_frame_size_bytes = 320

        frame = b"\x01\x00" * 320
        denoised = s._apply_input_denoise(frame)

        assert denoised == frame
        assert len(fake_apm.calls) == 2
        assert all(len(chunk) == 320 for chunk in fake_apm.calls)

    def test_noise_gate_attenuates_quiet_frames(self):
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._webrtc_apm = None
        s._denoise_enabled = True
        s._noise_floor_rms = 100.0
        s._noise_gate_multiplier = 1.5
        s._noise_gate_min_rms = 100.0
        s._noise_gate_attack_rms = 200.0
        s._noise_gate_attenuation = 0.1

        quiet = (10).to_bytes(2, "little", signed=True) * 320
        denoised = s._apply_input_denoise(quiet)

        assert denoised != quiet
        assert s._noise_gate_suppressed_frames == 1

    def test_noise_gate_keeps_strong_speech(self):
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._webrtc_apm = None
        s._denoise_enabled = True
        s._noise_floor_rms = 100.0
        s._noise_gate_multiplier = 1.5
        s._noise_gate_min_rms = 100.0
        s._noise_gate_attack_rms = 200.0
        s._noise_gate_attenuation = 0.1

        loud = (1000).to_bytes(2, "little", signed=True) * 320
        denoised = s._apply_input_denoise(loud)

        assert denoised == loud
