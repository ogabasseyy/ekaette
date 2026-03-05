"""TDD tests for SIP bridge WA tools (during-call messaging)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from sip_bridge.wa_tools import (
    SEND_WA_MESSAGE_TOOL,
    _build_service_auth_headers,
    handle_send_wa_message,
)


@dataclass
class MockConfig:
    wa_service_api_base_url: str = "https://test.example.com"
    wa_service_secret: str = "test_secret_123"
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"


class TestToolDeclaration:
    """Gemini function tool schema."""

    def test_has_function_declarations(self) -> None:
        assert "function_declarations" in SEND_WA_MESSAGE_TOOL

    def test_function_name(self) -> None:
        decl = SEND_WA_MESSAGE_TOOL["function_declarations"][0]
        assert decl["name"] == "send_whatsapp_message"

    def test_required_text_param(self) -> None:
        decl = SEND_WA_MESSAGE_TOOL["function_declarations"][0]
        assert "text" in decl["parameters"]["properties"]
        assert "text" in decl["parameters"]["required"]


class TestServiceAuthHeaders:
    """Service-auth header generation."""

    def test_headers_present(self) -> None:
        headers = _build_service_auth_headers("{}", "secret")
        assert "X-Service-Timestamp" in headers
        assert "X-Service-Nonce" in headers
        assert "X-Service-Auth" in headers

    def test_hmac_not_empty(self) -> None:
        headers = _build_service_auth_headers("{}", "secret")
        assert len(headers["X-Service-Auth"]) == 64  # SHA256 hex


class TestHandleSendWaMessage:
    """Tool call handler."""

    @patch("sip_bridge.wa_tools.httpx.AsyncClient")
    async def test_success(self, mock_client_cls) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "result": {"messages": [{"id": "wamid.sent1"}]},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await handle_send_wa_message(
            args={"text": "Your account is 1234567890"},
            caller_phone="+2348012345678",
            config=MockConfig(),
        )
        assert result["status"] == "sent"
        assert result["message_id"] == "wamid.sent1"

    async def test_empty_text_returns_error(self) -> None:
        result = await handle_send_wa_message(
            args={"text": ""},
            caller_phone="+234",
            config=MockConfig(),
        )
        assert result["status"] == "error"
        assert "No text" in result["detail"]

    async def test_missing_base_url_returns_error(self) -> None:
        config = MockConfig(wa_service_api_base_url="")
        result = await handle_send_wa_message(
            args={"text": "hello"},
            caller_phone="+234",
            config=config,
        )
        assert result["status"] == "error"
        assert "BASE_URL" in result["detail"]

    async def test_missing_secret_returns_error(self) -> None:
        config = MockConfig(wa_service_secret="")
        result = await handle_send_wa_message(
            args={"text": "hello"},
            caller_phone="+234",
            config=config,
        )
        assert result["status"] == "error"
        assert "SECRET" in result["detail"]

    @patch("sip_bridge.wa_tools.httpx.AsyncClient")
    async def test_http_error(self, mock_client_cls) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "Bad Gateway"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await handle_send_wa_message(
            args={"text": "hello"},
            caller_phone="+234",
            config=MockConfig(),
        )
        assert result["status"] == "error"
        assert "502" in result["detail"]

    @patch("sip_bridge.wa_tools.httpx.AsyncClient")
    async def test_uses_correct_url(self, mock_client_cls) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "result": {"messages": [{"id": "x"}]}}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await handle_send_wa_message(
            args={"text": "hello"},
            caller_phone="+234",
            config=MockConfig(wa_service_api_base_url="https://custom.example.com"),
        )

        call_args = mock_client.post.call_args
        assert "https://custom.example.com/api/v1/at/whatsapp/send" == call_args[0][0]
