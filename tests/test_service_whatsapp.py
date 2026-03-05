"""TDD tests for WhatsApp service business logic."""

from __future__ import annotations

import time

import pytest
from unittest.mock import patch, AsyncMock

from app.api.v1.at.service_whatsapp import (
    WA_MAX_CHARS,
    check_service_window,
    handle_text_message,
    handle_unsupported_message_type,
    record_inbound_timestamp,
    reset_idempotency_store,
    reset_service_windows,
    send_with_idempotency,
    send_with_template_fallback,
)


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_service_windows()
    reset_idempotency_store()
    yield
    reset_service_windows()
    reset_idempotency_store()


# ── Text Handling ──


class TestHandleTextMessage:
    """Text message → AI reply."""

    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    async def test_returns_ai_reply(self, mock_query) -> None:
        mock_query.return_value = "Your order ships tomorrow!"
        reply = await handle_text_message(from_="234", text="Where is my order?")
        assert reply == "Your order ships tomorrow!"
        mock_query.assert_awaited_once()

    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    async def test_truncates_to_4096(self, mock_query) -> None:
        mock_query.return_value = "A" * 5000
        reply = await handle_text_message(from_="234", text="Hi")
        assert len(reply) <= WA_MAX_CHARS

    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    async def test_uses_whatsapp_channel(self, mock_query) -> None:
        mock_query.return_value = "reply"
        await handle_text_message(from_="234", text="Hi")
        call_kwargs = mock_query.call_args[1]
        assert call_kwargs["channel"] == "whatsapp"


# ── Unsupported Message Type ──


class TestHandleUnsupportedType:
    async def test_audio_reply(self) -> None:
        reply = await handle_unsupported_message_type(from_="234", message_type="audio")
        assert "audio" in reply.lower()
        assert "text message" in reply.lower()

    async def test_sticker_reply(self) -> None:
        reply = await handle_unsupported_message_type(from_="234", message_type="sticker")
        assert "sticker" in reply.lower()


# ── Service Window ──


class TestServiceWindow:
    def test_fresh_window_open(self) -> None:
        record_inbound_timestamp("234", "phone1")
        assert check_service_window("234", "phone1") is True

    def test_no_window(self) -> None:
        assert check_service_window("234", "phone1") is False

    def test_stale_window_closed(self) -> None:
        record_inbound_timestamp("234", "phone1")
        from app.api.v1.at import service_whatsapp
        key = service_whatsapp._window_key("234", "phone1", "public", "ekaette-electronics")
        service_whatsapp._service_windows[key] = time.time() - 90000  # > 24h
        assert check_service_window("234", "phone1") is False

    def test_scope_isolation(self) -> None:
        """Different tenant/company/phone_number_id scopes should be independent."""
        record_inbound_timestamp("234", "phone1", tenant_id="t1", company_id="c1")
        assert check_service_window("234", "phone1", tenant_id="t1", company_id="c1") is True
        assert check_service_window("234", "phone1", tenant_id="t2", company_id="c1") is False
        assert check_service_window("234", "phone2", tenant_id="t1", company_id="c1") is False


# ── Template Fallback ──


class TestTemplateFallback:
    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    async def test_sends_text_when_window_open(self, mock_send) -> None:
        mock_send.return_value = (200, {"messages": [{"id": "wamid.out"}]})
        record_inbound_timestamp("234", "")
        status, _ = await send_with_template_fallback(to="234", text="Hello")
        assert status == 200
        mock_send.assert_awaited_once()

    @patch("app.api.v1.at.providers.whatsapp_send_template", new_callable=AsyncMock)
    async def test_sends_template_when_no_window(self, mock_template) -> None:
        mock_template.return_value = (200, {"messages": [{"id": "wamid.tmpl"}]})
        with patch("app.api.v1.at.service_whatsapp.WA_UTILITY_TEMPLATE_NAME", "test_template"):
            status, _ = await send_with_template_fallback(to="234", text="Hello")
        assert status == 200
        mock_template.assert_awaited_once()

    async def test_fails_closed_when_no_template_config(self) -> None:
        with (
            patch("app.api.v1.at.service_whatsapp.WA_UTILITY_TEMPLATE_NAME", ""),
            pytest.raises(RuntimeError, match="WA_UTILITY_TEMPLATE_NAME"),
        ):
            await send_with_template_fallback(to="234", text="Hello")


# ── Send Idempotency ──


class TestSendIdempotency:
    async def test_first_send_executes(self) -> None:
        mock_fn = AsyncMock(return_value=(200, {"id": "msg1"}))
        status, body = await send_with_idempotency(
            idempotency_key="key1", payload_hash="hash1", send_fn=mock_fn,
        )
        assert status == 200
        mock_fn.assert_awaited_once()

    async def test_same_key_same_payload_returns_cached(self) -> None:
        mock_fn = AsyncMock(return_value=(200, {"id": "msg1"}))
        await send_with_idempotency(
            idempotency_key="key2", payload_hash="hashA", send_fn=mock_fn,
        )
        mock_fn.reset_mock()
        status, body = await send_with_idempotency(
            idempotency_key="key2", payload_hash="hashA", send_fn=mock_fn,
        )
        assert status == 200
        mock_fn.assert_not_awaited()

    async def test_same_key_different_payload_returns_409(self) -> None:
        mock_fn = AsyncMock(return_value=(200, {"id": "msg1"}))
        await send_with_idempotency(
            idempotency_key="key3", payload_hash="hashX", send_fn=mock_fn,
        )
        mock_fn.reset_mock()
        status, body = await send_with_idempotency(
            idempotency_key="key3", payload_hash="hashY", send_fn=mock_fn,
        )
        assert status == 409
        mock_fn.assert_not_awaited()
