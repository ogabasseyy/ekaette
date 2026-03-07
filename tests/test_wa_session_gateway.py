"""Tests for WaSession gateway mode.

Phase 4 of Single AI Brain — WaSession routes through Cloud Run WebSocket
instead of direct Gemini Live when gateway_mode=True.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from sip_bridge.wa_session import WaSession


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
        self.session_id = "mock-session"
        self._canonical_session_id = ""
        self._resumption_token = ""
        self._frames_to_yield: list = []

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
        for frame in self._frames_to_yield:
            yield frame


# ---------------------------------------------------------------------------
# Gateway mode tests
# ---------------------------------------------------------------------------

class TestWaSessionGatewayMode:
    """WaSession in gateway_mode uses GatewayClient instead of Gemini."""

    def test_gateway_client_field_exists(self):
        """WaSession has gateway_client field."""
        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        assert hasattr(s, "gateway_client")

    @pytest.mark.asyncio
    async def test_gateway_send_loop_sends_audio(self):
        """Audio from _gemini_in_queue sent to gateway."""
        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        original_send = mock_client.send_audio
        async def send_and_stop(data):
            await original_send(data)
            s._shutdown.set()
        mock_client.send_audio = send_and_stop
        s.gateway_client = mock_client
        s._model_speaking = False
        s._model_speech_end_time = 0.0

        pcm16 = b"\x01\x02" * 320
        await s._gemini_in_queue.put(pcm16)

        await s._gateway_send_loop()
        original_send.assert_called_once_with(pcm16)

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_routes_audio(self):
        """Audio from gateway goes to outbound_queue."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        audio = b"\x00" * 960
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(is_audio=True, audio_data=audio),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()
        assert s.outbound_queue.get_nowait() == audio

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_handles_interrupted(self):
        """interrupted clears model speaking state."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
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
    async def test_gateway_recv_loop_sends_virtual_assistant_greeting_once(self):
        """session_started sends the greeting once and remembers the session."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
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

        mock_client.send_text.assert_awaited_once()
        actual_payload = mock_client.send_text.await_args.args[0]
        assert json.loads(actual_payload) == {
            "type": "text",
            "text": "[Call connected]",
        }
        assert mock_client.canonical_session_id == "canonical-xyz"
        assert s._gateway_greeting_sent is True
        assert s._model_speaking is True

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_skips_duplicate_greeting_on_resume(self):
        """A resumed gateway session does not re-trigger the call greeting."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-1",
                }),
            ),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "interrupted"}),
            ),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-2",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        assert mock_client.send_text.await_count == 1
        assert mock_client.canonical_session_id == "canonical-2"
        assert s._gateway_greeting_sent is True
        assert s._model_speaking is False

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_marks_failed_greeting_as_sent(self):
        """A failed initial greeting should not be retried on later resumes."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client.send_text = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-xyz",
                }),
            ),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "interrupted"}),
            ),
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-resumed",
                }),
            ),
        ]
        s.gateway_client = mock_client

        await s._gateway_recv_loop()

        mock_client.send_text.assert_awaited_once()
        assert mock_client.canonical_session_id == "canonical-resumed"
        assert s._gateway_greeting_sent is True
        assert s._model_speaking is False

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_session_ending_shuts_down(self):
        """session_ending with live_session_ended triggers shutdown."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
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
    async def test_gateway_mode_skips_tool_handling(self):
        """In gateway mode, tool calls are handled by ADK, not locally."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({"type": "tool_call", "name": "send_whatsapp_message"}),
            ),
        ]
        s.gateway_client = mock_client
        with patch.object(WaSession, "_handle_tool_call", new=AsyncMock()) as mock_tool_call:
            await s._gateway_recv_loop()
        mock_tool_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# WA early gateway-failure termination
# ---------------------------------------------------------------------------

class TestWaGatewayEarlyFailure:
    """WaSession gateway connect failure calls _cleanup_transport + _write_call_end."""

    @pytest.mark.asyncio
    async def test_gateway_connect_failure_calls_write_call_end(self):
        """Failed gateway connect writes terminal Firestore state."""
        from unittest.mock import MagicMock, patch

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("refused"))
        s.gateway_client = mock_client

        with patch.object(WaSession, "_write_call_end") as mock_end, \
             patch.object(WaSession, "_write_call_start"):
            await s.run()

            mock_end.assert_called_once()
            duration = mock_end.call_args[0][0]
            assert duration >= 0

    @pytest.mark.asyncio
    async def test_gateway_connect_failure_cleans_transport(self):
        """Failed gateway connect cleans up owned transport."""
        import socket
        from unittest.mock import MagicMock, patch

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("refused"))
        s.gateway_client = mock_client

        # Simulate an owned transport
        mock_sock = MagicMock(spec=socket.socket)
        s.media_transport = mock_sock
        s._owns_transport = True

        with patch.object(WaSession, "_write_call_start"), \
             patch.object(WaSession, "_write_call_end"):
            await s.run()

        mock_sock.close.assert_called_once()
        assert s.media_transport is None

    @pytest.mark.asyncio
    async def test_gateway_connect_failure_returns_early(self):
        """Failed gateway connect returns before entering TaskGroup."""
        from unittest.mock import MagicMock, patch

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client.connect = AsyncMock(side_effect=OSError("network down"))
        s.gateway_client = mock_client

        # Track if _media_recv_loop is called (only happens inside TaskGroup)
        recv_called = False

        async def _trap_recv(_self):
            nonlocal recv_called
            recv_called = True

        with patch.object(WaSession, "_write_call_start"), \
             patch.object(WaSession, "_write_call_end"), \
             patch.object(WaSession, "_media_recv_loop", _trap_recv):
            await s.run()

        assert not recv_called, "TaskGroup should not be entered on gateway connect failure"
