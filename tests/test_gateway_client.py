"""Tests for sip_bridge.gateway_client — WebSocket client bridging SIP → Cloud Run.

Phase 1 of Single AI Brain architecture.
TDD red → write tests first, then implement.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from sip_bridge.gateway_client import (
    GatewayClient,
    GatewayConnectionError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockWebSocket:
    """Mock WebSocket that supports async iteration and tracks calls."""

    def __init__(self, messages: list | None = None):
        self._messages = messages or []
        self.send = AsyncMock()
        self.close = AsyncMock()

    def __aiter__(self):
        return self._async_gen()

    async def _async_gen(self):
        for msg in self._messages:
            yield msg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> GatewayClient:
    """Create a GatewayClient with test defaults."""
    return GatewayClient(
        gateway_ws_url="wss://ekaette-test.run.app",
        user_id="sip-abc123",
        session_id="sip-def456",
        tenant_id="public",
        company_id="ekaette-electronics",
        industry="electronics",
    )


@pytest.fixture
def client_with_phone() -> GatewayClient:
    """GatewayClient with caller_phone set."""
    return GatewayClient(
        gateway_ws_url="wss://ekaette-test.run.app",
        user_id="sip-abc123",
        session_id="sip-def456",
        caller_phone="+2348012345678",
    )


# ---------------------------------------------------------------------------
# 1. Connection URL construction
# ---------------------------------------------------------------------------

class TestConnectionURL:
    def test_connect_url_includes_path_and_query_params(self, client: GatewayClient):
        """URL must include /ws/{user_id}/{session_id} with tenant/company/industry."""
        url = client._build_connect_url()
        assert "/ws/sip-abc123/sip-def456" in url
        assert "tenantId=public" in url
        assert "companyId=ekaette-electronics" in url
        assert "industry=electronics" in url

    def test_connect_url_includes_caller_phone(self, client_with_phone: GatewayClient):
        """caller_phone query param present when set."""
        url = client_with_phone._build_connect_url()
        assert "caller_phone=%2B2348012345678" in url

    def test_connect_url_omits_empty_caller_phone(self, client: GatewayClient):
        """caller_phone param absent when empty."""
        url = client._build_connect_url()
        assert "caller_phone" not in url

    def test_connect_url_includes_minted_token_when_secret_set(self):
        """Token query param present when ws_secret is provided (per-call mint)."""
        c = GatewayClient(
            gateway_ws_url="wss://test.run.app",
            user_id="u1",
            session_id="s1",
            ws_secret="test-shared-secret",
        )
        url = c._build_connect_url()
        assert "token=" in url
        # Token should NOT be the raw secret
        assert "test-shared-secret" not in url

    def test_connect_url_omits_token_when_no_secret(self):
        """No token param when ws_secret is empty."""
        c = GatewayClient(
            gateway_ws_url="wss://test.run.app",
            user_id="u1",
            session_id="s1",
        )
        url = c._build_connect_url()
        assert "token=" not in url

    def test_minted_tokens_have_unique_jti(self):
        """Each call to _mint_token produces a unique JTI."""
        c = GatewayClient(
            gateway_ws_url="wss://test.run.app",
            user_id="u1",
            session_id="s1",
            ws_secret="test-secret",
        )
        t1 = c._mint_token()
        t2 = c._mint_token()
        assert t1 != t2  # different JTI each time

    def test_minted_token_validates_with_ws_auth(self):
        """Token minted by GatewayClient validates with ws_auth.validate_ws_token."""
        from app.api.v1.public import ws_auth

        secret = "shared-hmac-secret-for-test"
        c = GatewayClient(
            gateway_ws_url="wss://test.run.app",
            user_id="sip-abc123",
            session_id="s1",
            tenant_id="public",
            company_id="acme",
            ws_secret=secret,
        )
        token = c._mint_token()
        assert token  # non-empty

        # Set the module-level secret so validate_ws_token can verify
        original_secret = ws_auth._WS_TOKEN_SECRET
        try:
            ws_auth._WS_TOKEN_SECRET = secret
            claims = ws_auth.validate_ws_token(token, expected_user_id="sip-abc123")
            assert claims is not None
            assert claims.sub == "sip-abc123"
            assert claims.tenant_id == "public"
            assert claims.company_id == "acme"
        finally:
            ws_auth._WS_TOKEN_SECRET = original_secret


# ---------------------------------------------------------------------------
# 2. Connect + handshake
# ---------------------------------------------------------------------------

class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_calls_websockets_connect(self, client: GatewayClient):
        """connect() should open a websocket to the built URL."""
        mock_ws = AsyncMock()
        mock_ws.__aiter__ = AsyncMock(return_value=iter([]))
        with patch("sip_bridge.gateway_client.websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws
            await client.connect()
            mock_connect.assert_called_once()
            call_url = mock_connect.call_args[0][0]
            assert "/ws/sip-abc123/sip-def456" in call_url

    @pytest.mark.asyncio
    async def test_connect_raises_on_failure(self, client: GatewayClient):
        """Connection failure raises GatewayConnectionError."""
        with patch("sip_bridge.gateway_client.websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = OSError("Connection refused")
            with pytest.raises(GatewayConnectionError):
                await client.connect()

    @pytest.mark.asyncio
    async def test_connect_timeout_raises(self, client: GatewayClient):
        """Connection timeout raises GatewayConnectionError."""
        with patch("sip_bridge.gateway_client.websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = asyncio.TimeoutError()
            with pytest.raises(GatewayConnectionError):
                await client.connect()


# ---------------------------------------------------------------------------
# 3. Send audio
# ---------------------------------------------------------------------------

class TestSendAudio:
    @pytest.mark.asyncio
    async def test_send_audio_sends_binary_frame(self, client: GatewayClient):
        """send_audio sends PCM16 bytes as a binary WebSocket frame."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        pcm16 = b"\x00" * 640
        await client.send_audio(pcm16)
        mock_ws.send.assert_called_once_with(pcm16)


# ---------------------------------------------------------------------------
# 4. Send text
# ---------------------------------------------------------------------------

class TestSendText:
    @pytest.mark.asyncio
    async def test_send_text_sends_json_frame(self, client: GatewayClient):
        """send_text sends a text WebSocket frame."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        msg = json.dumps({"type": "negotiate"})
        await client.send_text(msg)
        mock_ws.send.assert_called_once_with(msg)


# ---------------------------------------------------------------------------
# 5. Receive — binary (audio) vs text (JSON)
# ---------------------------------------------------------------------------

class TestReceive:
    @pytest.mark.asyncio
    async def test_receive_binary_yields_audio_frame(self, client: GatewayClient):
        """Binary WebSocket frames yield GatewayFrame(is_audio=True)."""
        audio_data = b"\x01\x02" * 480
        client._ws = MockWebSocket(messages=[audio_data])

        frames = []
        async for frame in client.receive():
            frames.append(frame)
        assert len(frames) == 1
        assert frames[0].is_audio is True
        assert frames[0].audio_data == audio_data

    @pytest.mark.asyncio
    async def test_receive_text_yields_json_frame(self, client: GatewayClient):
        """Text WebSocket frames yield GatewayFrame(is_audio=False)."""
        text_msg = json.dumps({"type": "session_started", "sessionId": "xyz"})
        client._ws = MockWebSocket(messages=[text_msg])

        frames = []
        async for frame in client.receive():
            frames.append(frame)
        assert len(frames) == 1
        assert frames[0].is_audio is False
        assert frames[0].text_data == text_msg


# ---------------------------------------------------------------------------
# 6. Close
# ---------------------------------------------------------------------------

class TestClose:
    @pytest.mark.asyncio
    async def test_close_closes_websocket(self, client: GatewayClient):
        """close() calls ws.close()."""
        mock_ws = AsyncMock()
        client._ws = mock_ws
        await client.close()
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_noop_when_not_connected(self, client: GatewayClient):
        """close() is safe to call when no connection exists."""
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# 7. Reconnect with canonical session ID + resumption token
# ---------------------------------------------------------------------------

class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_uses_canonical_session_id(self, client: GatewayClient):
        """reconnect() uses _canonical_session_id if available."""
        client._canonical_session_id = "canonical-xyz"
        client._resumption_token = "tok-123"
        mock_ws = AsyncMock()
        with patch("sip_bridge.gateway_client.websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws
            await client.reconnect()
            call_url = mock_connect.call_args[0][0]
            # Should use canonical session ID in path
            assert "/ws/sip-abc123/canonical-xyz" in call_url
            # Should include resumption token
            assert "resumption_token=tok-123" in call_url

    @pytest.mark.asyncio
    async def test_reconnect_falls_back_to_original_session_id(self, client: GatewayClient):
        """reconnect() uses original session_id when canonical is empty."""
        mock_ws = AsyncMock()
        with patch("sip_bridge.gateway_client.websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws
            await client.reconnect()
            call_url = mock_connect.call_args[0][0]
            assert "/ws/sip-abc123/sip-def456" in call_url
            assert "resumption_token" not in call_url


# ---------------------------------------------------------------------------
# 8. Concurrent send/receive
# ---------------------------------------------------------------------------

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_send_receive_no_deadlock(self, client: GatewayClient):
        """Simultaneous send + receive must not deadlock."""
        client._ws = MockWebSocket(messages=[b"\x00" * 640])

        async def sender():
            for _ in range(5):
                await client.send_audio(b"\x00" * 640)

        async def receiver():
            async for _ in client.receive():
                pass

        # Should complete within 2 seconds
        await asyncio.wait_for(
            asyncio.gather(sender(), receiver()),
            timeout=2.0,
        )
