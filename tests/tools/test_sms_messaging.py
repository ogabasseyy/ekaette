"""Tests for ADK send_sms_message tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.realtime.caller_phone_registry import (
    clear_registered_caller_phone,
    register_caller_phone,
)
from app.tools.sms_messaging import (
    resolve_sms_sender_id_from_state,
    send_sms_message,
)


class TestSendSMSMessage:
    @pytest.mark.asyncio
    async def test_missing_caller_phone_returns_error(self):
        ctx = SimpleNamespace(state={})
        result = await send_sms_message("Hello", ctx)
        assert result["status"] == "error"
        assert "caller phone" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_empty_text_returns_error(self):
        ctx = SimpleNamespace(state={"user:caller_phone": "+2348012345678"})
        result = await send_sms_message("", ctx)
        assert result["status"] == "error"
        assert "text" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_successful_send_uses_company_sender_id(self, monkeypatch):
        monkeypatch.delenv("AT_SMS_SENDER_ID", raising=False)
        ctx = SimpleNamespace(
            state={
                "user:caller_phone": "+2348012345678",
                "app:company_name": "Awgabassey Gadgets",
            }
        )

        with patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {
                "SMSMessageData": {"Recipients": [{"status": "Success"}]},
            }
            result = await send_sms_message("Pay to 1234567890", ctx)

        assert result["status"] == "sent"
        assert result["recipient"] == "+2348012345678"
        assert result["sender_id"] == "Awgabassey"
        mock_send.assert_awaited_once_with(
            message="Pay to 1234567890",
            recipients=["+2348012345678"],
            sender_id="Awgabassey",
        )

    @pytest.mark.asyncio
    async def test_runtime_registry_fallback_resolves_caller_phone(self, monkeypatch):
        monkeypatch.delenv("AT_SMS_SENDER_ID", raising=False)
        register_caller_phone(
            user_id="voice-user-1",
            session_id="session-1",
            caller_phone="+2348012345678",
        )
        ctx = SimpleNamespace(
            state={"app:company_name": "Awgabassey Gadgets"},
            session=SimpleNamespace(id="session-1", state={}),
            user_id="voice-user-1",
        )

        with patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {
                "SMSMessageData": {"Recipients": [{"status": "Success"}]},
            }
            result = await send_sms_message("Pay to 1234567890", ctx)

        clear_registered_caller_phone(user_id="voice-user-1", session_id="session-1")
        assert result["status"] == "sent"
        assert result["recipient"] == "+2348012345678"

    @pytest.mark.asyncio
    async def test_runtime_registry_fallback_uses_state_ids_when_context_ids_missing(
        self, monkeypatch,
    ):
        monkeypatch.delenv("AT_SMS_SENDER_ID", raising=False)
        register_caller_phone(
            user_id="voice-user-2",
            session_id="session-2",
            caller_phone="+2348012345678",
        )
        ctx = SimpleNamespace(
            state={
                "app:company_name": "Awgabassey Gadgets",
                "app:user_id": "voice-user-2",
                "app:session_id": "session-2",
            },
            session=SimpleNamespace(state={}),
        )

        with patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {
                "SMSMessageData": {"Recipients": [{"status": "Success"}]},
            }
            result = await send_sms_message("Pay to 1234567890", ctx)

        clear_registered_caller_phone(user_id="voice-user-2", session_id="session-2")
        assert result["status"] == "sent"
        assert result["recipient"] == "+2348012345678"

    def test_sender_id_prefers_profile_override(self):
        state = {
            "app:company_name": "Awgabassey Gadgets",
            "app:company_profile": {
                "name": "Awgabassey Gadgets",
                "sms_sender_id": "OGBStore",
            },
        }
        assert resolve_sms_sender_id_from_state(state) == "OGBStore"
