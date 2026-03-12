"""Tests for WaSession gateway mode.

Phase 4 of Single AI Brain — WaSession routes through Cloud Run WebSocket
instead of direct Gemini Live when gateway_mode=True.
"""

from __future__ import annotations

import json
import struct
from unittest.mock import AsyncMock, patch

import pytest

from sip_bridge.wa_session import SILENCE_FRAME
from sip_bridge.wa_session import WaSession
from sip_bridge.wa_gateway import _gateway_send_loop, _gateway_recv_loop


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

        await _gateway_send_loop(s)
        original_send.assert_called_once_with(pcm16)

    @pytest.mark.asyncio
    async def test_gateway_send_loop_mutes_only_during_greeting_lock(self):
        """Greeting lock should mute caller audio during the non-interruptible greeting."""
        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
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

        await _gateway_send_loop(s)
        original_send.assert_called_once_with(SILENCE_FRAME)

    @pytest.mark.asyncio
    async def test_gateway_send_loop_keeps_caller_audio_after_greeting(self):
        """Post-greeting speech should remain interruptible."""
        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
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

        await _gateway_send_loop(s)
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

        await _gateway_recv_loop(s)
        assert s.outbound_queue.get_nowait() == audio

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_downmixes_audio_when_configured(self, monkeypatch):
        """Gateway audio is downmixed before entering the outbound queue when configured."""
        from sip_bridge.gateway_client import GatewayFrame
        import sip_bridge.wa_session as wa_session

        monkeypatch.setattr(wa_session, "MODEL_OUTPUT_CHANNELS", 2)
        s = wa_session.WaSession(call_id="c1", tenant_id="public", company_id="acme")
        stereo = struct.pack("<4h", 1000, 3000, -1000, 1000)
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(is_audio=True, audio_data=stereo),
        ]
        s.gateway_client = mock_client

        await _gateway_recv_loop(s)
        assert s.outbound_queue.get_nowait() == struct.pack("<2h", 2000, 0)

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_handles_interrupted(self):
        """interrupted clears model speaking state."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
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

        await _gateway_recv_loop(s)
        assert s._model_speaking is False
        assert s.outbound_queue.empty()

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

        await _gateway_recv_loop(s)

        mock_client.send_text.assert_awaited_once()
        actual_payload = mock_client.send_text.await_args.args[0]
        assert json.loads(actual_payload) == {
            "type": "system_text",
            "text": "Call connected",
        }
        assert mock_client.canonical_session_id == "canonical-xyz"
        assert s._gateway_greeting_sent is True
        assert s._model_speaking is False
        assert s._greeting_lock_active is True

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

        await _gateway_recv_loop(s)

        assert mock_client.send_text.await_count == 1
        assert mock_client.canonical_session_id == "canonical-2"
        assert s._gateway_greeting_sent is True
        assert s._model_speaking is False
        assert s._greeting_lock_active is False

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

        await _gateway_recv_loop(s)

        mock_client.send_text.assert_awaited_once()
        assert mock_client.canonical_session_id == "canonical-resumed"
        assert s._gateway_greeting_sent is True
        assert s._model_speaking is False
        assert s._greeting_lock_active is False

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_failed_greeting_resets_lock(self):
        """A failed greeting send must reset _greeting_lock_active to avoid permanent muting."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client.send_text = AsyncMock(side_effect=RuntimeError("boom"))
        # Only session_started, no interrupted frame to rescue
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

        await _gateway_recv_loop(s)

        assert s._gateway_greeting_sent is True
        assert s._greeting_lock_active is False, (
            "_greeting_lock_active must be reset on failed greeting to avoid permanent muting"
        )

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_session_started_none_client(self):
        """session_started with None gateway_client should not raise."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_started",
                    "sessionId": "canonical-abc",
                }),
            ),
        ]
        s.gateway_client = mock_client

        # Simulate gateway_client becoming None mid-stream by replacing
        # the actual client's remember method to set gateway_client to None
        original_remember = mock_client.remember_canonical_session_id
        def remember_and_clear(sid):
            original_remember(sid)
            s.gateway_client = None
        mock_client.remember_canonical_session_id = remember_and_clear

        # Should not raise AttributeError
        await _gateway_recv_loop(s)

    @pytest.mark.asyncio
    async def test_gateway_recv_loop_session_resumption_none_client(self):
        """session_resumption with None gateway_client should not raise."""
        from sip_bridge.gateway_client import GatewayFrame

        s = WaSession(call_id="c1", tenant_id="public", company_id="acme")
        mock_client = MockGatewayClient()
        mock_client._frames_to_yield = [
            GatewayFrame(
                is_audio=False,
                text_data=json.dumps({
                    "type": "session_ending",
                    "reason": "session_resumption",
                    "resumptionToken": "tok-123",
                }),
            ),
        ]
        # Set gateway_client to None to trigger the bug
        s.gateway_client = mock_client

        # Patch receive to use our mock but gateway_client is None for the method call
        original_receive = mock_client.receive
        async def receive_then_clear():
            async for frame in original_receive():
                s.gateway_client = None
                yield frame
        mock_client.receive = receive_then_clear

        # Should not raise AttributeError
        await _gateway_recv_loop(s)

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

        await _gateway_recv_loop(s)
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
            await _gateway_recv_loop(s)
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

        # Track if media_recv_loop is called (only happens inside TaskGroup)
        recv_called = False

        async def _trap_recv(_session):
            nonlocal recv_called
            recv_called = True

        with patch.object(WaSession, "_write_call_start"), \
             patch.object(WaSession, "_write_call_end"), \
             patch("sip_bridge.wa_media_pipeline.media_recv_loop", _trap_recv):
            await s.run()

        assert not recv_called, "TaskGroup should not be entered on gateway connect failure"
