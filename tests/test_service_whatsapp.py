"""TDD tests for WhatsApp service business logic."""

from __future__ import annotations

import asyncio
import time

import pytest
from unittest.mock import patch, AsyncMock

from app.api.v1.at.service_whatsapp import (
    WA_MAX_CHARS,
    check_service_window,
    handle_image_message,
    handle_text_message,
    handle_unsupported_message_type,
    record_inbound_timestamp,
    reset_idempotency_store,
    reset_service_windows,
    send_interactive_buttons,
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


@pytest.fixture(autouse=True)
def _no_adk_runner():
    """Ensure ADK runner is not available so tests exercise bridge_text fallback."""
    with patch(
        "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
        return_value=(None, None, None, None, ""),
    ):
        yield


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
        assert key not in service_whatsapp._service_windows

    def test_scope_isolation(self) -> None:
        """Different tenant/company/phone_number_id scopes should be independent."""
        record_inbound_timestamp("234", "phone1", tenant_id="t1", company_id="c1")
        assert check_service_window("234", "phone1", tenant_id="t1", company_id="c1") is True
        assert check_service_window("234", "phone1", tenant_id="t2", company_id="c1") is False
        assert check_service_window("234", "phone2", tenant_id="t1", company_id="c1") is False

    def test_size_cap_evicts_oldest(self, monkeypatch) -> None:
        from app.api.v1.at import service_whatsapp

        monkeypatch.setattr(service_whatsapp, "_SERVICE_WINDOW_MAX_ENTRIES", 2)
        now = time.time()
        service_whatsapp._service_windows["k1"] = now - 10.0
        service_whatsapp._service_windows["k2"] = now - 5.0

        record_inbound_timestamp("234", "phone1")
        assert len(service_whatsapp._service_windows) == 2
        assert "k1" not in service_whatsapp._service_windows


# ── Template Fallback ──


class TestTemplateFallback:
    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    async def test_sends_text_when_window_open(self, mock_send) -> None:
        mock_send.return_value = (200, {"messages": [{"id": "wamid.out"}]})
        record_inbound_timestamp("234", "phone123")
        status, _ = await send_with_template_fallback(to="234", text="Hello", phone_number_id="phone123")
        assert status == 200
        mock_send.assert_awaited_once()
        assert mock_send.await_args.kwargs["phone_number_id"] == "phone123"

    @patch("app.api.v1.at.providers.whatsapp_send_template", new_callable=AsyncMock)
    async def test_sends_template_when_no_window(self, mock_template) -> None:
        mock_template.return_value = (200, {"messages": [{"id": "wamid.tmpl"}]})
        with patch("app.api.v1.at.service_whatsapp.WA_UTILITY_TEMPLATE_NAME", "test_template"):
            status, _ = await send_with_template_fallback(to="234", text="Hello", phone_number_id="phone123")
        assert status == 200
        mock_template.assert_awaited_once()
        assert mock_template.await_args.kwargs["phone_number_id"] == "phone123"

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

    async def test_concurrent_same_key_executes_once(self) -> None:
        call_count = 0

        async def slow_send():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return 200, {"id": "msg1"}

        results = await asyncio.gather(
            send_with_idempotency(
                idempotency_key="key4",
                payload_hash="hashZ",
                send_fn=slow_send,
            ),
            send_with_idempotency(
                idempotency_key="key4",
                payload_hash="hashZ",
                send_fn=slow_send,
            ),
        )
        assert call_count == 1
        assert results[0][0] == 200
        assert results[1][0] == 200

    async def test_inflight_conflict_returns_409(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_send():
            started.set()
            await release.wait()
            return 200, {"id": "msg2"}

        first = asyncio.create_task(
            send_with_idempotency(
                idempotency_key="key5",
                payload_hash="hashA",
                send_fn=blocking_send,
            )
        )
        await started.wait()
        status, body = await send_with_idempotency(
            idempotency_key="key5",
            payload_hash="hashB",
            send_fn=AsyncMock(return_value=(200, {"id": "should-not-run"})),
        )
        release.set()
        first_status, first_body = await first

        assert status == 409
        assert body["error"] == "Idempotency key conflict"
        assert first_status == 200
        assert first_body["id"] == "msg2"


# ── Canonical Phone Identity (ADK runner path) ──


class TestCanonicalPhoneIdentity:
    """Verify send_text_message and handle_image_message pass phone-* user_id
    to the ADK runner — the actual cross-channel identity feature."""

    @patch("app.api.v1.at.service_whatsapp.adk_text_adapter.send_text_message", new_callable=AsyncMock)
    async def test_text_message_uses_canonical_phone_user_id(self, mock_send_text):
        """handle_text_message passes phone-{hash} user_id to adk_text_adapter."""
        mock_runner = AsyncMock()
        mock_session_svc = AsyncMock()
        mock_send_text.return_value = {"text": "Hi from ADK"}

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(mock_runner, mock_session_svc, "ekaette_text", None, ""),
        ):
            reply = await handle_text_message(
                from_="+2348001234567",
                text="I want a trade-in",
                tenant_id="public",
                company_id="ekaette-electronics",
            )

        assert reply == "Hi from ADK"
        call_kwargs = mock_send_text.call_args[1]
        uid = call_kwargs["user_id"]
        assert uid.startswith("phone-"), f"Expected phone-* user_id, got: {uid}"
        assert len(uid) == 30  # phone- (6) + sha256[:24] (24)

    @patch("app.api.v1.at.service_whatsapp.adk_text_adapter.send_text_message", new_callable=AsyncMock)
    async def test_text_different_formats_same_user_id(self, mock_send_text):
        """E.164 and raw digits for the same number produce identical user_id."""
        mock_send_text.return_value = {"text": "ok"}
        uids = []
        for phone in ("+2348001234567", "2348001234567", "08001234567"):
            with patch(
                "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
                return_value=(AsyncMock(), AsyncMock(), "app", None, ""),
            ):
                await handle_text_message(from_=phone, text="hi")
            uids.append(mock_send_text.call_args[1]["user_id"])
            mock_send_text.reset_mock()

        assert uids[0] == uids[1] == uids[2], f"user_ids diverged: {uids}"

    @patch("app.api.v1.at.service_whatsapp.adk_text_adapter.send_media_message", new_callable=AsyncMock)
    @patch("app.api.v1.at.service_whatsapp.providers.whatsapp_download_media", new_callable=AsyncMock)
    async def test_image_message_uses_canonical_phone_user_id(self, mock_download, mock_send_media):
        """handle_image_message passes phone-{hash} user_id to adk_text_adapter."""
        mock_download.return_value = (b"\x89PNG", "image/png")
        mock_send_media.return_value = {"text": "Nice photo"}

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(AsyncMock(), AsyncMock(), "ekaette_text", None, ""),
        ):
            reply = await handle_image_message(
                from_="+2348001234567",
                media_id="media123",
                mime_type="image/png",
                tenant_id="public",
                company_id="ekaette-electronics",
            )

        assert reply == "Nice photo"
        call_kwargs = mock_send_media.call_args[1]
        uid = call_kwargs["user_id"]
        assert uid.startswith("phone-"), f"Expected phone-* user_id, got: {uid}"
        assert len(uid) == 30

    @patch("app.api.v1.at.service_whatsapp.adk_text_adapter.send_text_message", new_callable=AsyncMock)
    async def test_invalid_phone_falls_back_to_wa_anon(self, mock_send_text):
        """Invalid phone produces wa-anon-{hash} deterministic fallback, not phone-*."""
        mock_send_text.return_value = {"text": "ok"}
        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(AsyncMock(), AsyncMock(), "app", None, ""),
        ):
            await handle_text_message(from_="invalid", text="hi")
            uid1 = mock_send_text.call_args[1]["user_id"]
            mock_send_text.reset_mock()

            await handle_text_message(from_="invalid", text="hello")
            uid2 = mock_send_text.call_args[1]["user_id"]

        assert uid1.startswith("wa-anon-"), f"Expected wa-anon-* fallback, got: {uid1}"
        assert uid1 == uid2, f"Anon fallback not deterministic: {uid1} != {uid2}"


class TestInteractiveButtons:
    @patch("app.api.v1.at.providers.whatsapp_send_interactive", new_callable=AsyncMock)
    async def test_missing_button_title_raises(self, _mock_send) -> None:
        with pytest.raises(ValueError, match="button title at index 0"):
            await send_interactive_buttons(
                to="234",
                body_text="Choose one",
                buttons=[{"id": "x"}],
            )

    @patch("app.api.v1.at.providers.whatsapp_send_interactive", new_callable=AsyncMock)
    async def test_valid_buttons_are_sent(self, mock_send) -> None:
        mock_send.return_value = (200, {"messages": [{"id": "wamid.button"}]})
        status, _ = await send_interactive_buttons(
            to="234",
            body_text="Choose one",
            buttons=[
                {"id": "a", "title": "First Option"},
                {"title": "Second Option"},
            ],
        )
        assert status == 200
        payload = mock_send.await_args.kwargs["interactive"]
        buttons = payload["action"]["buttons"]
        assert buttons[0]["reply"]["id"] == "a"
        assert buttons[1]["reply"]["id"] == "btn_1"
