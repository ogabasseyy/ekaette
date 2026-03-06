"""Tests for WhatsApp voice note / audio message handling."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestHandleAudioMessage:
    """Audio message → download → Gemini multimodal → reply text."""

    @pytest.fixture(autouse=True)
    def _no_adk_runner(self):
        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(None, None, None, None, ""),
        ):
            yield

    @pytest.mark.asyncio
    async def test_audio_downloads_and_returns_reply(self):
        """Voice note is downloaded and processed through legacy analysis."""
        from app.api.v1.at.service_whatsapp import handle_audio_message

        mock_response = MagicMock()
        mock_response.text = "The customer said: I want to buy an iPhone 15"

        with patch(
            "app.api.v1.at.service_whatsapp.providers.whatsapp_download_media",
            new_callable=AsyncMock,
            return_value=(b"fake-audio-bytes", "audio/ogg"),
        ) as dl_mock, patch(
            "app.api.v1.at.service_whatsapp._legacy_media_analysis",
            new_callable=AsyncMock,
            return_value="The customer said: I want to buy an iPhone 15",
        ):
            result = await handle_audio_message(
                from_="2348001234567",
                media_id="media-audio-123",
                mime_type="audio/ogg; codecs=opus",
            )

        assert "iPhone 15" in result
        dl_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_uses_adk_runner_when_available(self):
        """When ADK runner is available, audio goes through agent graph."""
        from app.api.v1.at.service_whatsapp import handle_audio_message

        mock_send = AsyncMock(return_value={"text": "I can help you buy that!", "session_id": "s1", "channel": "whatsapp"})

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=("runner", "svc", "app", "fb_runner", "fb_app"),
        ), patch(
            "app.api.v1.at.service_whatsapp.providers.whatsapp_download_media",
            new_callable=AsyncMock,
            return_value=(b"fake-audio-bytes", "audio/ogg"),
        ), patch(
            "app.api.v1.at.service_whatsapp.adk_text_adapter.send_media_message",
            mock_send,
        ):
            result = await handle_audio_message(
                from_="2348001234567",
                media_id="media-audio-456",
                mime_type="audio/ogg; codecs=opus",
            )

        assert "help you buy" in result
        # Verify it was called with audio mime type
        call_kwargs = mock_send.call_args[1]
        assert "audio" in call_kwargs["mime_type"]

    @pytest.mark.asyncio
    async def test_audio_empty_bytes_returns_fallback(self):
        """Empty audio returns a friendly retry message."""
        from app.api.v1.at.service_whatsapp import handle_audio_message

        with patch(
            "app.api.v1.at.service_whatsapp.providers.whatsapp_download_media",
            new_callable=AsyncMock,
            return_value=(b"", "audio/ogg"),
        ):
            result = await handle_audio_message(
                from_="2348001234567",
                media_id="media-audio-789",
            )

        assert "empty" in result.lower() or "again" in result.lower()


class TestAudioInProcessMessage:
    """Audio messages route through _process_message correctly."""

    @pytest.mark.asyncio
    async def test_audio_type_is_processed(self):
        """WhatsApp audio messages are handled, not rejected as unsupported."""
        from app.api.v1.at.whatsapp import _process_message

        async def mock_handle_audio(**kwargs):
            return "I heard you say you want an iPhone!"

        async def mock_send_text(**kwargs):
            return 200, {"messages": [{"id": "wamid.reply1"}]}

        async def mock_typing(**kwargs):
            pass

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_typing_indicator",
            side_effect=mock_typing,
        ), patch(
            "app.api.v1.at.whatsapp.service_whatsapp.handle_audio_message",
            side_effect=mock_handle_audio,
        ) as audio_mock, patch(
            "app.api.v1.at.whatsapp._send_voice_reply",
            new_callable=AsyncMock,
            return_value=(200, {"messages": [{"id": "wamid.audio_reply"}]}),
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            side_effect=mock_send_text,
        ), patch(
            "app.api.v1.at.whatsapp._schedule_nudge",
            new_callable=AsyncMock,
        ):
            message = {
                "from": "2348001234567",
                "id": "wamid.audio123",
                "type": "audio",
                "audio": {"id": "media-audio-123", "mime_type": "audio/ogg; codecs=opus"},
            }
            await _process_message(message, "test_phone_id")

        audio_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_not_in_unsupported(self):
        """Audio is no longer in UNSUPPORTED_MESSAGE_TYPES."""
        from app.api.v1.at.service_whatsapp import UNSUPPORTED_MESSAGE_TYPES, SUPPORTED_MESSAGE_TYPES

        assert "audio" not in UNSUPPORTED_MESSAGE_TYPES
        assert "audio" in SUPPORTED_MESSAGE_TYPES


class TestAudioMediaPrompt:
    """Audio-specific prompt in the media adapter."""

    def test_audio_prompt_exists(self):
        from app.channels.adk_text_adapter import _DEFAULT_MEDIA_PROMPTS

        assert "audio" in _DEFAULT_MEDIA_PROMPTS
        assert "voice" in _DEFAULT_MEDIA_PROMPTS["audio"].lower() or "said" in _DEFAULT_MEDIA_PROMPTS["audio"].lower()
