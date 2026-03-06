"""Tests for WhatsApp typing indicator."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx


class TestWhatsappSendTypingIndicator:
    """Test the typing indicator provider function."""

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self):
        """Typing indicator sends correct Graph API payload."""
        from app.api.v1.at.providers import whatsapp_send_typing_indicator

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_client.post.return_value = mock_resp

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await whatsapp_send_typing_indicator(
                access_token="test-token",
                to="2348001234567",
                phone_number_id="123456789",
            )

        mock_client.post.assert_called_once()
        _, kwargs = mock_client.post.call_args
        payload = kwargs["json"]
        assert payload["messaging_product"] == "whatsapp"
        assert payload["recipient_type"] == "individual"
        assert payload["to"] == "2348001234567"
        assert payload["type"] == "typing"
        assert payload["typing"]["status"] == "typing"

    @pytest.mark.asyncio
    async def test_uses_correct_auth_header(self):
        """Typing indicator uses Bearer token auth."""
        from app.api.v1.at.providers import whatsapp_send_typing_indicator

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_client.post.return_value = mock_resp

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await whatsapp_send_typing_indicator(
                access_token="my-secret-token",
                to="2348001234567",
                phone_number_id="123456789",
            )

        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer my-secret-token"

    @pytest.mark.asyncio
    async def test_does_not_raise_on_failure(self):
        """Typing indicator is fire-and-forget — errors are swallowed."""
        from app.api.v1.at.providers import whatsapp_send_typing_indicator

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("network down")

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await whatsapp_send_typing_indicator(
                access_token="test-token",
                to="2348001234567",
                phone_number_id="123456789",
            )

    @pytest.mark.asyncio
    async def test_does_not_raise_on_non_200(self):
        """Non-200 response is silently ignored."""
        from app.api.v1.at.providers import whatsapp_send_typing_indicator

        async def mock_post(url, *, headers=None, json=None, **kwargs):
            resp = MagicMock()
            resp.status_code = 400
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await whatsapp_send_typing_indicator(
                access_token="test-token",
                to="2348001234567",
                phone_number_id="123456789",
            )


class TestTypingIndicatorInProcessMessage:
    """Test that _process_message fires typing indicator before AI processing."""

    @pytest.mark.asyncio
    async def test_typing_fires_before_text_handling(self):
        """Typing indicator is sent before handle_text_message."""
        from app.api.v1.at.whatsapp import _process_message

        call_order = []

        async def mock_typing(**kwargs):
            call_order.append("typing")

        async def mock_handle_text(**kwargs):
            call_order.append("handle_text")
            return "Hello!"

        async def mock_send_text(**kwargs):
            call_order.append("send_text")
            return 200, {"messages": [{"id": "wamid.123"}]}

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_typing_indicator",
            side_effect=mock_typing,
        ), patch(
            "app.api.v1.at.whatsapp.service_whatsapp.handle_text_message",
            side_effect=mock_handle_text,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            side_effect=mock_send_text,
        ):
            message = {"from": "2348001234567", "type": "text", "text": {"body": "Hi"}}
            await _process_message(message, "test_phone_id")

        assert call_order == ["typing", "handle_text", "send_text"]

    @pytest.mark.asyncio
    async def test_typing_fires_before_image_handling(self):
        """Typing indicator fires before image processing too."""
        from app.api.v1.at.whatsapp import _process_message

        typing_called = False

        async def mock_typing(**kwargs):
            nonlocal typing_called
            typing_called = True

        async def mock_handle_image(**kwargs):
            return "I see a device!"

        async def mock_send_text(**kwargs):
            return 200, {"messages": [{"id": "wamid.123"}]}

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_typing_indicator",
            side_effect=mock_typing,
        ), patch(
            "app.api.v1.at.whatsapp.service_whatsapp.handle_image_message",
            side_effect=mock_handle_image,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            side_effect=mock_send_text,
        ):
            message = {
                "from": "2348001234567",
                "type": "image",
                "image": {"id": "media-123", "mime_type": "image/jpeg"},
            }
            await _process_message(message, "test_phone_id")

        assert typing_called

    @pytest.mark.asyncio
    async def test_typing_failure_does_not_block_message(self):
        """If typing indicator fails, message processing continues."""
        from app.api.v1.at.whatsapp import _process_message

        async def mock_typing(**kwargs):
            raise httpx.ConnectError("network down")

        async def mock_handle_text(**kwargs):
            return "Reply!"

        async def mock_send_text(**kwargs):
            return 200, {"messages": [{"id": "wamid.123"}]}

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_typing_indicator",
            side_effect=mock_typing,
        ), patch(
            "app.api.v1.at.whatsapp.service_whatsapp.handle_text_message",
            side_effect=mock_handle_text,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            side_effect=mock_send_text,
        ):
            message = {"from": "2348001234567", "type": "text", "text": {"body": "Hi"}}
            # Should not raise even though typing fails
            await _process_message(message, "test_phone_id")
