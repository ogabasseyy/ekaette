"""Tests for CallSession + SIPServer gateway mode.

Phase 3 of Single AI Brain — CallSession routes through Cloud Run WebSocket
instead of direct Gemini Live when gateway_mode=True.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

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
        assert s._greeting_lock_active is True

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
        assert s._greeting_lock_active is True

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
            live_model_id="gemini-2.5-flash-native-audio-preview-12-2025",
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
            live_model_id="gemini-2.5-flash-native-audio-preview-12-2025",
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
            live_model_id="gemini-2.5-flash-native-audio-preview-12-2025",
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
        monkeypatch.setattr(server_mod, "build_sdp_answer", lambda public_ip, local_rtp_port: "v=0")
        monkeypatch.setattr(server_mod, "build_sip_response", lambda code, reason, headers, sdp_body=None, contact_uri="": f"SIP/2.0 {code} {reason}")
        monkeypatch.setattr(server_mod.random, "randint", lambda start, end: 12000)
        monkeypatch.setattr("sip_bridge.session.CallSession.run", _fake_run)
        real_create_task = asyncio.create_task

        def _capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        monkeypatch.setattr(server_mod.asyncio, "create_task", _capture_task)

        protocol._handle_invite("INVITE sip:test SIP/2.0", ("1.2.3.4", 5060))
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
        monkeypatch.setattr(server_mod, "build_sdp_answer", lambda public_ip, local_rtp_port: "v=0")
        monkeypatch.setattr(server_mod, "build_sip_response", lambda code, reason, headers, sdp_body=None, contact_uri="": f"SIP/2.0 {code} {reason}")
        monkeypatch.setattr(server_mod.random, "randint", lambda start, end: 12000)
        monkeypatch.setattr("sip_bridge.session.CallSession.run", _fake_run)
        real_create_task = asyncio.create_task

        def _capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        monkeypatch.setattr(server_mod.asyncio, "create_task", _capture_task)

        protocol._handle_invite("INVITE sip:test SIP/2.0", ("1.2.3.4", 5060))
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
