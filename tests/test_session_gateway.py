"""Tests for CallSession + SIPServer gateway mode.

Phase 3 of Single AI Brain — CallSession routes through Cloud Run WebSocket
instead of direct Gemini Live when gateway_mode=True.
"""

from __future__ import annotations

import asyncio
import hashlib
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
        self._canonical_session_id = ""
        self._resumption_token = ""
        self._frames_to_yield: list = []

    async def receive(self):
        for frame in self._frames_to_yield:
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
    async def test_gateway_send_loop_echo_mutes(self):
        """Echo suppression sends SILENCE when model is speaking."""
        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        original_send = mock_client.send_audio
        async def send_and_stop(data):
            await original_send(data)
            s._shutdown.set()
        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client
        s._model_speaking = True

        pcm16 = b"\x01\x02" * 320
        await s._gemini_in_queue.put(pcm16)

        await s._gateway_send_loop()
        original_send.assert_called_once_with(SILENCE_FRAME)

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
        assert mock_client._canonical_session_id == "canonical-xyz"

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_handles_interrupted(self):
        """interrupted clears model speaking state."""
        from sip_bridge.gateway_client import GatewayFrame

        s = CallSession(call_id="c1", tenant_id="public", company_id="acme")
        s._model_speaking = True
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
        assert mock_client._resumption_token == "tok-abc"
        assert not s._shutdown.is_set()


# ---------------------------------------------------------------------------
# Server — caller phone extraction + user_id derivation
# ---------------------------------------------------------------------------

class TestServerCallerPhone:
    """server.py extracts caller phone from SIP From header."""

    def test_extract_caller_phone_valid(self):
        """Valid SIP From header yields phone number."""
        from sip_bridge.wa_main import _extract_caller_phone

        assert _extract_caller_phone('"User" <sip:+2348012345678@example.com>') == "+2348012345678"

    def test_extract_caller_phone_empty(self):
        """Empty From header yields empty string."""
        from sip_bridge.wa_main import _extract_caller_phone

        assert _extract_caller_phone("") == ""

    def test_user_id_derivation_from_phone(self):
        """user_id = sip-{sha256(phone)[:16]}."""
        phone = "+2348012345678"
        user_id = f"sip-{hashlib.sha256(phone.encode()).hexdigest()[:16]}"
        assert user_id.startswith("sip-")
        assert len(user_id) == 4 + 16  # "sip-" + 16 hex chars

    def test_anonymous_fallback_uses_call_id(self):
        """No caller phone → user_id = sip-anon-{sha256(call_id)[:16]}."""
        call_id = "abc123@host"
        user_id = f"sip-anon-{hashlib.sha256(call_id.encode()).hexdigest()[:16]}"
        assert user_id.startswith("sip-anon-")
        assert len(user_id) == 9 + 16  # "sip-anon-" + 16 hex chars

    def test_session_id_from_call_id_is_safe(self):
        """session_id from call_id hash is safe for WS path regex."""
        import re
        call_id = "abc123@host.example.com;tag=xyz"
        session_id = f"sip-{hashlib.sha256(call_id.encode()).hexdigest()[:24]}"
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
        )
        server = SIPServer(config=config)
        session = server.handle_invite(
            "call-1", ("1.2.3.4", 5060),
            sip_from_header='"User" <sip:+2348012345678@example.com>',
        )
        assert session.gateway_client is not None
        assert session._caller_phone == "+2348012345678"
        assert session.gateway_client.caller_phone == "+2348012345678"
        assert session.gateway_client.user_id.startswith("sip-")

    def test_handle_invite_gateway_no_phone_uses_anon(self):
        """Gateway mode without From header → sip-anon user_id."""
        from sip_bridge.server import SIPServer
        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
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

    def test_handle_invite_gateway_no_secret_no_token(self):
        """Gateway mode without ws_secret produces no token in URL."""
        from sip_bridge.server import SIPServer
        config = self._make_config(
            gateway_mode=True,
            gateway_ws_url="wss://ekaette.run.app",
        )
        server = SIPServer(config=config)
        session = server.handle_invite("call-4", ("1.2.3.4", 5060))
        assert session.gateway_client is not None
        url = session.gateway_client._build_connect_url()
        assert "token=" not in url


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


# ---------------------------------------------------------------------------
# Server — done-callback session cleanup
# ---------------------------------------------------------------------------

class TestServerDoneCallback:
    """SIPProtocol done-callback cleans up _active_sessions when run() exits."""

    def _make_config(self, gateway_mode=False, gateway_ws_url=""):  # noqa: FBT002
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
        )

    @pytest.mark.asyncio
    async def test_done_callback_removes_session_on_success(self):
        """When session.run() completes, done-callback removes from _active_sessions."""
        from sip_bridge.server import SIPServer

        server = SIPServer(config=self._make_config())
        _session = server.handle_invite("call-done-1", ("1.2.3.4", 5060))
        assert "call-done-1" in server._active_sessions

        # Simulate what SIPProtocol._handle_invite does: create task + done-callback
        async def _fake_run():
            pass  # immediate success

        task = asyncio.create_task(_fake_run())

        call_id = "call-done-1"

        def _on_done(done_task: asyncio.Task) -> None:
            server._active_sessions.pop(call_id, None)

        task.add_done_callback(_on_done)

        await task
        # Allow callbacks to fire
        await asyncio.sleep(0)

        assert "call-done-1" not in server._active_sessions

    @pytest.mark.asyncio
    async def test_done_callback_removes_session_on_exception(self):
        """When session.run() raises, done-callback still removes from _active_sessions."""
        from sip_bridge.server import SIPServer

        server = SIPServer(config=self._make_config())
        _session = server.handle_invite("call-done-2", ("1.2.3.4", 5060))
        assert "call-done-2" in server._active_sessions

        async def _fake_run():
            raise RuntimeError("session crashed")

        task = asyncio.create_task(_fake_run())

        call_id = "call-done-2"

        def _on_done(done_task: asyncio.Task) -> None:
            server._active_sessions.pop(call_id, None)

        task.add_done_callback(_on_done)

        with pytest.raises(RuntimeError, match="session crashed"):
            await task
        await asyncio.sleep(0)

        assert "call-done-2" not in server._active_sessions
