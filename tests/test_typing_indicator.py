"""Tests for WhatsApp typing indicator."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx


class TestWhatsappSendTypingIndicator:
    """Test the typing indicator provider function."""

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self):
        """Typing indicator sends read+typing payload with message_id."""
        from app.api.v1.at.providers import whatsapp_send_typing_indicator

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_client.post.return_value = mock_resp

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await whatsapp_send_typing_indicator(
                access_token="test-token",
                message_id="wamid.abc123",
                phone_number_id="123456789",
            )

        mock_client.post.assert_called_once()
        _, kwargs = mock_client.post.call_args
        payload = kwargs["json"]
        assert payload["messaging_product"] == "whatsapp"
        assert payload["status"] == "read"
        assert payload["message_id"] == "wamid.abc123"
        assert payload["typing_indicator"]["type"] == "text"

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
                message_id="wamid.abc123",
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
                message_id="wamid.abc123",
                phone_number_id="123456789",
            )

    @pytest.mark.asyncio
    async def test_does_not_raise_on_non_200(self):
        """Non-200 response is silently ignored."""
        from app.api.v1.at.providers import whatsapp_send_typing_indicator

        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=400)
        mock_client.post.return_value = mock_resp

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await whatsapp_send_typing_indicator(
                access_token="test-token",
                message_id="wamid.abc123",
                phone_number_id="123456789",
            )


class TestTypingIndicatorInProcessMessage:
    """Test that _process_message fires typing indicator before AI processing."""

    @pytest.mark.asyncio
    async def test_typing_fires_during_text_handling(self):
        """Typing indicator is scheduled as background task during message processing."""
        import asyncio
        from app.api.v1.at.whatsapp import _process_message

        typing_called = False

        async def mock_typing(**kwargs):
            nonlocal typing_called
            typing_called = True

        async def mock_handle_text(**kwargs):
            return "Hello!"

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
            message = {
                "from": "2348001234567",
                "id": "wamid.inbound123",
                "type": "text",
                "text": {"body": "Hi"},
            }
            await _process_message(message, "test_phone_id")
            # Let background typing task complete
            await asyncio.sleep(0)

        assert typing_called

    @pytest.mark.asyncio
    async def test_typing_fires_during_image_handling(self):
        """Typing indicator fires as background task for image processing too."""
        import asyncio
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
                "id": "wamid.inbound456",
                "type": "image",
                "image": {"id": "media-123", "mime_type": "image/jpeg"},
            }
            await _process_message(message, "test_phone_id")
            # Let background typing task complete
            await asyncio.sleep(0)

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
            message = {
                "from": "2348001234567",
                "id": "wamid.inbound789",
                "type": "text",
                "text": {"body": "Hi"},
            }
            # Should not raise even though typing fails
            await _process_message(message, "test_phone_id")

    @pytest.mark.asyncio
    async def test_no_typing_when_message_id_missing(self):
        """No typing indicator sent when message has no id."""
        from app.api.v1.at.whatsapp import _process_message

        typing_called = False

        async def mock_typing(**kwargs):
            nonlocal typing_called
            typing_called = True

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
            # No "id" field in message
            message = {"from": "2348001234567", "type": "text", "text": {"body": "Hi"}}
            await _process_message(message, "test_phone_id")

        assert not typing_called
