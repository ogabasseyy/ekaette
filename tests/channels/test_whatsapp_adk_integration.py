"""Tests for WhatsApp → ADK adapter integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestHandleTextMessageADK:
    """Test that handle_text_message routes through ADK when runner is available."""

    @pytest.mark.asyncio
    async def test_text_routes_through_adk_adapter(self):
        """When runner is available, text goes through ADK adapter."""
        from app.api.v1.at import service_whatsapp

        mock_result = {
            "text": "I can help you with that trade-in!",
            "session_id": "whatsapp-abc123",
            "channel": "whatsapp",
        }

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(MagicMock(), MagicMock(), "ekaette", None, ""),
        ), patch(
            "app.channels.adk_text_adapter.send_text_message",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_send:
            reply = await service_whatsapp.handle_text_message(
                from_="2348001234567",
                text="I want to swap my phone",
            )

        assert reply == "I can help you with that trade-in!"
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_bridge_text_when_no_runner(self):
        """When runner is not available, falls back to bridge_text."""
        from app.api.v1.at import service_whatsapp

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(None, None, None, None, ""),
        ), patch(
            "app.api.v1.at.bridge_text.query_text",
            new_callable=AsyncMock,
            return_value="Fallback reply",
        ) as mock_bridge:
            reply = await service_whatsapp.handle_text_message(
                from_="2348001234567",
                text="Hello",
            )

        assert reply == "Fallback reply"
        mock_bridge.assert_called_once()


class TestHandleImageMessageADK:
    """Test that handle_image_message routes through ADK when runner is available."""

    @pytest.mark.asyncio
    async def test_image_routes_through_adk_adapter(self):
        """When runner is available, image goes through ADK adapter."""
        from app.api.v1.at import service_whatsapp

        mock_result = {
            "text": "I can see an iPhone 14 Pro in good condition.",
            "session_id": "whatsapp-abc123",
            "channel": "whatsapp",
        }

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(MagicMock(), MagicMock(), "ekaette", None, ""),
        ), patch(
            "app.channels.adk_text_adapter.send_media_message",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_send, patch(
            "app.api.v1.at.providers.whatsapp_download_media",
            new_callable=AsyncMock,
            return_value=(b"fake-jpeg", "image/jpeg"),
        ):
            reply = await service_whatsapp.handle_image_message(
                from_="2348001234567",
                media_id="media-123",
                mime_type="image/jpeg",
                caption="Check this phone",
            )

        assert reply == "I can see an iPhone 14 Pro in good condition."
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_image_falls_back_when_no_runner(self):
        """When runner is not available, falls back to direct Gemini vision call."""
        from app.api.v1.at import service_whatsapp

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(None, None, None, None, ""),
        ), patch(
            "app.api.v1.at.providers.whatsapp_download_media",
            new_callable=AsyncMock,
            return_value=(b"fake-jpeg", "image/jpeg"),
        ), patch(
            "app.api.v1.at.service_whatsapp._legacy_media_analysis",
            new_callable=AsyncMock,
            return_value="Legacy analysis result",
        ) as mock_legacy:
            reply = await service_whatsapp.handle_image_message(
                from_="2348001234567",
                media_id="media-123",
            )

        assert reply == "Legacy analysis result"
        mock_legacy.assert_called_once()


class TestHandleVideoMessageADK:
    """Test that handle_video_message routes through ADK when runner is available."""

    @pytest.mark.asyncio
    async def test_video_routes_through_adk_adapter(self):
        """When runner is available, video goes through ADK send_media_message."""
        from app.api.v1.at import service_whatsapp

        mock_result = {
            "text": "I can see a Samsung Galaxy S24 with a cracked screen.",
            "session_id": "whatsapp-abc123",
            "channel": "whatsapp",
        }

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(MagicMock(), MagicMock(), "ekaette", None, ""),
        ), patch(
            "app.channels.adk_text_adapter.send_media_message",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_send, patch(
            "app.api.v1.at.providers.whatsapp_download_media",
            new_callable=AsyncMock,
            return_value=(b"fake-mp4", "video/mp4"),
        ):
            reply = await service_whatsapp.handle_video_message(
                from_="2348001234567",
                media_id="media-456",
                mime_type="video/mp4",
                caption="Check my phone screen",
            )

        assert reply == "I can see a Samsung Galaxy S24 with a cracked screen."
        mock_send.assert_called_once()
        # Verify video mime_type is passed through
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["mime_type"] == "video/mp4"

    @pytest.mark.asyncio
    async def test_video_falls_back_when_no_runner(self):
        """When runner is not available, falls back to legacy vision."""
        from app.api.v1.at import service_whatsapp

        with patch(
            "app.api.v1.at.service_whatsapp._get_adk_runner_and_service",
            return_value=(None, None, None, None, ""),
        ), patch(
            "app.api.v1.at.providers.whatsapp_download_media",
            new_callable=AsyncMock,
            return_value=(b"fake-mp4", "video/mp4"),
        ), patch(
            "app.api.v1.at.service_whatsapp._legacy_media_analysis",
            new_callable=AsyncMock,
            return_value="Legacy video analysis",
        ) as mock_legacy:
            reply = await service_whatsapp.handle_video_message(
                from_="2348001234567",
                media_id="media-456",
            )

        assert reply == "Legacy video analysis"
        mock_legacy.assert_called_once()

    def test_video_is_supported_message_type(self):
        """Video should be in SUPPORTED_MESSAGE_TYPES, not UNSUPPORTED."""
        from app.api.v1.at.service_whatsapp import (
            SUPPORTED_MESSAGE_TYPES,
            UNSUPPORTED_MESSAGE_TYPES,
        )

        assert "video" in SUPPORTED_MESSAGE_TYPES
        assert "video" not in UNSUPPORTED_MESSAGE_TYPES


class TestModelOverloadedFallback:
    """Test automatic fallback when primary model returns 503."""

    @pytest.mark.asyncio
    async def test_text_falls_back_on_model_overloaded(self):
        """When primary runner raises ModelOverloadedError, fallback runner succeeds."""
        from app.channels.adk_text_adapter import (
            ModelOverloadedError,
            send_text_message,
        )

        # Primary runner raises overloaded; fallback runner succeeds
        mock_run_collect = AsyncMock(
            side_effect=[ModelOverloadedError("503"), "Fallback reply from 2.5-flash"]
        )
        mock_ensure = AsyncMock(return_value="session-abc")

        with patch(
            "app.channels.adk_text_adapter._run_and_collect_text", mock_run_collect,
        ), patch(
            "app.channels.adk_text_adapter._ensure_session", mock_ensure,
        ):
            result = await send_text_message(
                runner=MagicMock(),
                session_service=MagicMock(),
                app_name="ekaette_text",
                user_id="wa_2348001234567",
                message_text="Hello",
                fallback_runner=MagicMock(),
                fallback_app_name="ekaette_text_fallback",
            )

        assert result["text"] == "Fallback reply from 2.5-flash"
        assert mock_run_collect.call_count == 2

    @pytest.mark.asyncio
    async def test_text_returns_default_when_overloaded_and_no_fallback(self):
        """When primary raises ModelOverloadedError and no fallback, returns default."""
        from app.channels.adk_text_adapter import (
            ModelOverloadedError,
            _DEFAULT_FALLBACK,
            send_text_message,
        )

        mock_run_collect = AsyncMock(side_effect=ModelOverloadedError("503"))
        mock_ensure = AsyncMock(return_value="session-abc")

        with patch(
            "app.channels.adk_text_adapter._run_and_collect_text", mock_run_collect,
        ), patch(
            "app.channels.adk_text_adapter._ensure_session", mock_ensure,
        ):
            result = await send_text_message(
                runner=MagicMock(),
                session_service=MagicMock(),
                app_name="ekaette_text",
                user_id="wa_2348001234567",
                message_text="Hello",
            )

        assert result["text"] == _DEFAULT_FALLBACK


class TestGetADKRunnerAndService:
    """Test the runner/service accessor."""

    def test_returns_none_tuple_when_runner_absent(self):
        """When main.py has no runner attribute, returns (None, None, None, None, "")."""
        from app.api.v1.at.service_whatsapp import _get_adk_runner_and_service

        # Simulate main module without runner initialized
        fake_main = MagicMock(spec=[])  # empty spec — no runner/session_service attrs
        with patch.dict("sys.modules", {"main": fake_main}):
            result = _get_adk_runner_and_service()
        assert isinstance(result, tuple)
        assert result == (None, None, None, None, "")
