"""Tests for ADK send_whatsapp_message tool.

Phase 6 of Single AI Brain — WA messaging tool migrated to ADK.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.wa_messaging import send_whatsapp_message


class MockToolContext:
    """Minimal mock for ADK tool_context."""

    def __init__(self, state: dict | None = None):
        self.state = state or {}


class TestSendWhatsAppMessage:
    """send_whatsapp_message ADK tool tests."""

    @pytest.mark.asyncio
    async def test_no_caller_phone_returns_error(self):
        """Missing user:caller_phone in state → error."""
        ctx = MockToolContext(state={})
        result = await send_whatsapp_message("Hello", ctx)
        assert result["status"] == "error"
        assert "caller phone" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_empty_text_returns_error(self):
        """Empty text → error."""
        ctx = MockToolContext(state={"user:caller_phone": "+234"})
        result = await send_whatsapp_message("", ctx)
        assert result["status"] == "error"
        assert "text" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_base_url_returns_error(self):
        """Missing WA_SERVICE_API_BASE_URL → error."""
        ctx = MockToolContext(state={"user:caller_phone": "+234"})
        with patch.dict(os.environ, {"WA_SERVICE_API_BASE_URL": ""}):
            result = await send_whatsapp_message("Hello", ctx)
        assert result["status"] == "error"
        assert "BASE_URL" in result["detail"]

    @pytest.mark.asyncio
    async def test_non_https_url_returns_error(self):
        """Non-HTTPS URL → error."""
        ctx = MockToolContext(state={"user:caller_phone": "+234"})
        with patch.dict(os.environ, {
            "WA_SERVICE_API_BASE_URL": "http://insecure.example.com",
            "WA_SERVICE_SECRET": "secret",
        }):
            result = await send_whatsapp_message("Hello", ctx)
        assert result["status"] == "error"
        assert "https" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_secret_returns_error(self):
        """Missing WA_SERVICE_SECRET → error."""
        ctx = MockToolContext(state={"user:caller_phone": "+234"})
        with patch.dict(os.environ, {
            "WA_SERVICE_API_BASE_URL": "https://wa.example.com",
            "WA_SERVICE_SECRET": "",
        }):
            result = await send_whatsapp_message("Hello", ctx)
        assert result["status"] == "error"
        assert "SECRET" in result["detail"]

    @pytest.mark.asyncio
    async def test_successful_send(self):
        """Successful POST returns sent status with message_id."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {"messages": [{"id": "msg-123"}]}
        }

        with patch.dict(os.environ, {
            "WA_SERVICE_API_BASE_URL": "https://wa.example.com",
            "WA_SERVICE_SECRET": "test-secret",
        }):
            with patch("app.tools.wa_messaging.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post.return_value = mock_response
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await send_whatsapp_message("Your account is 1234567890", ctx)

        assert result["status"] == "sent"
        assert result["message_id"] == "msg-123"
        # Verify HMAC headers were sent
        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "X-Service-Auth" in headers
        assert "X-Service-Timestamp" in headers
        assert "X-Service-Nonce" in headers
