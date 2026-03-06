"""Tests for voice note reply: TTS + upload + send audio."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTextToSpeech:
    """Gemini TTS: text → audio bytes."""

    @pytest.mark.asyncio
    async def test_tts_returns_audio_bytes(self):
        from app.api.v1.at.providers import text_to_speech

        fake_pcm = b"\x00\x01" * 1000
        fake_ogg = b"OggS-fake-opus-data"

        mock_response = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data.data = fake_pcm
        mock_response.candidates = [MagicMock(content=MagicMock(parts=[mock_part]))]

        with patch("app.api.v1.at.providers._get_genai_client") as mock_client, \
             patch("app.api.v1.at.providers._pcm_to_ogg_opus", return_value=fake_ogg):
            mock_client.return_value.aio.models.generate_content = AsyncMock(
                return_value=mock_response
            )
            audio_bytes, mime_type = await text_to_speech("Hello there!")

        assert audio_bytes == fake_ogg
        assert mime_type == "audio/ogg"

    @pytest.mark.asyncio
    async def test_tts_empty_text_raises(self):
        from app.api.v1.at.providers import text_to_speech

        with pytest.raises(ValueError, match="empty"):
            await text_to_speech("")


class TestWhatsappUploadMedia:
    """Upload audio to WhatsApp Media API."""

    @pytest.mark.asyncio
    async def test_upload_returns_media_id(self):
        from app.api.v1.at.providers import whatsapp_upload_media

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_resp = MagicMock(status_code=200)
            mock_resp.json.return_value = {"id": "media_id_123"}
            mock_client.post.return_value = mock_resp

            media_id = await whatsapp_upload_media(
                access_token="test-token",
                media_bytes=b"fake-audio",
                mime_type="audio/ogg",
                phone_number_id="123456789",
            )

        assert media_id == "media_id_123"

    @pytest.mark.asyncio
    async def test_upload_failure_raises(self):
        from app.api.v1.at.providers import whatsapp_upload_media

        with patch("app.api.v1.at.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_resp = MagicMock(status_code=400)
            mock_resp.json.return_value = {"error": {"message": "bad"}}
            mock_resp.text = '{"error": {"message": "bad"}}'
            mock_client.post.return_value = mock_resp

            with pytest.raises(RuntimeError, match="upload failed"):
                await whatsapp_upload_media(
                    access_token="test-token",
                    media_bytes=b"fake-audio",
                    mime_type="audio/ogg",
                    phone_number_id="123456789",
                )


class TestWhatsappSendAudio:
    """Send audio message via WhatsApp Cloud API."""

    @pytest.mark.asyncio
    async def test_send_audio_correct_payload(self):
        from app.api.v1.at.providers import whatsapp_send_audio

        with patch(
            "app.api.v1.at.providers._wa_graph_request",
            new_callable=AsyncMock,
            return_value=(200, {"messages": [{"id": "wamid.audio_out"}]}),
        ) as mock_req:
            status, body = await whatsapp_send_audio(
                access_token="test-token",
                to="2348001234567",
                media_id="media_id_123",
                phone_number_id="123456789",
            )

        assert status == 200
        _, kwargs = mock_req.call_args
        payload = kwargs["json"]
        assert payload["type"] == "audio"
        assert payload["audio"]["id"] == "media_id_123"
        assert payload["messaging_product"] == "whatsapp"


class TestVoiceNoteReplyPipeline:
    """End-to-end: audio message in → voice note reply out."""

    @pytest.mark.asyncio
    async def test_audio_message_gets_audio_reply(self):
        """When user sends voice note, reply is sent as audio, not text."""
        from app.api.v1.at.whatsapp import _process_message

        send_audio_called = False
        send_text_called = False

        async def mock_handle_audio(**kwargs):
            return "I can help you with that!"

        async def mock_tts(text):
            return b"fake-ogg-audio", "audio/ogg"

        async def mock_upload(**kwargs):
            return "media_uploaded_123"

        async def mock_send_audio(**kwargs):
            nonlocal send_audio_called
            send_audio_called = True
            return 200, {"messages": [{"id": "wamid.audio_reply"}]}

        async def mock_send_text(**kwargs):
            nonlocal send_text_called
            send_text_called = True
            return 200, {"messages": [{"id": "wamid.text_reply"}]}

        async def mock_typing(**kwargs):
            pass

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_typing_indicator",
            side_effect=mock_typing,
        ), patch(
            "app.api.v1.at.whatsapp.service_whatsapp.handle_audio_message",
            side_effect=mock_handle_audio,
        ), patch(
            "app.api.v1.at.whatsapp.providers.text_to_speech",
            side_effect=mock_tts,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_upload_media",
            side_effect=mock_upload,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_audio",
            side_effect=mock_send_audio,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            side_effect=mock_send_text,
        ), patch(
            "app.api.v1.at.whatsapp._schedule_nudge",
            new_callable=AsyncMock,
        ):
            message = {
                "from": "2348001234567",
                "id": "wamid.voice_in",
                "type": "audio",
                "audio": {"id": "media-audio-123", "mime_type": "audio/ogg; codecs=opus"},
            }
            await _process_message(message, "test_phone_id")

        assert send_audio_called
        assert not send_text_called

    @pytest.mark.asyncio
    async def test_audio_falls_back_to_text_on_tts_failure(self):
        """If TTS fails, fall back to sending text reply."""
        from app.api.v1.at.whatsapp import _process_message

        send_text_called = False

        async def mock_handle_audio(**kwargs):
            return "Here's what I found!"

        async def mock_tts(text):
            raise RuntimeError("TTS failed")

        async def mock_send_text(**kwargs):
            nonlocal send_text_called
            send_text_called = True
            return 200, {"messages": [{"id": "wamid.text_fallback"}]}

        async def mock_typing(**kwargs):
            pass

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_typing_indicator",
            side_effect=mock_typing,
        ), patch(
            "app.api.v1.at.whatsapp.service_whatsapp.handle_audio_message",
            side_effect=mock_handle_audio,
        ), patch(
            "app.api.v1.at.whatsapp.providers.text_to_speech",
            side_effect=mock_tts,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            side_effect=mock_send_text,
        ), patch(
            "app.api.v1.at.whatsapp._schedule_nudge",
            new_callable=AsyncMock,
        ):
            message = {
                "from": "2348001234567",
                "id": "wamid.voice_in2",
                "type": "audio",
                "audio": {"id": "media-audio-456", "mime_type": "audio/ogg; codecs=opus"},
            }
            await _process_message(message, "test_phone_id")

        assert send_text_called

    @pytest.mark.asyncio
    async def test_text_message_still_gets_text_reply(self):
        """Text messages still get text replies, not audio."""
        from app.api.v1.at.whatsapp import _process_message

        send_text_called = False

        async def mock_handle_text(**kwargs):
            return "Sure thing!"

        async def mock_send_text(**kwargs):
            nonlocal send_text_called
            send_text_called = True
            return 200, {"messages": [{"id": "wamid.text_out"}]}

        async def mock_typing(**kwargs):
            pass

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_typing_indicator",
            side_effect=mock_typing,
        ), patch(
            "app.api.v1.at.whatsapp.service_whatsapp.handle_text_message",
            side_effect=mock_handle_text,
        ), patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            side_effect=mock_send_text,
        ), patch(
            "app.api.v1.at.whatsapp._schedule_nudge",
            new_callable=AsyncMock,
        ):
            message = {
                "from": "2348001234567",
                "id": "wamid.text_in",
                "type": "text",
                "text": {"body": "Hello"},
            }
            await _process_message(message, "test_phone_id")

        assert send_text_called
