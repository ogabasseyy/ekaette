"""Tests for ADK send_whatsapp_message tool.

Phase 6 of Single AI Brain — WA messaging tool migrated to ADK.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.wa_messaging import send_whatsapp_image_message, send_whatsapp_message


class MockToolContext:
    """Minimal mock for ADK tool_context."""

    def __init__(self, state: dict | None = None, function_call_id: str | None = None):
        self.state = state or {}
        self.function_call_id = function_call_id


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
    async def test_missing_scope_returns_error(self):
        """Missing tenant/company scope fails closed."""
        ctx = MockToolContext(state={"user:caller_phone": "+2348012345678"})
        with patch.dict(os.environ, {
            "WA_SERVICE_API_BASE_URL": "https://wa.example.com",
            "WA_SERVICE_SECRET": "test-secret",
        }):
            result = await send_whatsapp_message("Hello", ctx)
        assert result["status"] == "error"
        assert "scope" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_successful_send(self):
        """Successful POST returns sent status with message_id."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        }, function_call_id="fc-123")

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
        assert call_kwargs.args[0] == "https://wa.example.com/api/v1/at/whatsapp/send"
        payload = call_kwargs.kwargs["content"].decode()
        payload_json = json.loads(payload)
        assert payload_json["to"] == "+2348012345678"
        assert payload_json["text"] == "Your account is 1234567890"
        assert payload_json["type"] == "text"
        assert payload_json["tenant_id"] == "public"
        assert payload_json["company_id"] == "ekaette-electronics"
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        expected_idempotency_key = hashlib.sha256(
            "public:ekaette-electronics:send_whatsapp_message:+2348012345678:Your account is 1234567890:fc-123".encode()
        ).hexdigest()
        assert headers["X-Idempotency-Key"] == expected_idempotency_key
        assert "X-Service-Auth" in headers
        assert "X-Service-Timestamp" in headers
        assert "X-Service-Nonce" in headers

    @pytest.mark.asyncio
    async def test_successful_send_with_template_override(self):
        """Template override metadata is forwarded to the internal WA send API."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        }, function_call_id="fc-456")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {"messages": [{"id": "msg-456"}]}
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

                result = await send_whatsapp_message(
                    "Please send a clear photo.",
                    ctx,
                    template_name="tradein_media_request",
                    template_language="en_US",
                )

        assert result["status"] == "sent"
        payload = json.loads(mock_client.post.call_args.kwargs["content"].decode())
        assert payload["template_name"] == "tradein_media_request"
        assert payload["template_language"] == "en_US"


class TestSendWhatsAppImageMessage:
    """send_whatsapp_image_message ADK tool tests."""

    @pytest.mark.asyncio
    async def test_successful_send(self):
        """Successful POST returns sent status with message_id for image."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        }, function_call_id="fc-image")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {"messages": [{"id": "msg-image-1"}]}
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

                result = await send_whatsapp_image_message(
                    media_bytes=b"\x89PNG",
                    mime_type="image/png",
                    caption="Preview",
                    tool_context=ctx,
                )

        assert result["status"] == "sent"
        payload = json.loads(mock_client.post.call_args.kwargs["content"].decode())
        assert payload["type"] == "image"
        assert payload["mime_type"] == "image/png"
        assert payload["caption"] == "Preview"
        assert payload["media_base64"] == base64.b64encode(b"\x89PNG").decode()
        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["X-Idempotency-Key"]
        assert headers["X-Service-Auth"]
        assert headers["X-Service-Timestamp"]
        assert headers["X-Service-Nonce"]

    @pytest.mark.asyncio
    async def test_missing_caller_phone_returns_error(self):
        """Missing caller phone fails closed."""
        ctx = MockToolContext(state={
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        })
        result = await send_whatsapp_image_message(
            media_bytes=b"\x89PNG",
            mime_type="image/png",
            caption="Preview",
            tool_context=ctx,
        )
        assert result["status"] == "error"
        assert "caller phone" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_empty_media_returns_error(self):
        """Empty media bytes fail fast."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        })
        result = await send_whatsapp_image_message(
            media_bytes=b"",
            mime_type="image/png",
            caption="Preview",
            tool_context=ctx,
        )
        assert result["status"] == "error"
        assert "media bytes" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_base_url_returns_error(self):
        """Missing WA service base URL returns error."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        })
        with patch.dict(os.environ, {"WA_SERVICE_API_BASE_URL": ""}):
            result = await send_whatsapp_image_message(
                media_bytes=b"\x89PNG",
                mime_type="image/png",
                caption="Preview",
                tool_context=ctx,
            )
        assert result["status"] == "error"
        assert "BASE_URL" in result["detail"]

    @pytest.mark.asyncio
    async def test_non_https_url_returns_error(self):
        """Non-HTTPS WA service base URL returns error."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        })
        with patch.dict(os.environ, {
            "WA_SERVICE_API_BASE_URL": "http://wa.example.com",
            "WA_SERVICE_SECRET": "test-secret",
        }):
            result = await send_whatsapp_image_message(
                media_bytes=b"\x89PNG",
                mime_type="image/png",
                caption="Preview",
                tool_context=ctx,
            )
        assert result["status"] == "error"
        assert "https" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_secret_returns_error(self):
        """Missing service secret returns error."""
        ctx = MockToolContext(state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        })
        with patch.dict(os.environ, {
            "WA_SERVICE_API_BASE_URL": "https://wa.example.com",
            "WA_SERVICE_SECRET": "",
        }):
            result = await send_whatsapp_image_message(
                media_bytes=b"\x89PNG",
                mime_type="image/png",
                caption="Preview",
                tool_context=ctx,
            )
        assert result["status"] == "error"
        assert "SECRET" in result["detail"]

    @pytest.mark.asyncio
    async def test_missing_scope_returns_error(self):
        """Missing tenant/company scope fails closed."""
        ctx = MockToolContext(state={"user:caller_phone": "+2348012345678"})
        with patch.dict(os.environ, {
            "WA_SERVICE_API_BASE_URL": "https://wa.example.com",
            "WA_SERVICE_SECRET": "test-secret",
        }):
            result = await send_whatsapp_image_message(
                media_bytes=b"\x89PNG",
                mime_type="image/png",
                caption="Preview",
                tool_context=ctx,
            )
        assert result["status"] == "error"
        assert "scope" in result["detail"].lower()
