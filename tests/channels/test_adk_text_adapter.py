"""Tests for ADK text channel adapter — TDD for unified channel routing."""

import pytest
from unittest.mock import AsyncMock, MagicMock


# ─── Helpers ───────────────────────────────────────────────────


def _make_event(text: str = "", is_partial: bool = False):
    """Create a mock ADK Event with text content."""
    part = MagicMock()
    part.text = text
    part.inline_data = None

    content = MagicMock()
    content.parts = [part]
    content.role = "model"

    event = MagicMock()
    event.content = content if text else None
    event.is_partial = is_partial
    event.actions = MagicMock()
    event.actions.transfer_to_agent = None
    event.actions.state_delta = {}
    return event


def _make_image_event():
    """Create a mock ADK Event with inline image data (no text)."""
    part = MagicMock()
    part.text = None
    part.inline_data = MagicMock()
    part.inline_data.mime_type = "image/jpeg"

    content = MagicMock()
    content.parts = [part]
    content.role = "model"

    event = MagicMock()
    event.content = content
    event.is_partial = False
    event.actions = MagicMock()
    event.actions.transfer_to_agent = None
    event.actions.state_delta = {}
    return event


async def _async_gen(*events):
    """Convert a list of events into an async generator."""
    for e in events:
        yield e


# ─── Test: send_text_message ──────────────────────────────────


class TestSendTextMessage:
    """Test sending a text message through the ADK runner."""

    @pytest.mark.asyncio
    async def test_returns_agent_text_response(self):
        """Basic text in → text out through runner.run_async."""
        from app.channels.adk_text_adapter import send_text_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(
                _make_event("Hello! How can I help you today?")
            )
        )

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-123")
        )

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_2348001234567",
            message_text="Hi there",
            channel="whatsapp",
        )

        assert result["text"] == "Hello! How can I help you today?"
        assert result["channel"] == "whatsapp"

    @pytest.mark.asyncio
    async def test_concatenates_multiple_text_events(self):
        """Multiple non-partial events are concatenated."""
        from app.channels.adk_text_adapter import send_text_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(
                _make_event("Part one. "),
                _make_event("Part two."),
            )
        )

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-123")
        )

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            message_text="Hello",
        )

        assert result["text"] == "Part one. \n\nPart two."

    @pytest.mark.asyncio
    async def test_skips_partial_events(self):
        """Partial (streaming) events are skipped — only final events collected."""
        from app.channels.adk_text_adapter import send_text_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(
                _make_event("Hel", is_partial=True),
                _make_event("Hello!", is_partial=False),
            )
        )

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-123")
        )

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            message_text="Hello",
        )

        assert result["text"] == "Hello!"

    @pytest.mark.asyncio
    async def test_skips_image_events(self):
        """Events with inline image data (no text) are ignored for text output."""
        from app.channels.adk_text_adapter import send_text_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(
                _make_image_event(),
                _make_event("Here is my analysis."),
            )
        )

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-123")
        )

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            message_text="Check this",
        )

        assert result["text"] == "Here is my analysis."

    @pytest.mark.asyncio
    async def test_empty_response_fallback(self):
        """If runner yields no text events, return a fallback message."""
        from app.channels.adk_text_adapter import send_text_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(return_value=_async_gen())

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-123")
        )

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            message_text="Hello",
        )

        assert result["text"]  # Non-empty fallback
        assert "help" in result["text"].lower()

    @pytest.mark.asyncio
    async def test_runner_exception_returns_error(self):
        """If runner raises, return a graceful error dict."""
        from app.channels.adk_text_adapter import send_text_message

        async def _failing_gen(**kwargs):
            raise RuntimeError("Model unavailable")
            yield  # noqa: unreachable — makes this an async generator

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(side_effect=_failing_gen)

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-123")
        )

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            message_text="Hello",
        )

        assert "text" in result
        assert result["text"]  # Non-empty fallback provided


# ─── Test: Session Management ─────────────────────────────────


class TestSessionManagement:
    """Test session get-or-create behavior."""

    @pytest.mark.asyncio
    async def test_resumes_existing_session(self):
        """If session exists for user, reuse it."""
        from app.channels.adk_text_adapter import send_text_message

        existing_session = MagicMock()
        existing_session.id = "existing-sess"
        existing_session.state = {"app:industry": "electronics"}

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(_make_event("Welcome back!"))
        )

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=existing_session)

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            message_text="Hi",
        )

        assert result["text"] == "Welcome back!"
        assert result["session_id"] == "existing-sess"
        mock_session_service.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_new_session_with_state(self):
        """If no session exists, creates one with bootstrapped state."""
        from app.channels.adk_text_adapter import send_text_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(_make_event("Hello!"))
        )

        created_session = MagicMock()
        created_session.id = "new-sess-456"

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(return_value=created_session)

        result = await send_text_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            message_text="Hi",
            tenant_id="public",
            company_id="ekaette-electronics",
        )

        assert result["session_id"] == "new-sess-456"
        mock_session_service.create_session.assert_called_once()
        create_kwargs = mock_session_service.create_session.call_args
        state = create_kwargs.kwargs.get("state", {})
        assert state.get("app:tenant_id") == "public"
        assert state.get("app:company_id") == "ekaette-electronics"


# ─── Test: send_image_message ─────────────────────────────────


class TestSendImageMessage:
    """Test sending an image through the ADK runner."""

    @pytest.mark.asyncio
    async def test_image_message_routes_through_runner(self):
        """Image bytes are sent as content to the runner."""
        from app.channels.adk_text_adapter import send_image_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(
                _make_event("I can see an iPhone 14 Pro in good condition.")
            )
        )

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-img")
        )

        result = await send_image_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            image_bytes=b"fake-jpeg-data",
            mime_type="image/jpeg",
            caption="Check this phone",
        )

        assert "iPhone" in result["text"]
        # Verify runner was called with content containing both image and text
        call_kwargs = mock_runner.run_async.call_args.kwargs
        new_message = call_kwargs["new_message"]
        assert len(new_message.parts) == 2  # image blob + caption text

    @pytest.mark.asyncio
    async def test_image_without_caption_uses_default(self):
        """If no caption, a default prompt is used."""
        from app.channels.adk_text_adapter import send_image_message

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(_make_event("Analysis complete."))
        )

        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-img2")
        )

        result = await send_image_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            image_bytes=b"fake-data",
            mime_type="image/jpeg",
        )

        assert result["text"] == "Analysis complete."
        call_kwargs = mock_runner.run_async.call_args.kwargs
        new_message = call_kwargs["new_message"]
        # Should have a default text part
        text_parts = [p for p in new_message.parts if p.text]
        assert len(text_parts) == 1


# ─── Test: Session ID derivation ──────────────────────────────


class TestSessionIdDerivation:
    """Test deterministic session ID from channel + user."""

    def test_whatsapp_session_id_is_deterministic(self):
        from app.channels.adk_text_adapter import derive_session_id

        sid1 = derive_session_id("whatsapp", "2348001234567")
        sid2 = derive_session_id("whatsapp", "2348001234567")
        assert sid1 == sid2

    def test_different_users_get_different_sessions(self):
        from app.channels.adk_text_adapter import derive_session_id

        sid1 = derive_session_id("whatsapp", "2348001234567")
        sid2 = derive_session_id("whatsapp", "2348009999999")
        assert sid1 != sid2

    def test_different_channels_get_different_sessions(self):
        from app.channels.adk_text_adapter import derive_session_id

        sid_wa = derive_session_id("whatsapp", "2348001234567")
        sid_sms = derive_session_id("sms", "2348001234567")
        assert sid_wa != sid_sms

    def test_session_id_is_safe_string(self):
        """Session IDs should be alphanumeric + hyphens only."""
        from app.channels.adk_text_adapter import derive_session_id

        sid = derive_session_id("whatsapp", "+234 800-123-4567")
        assert all(c.isalnum() or c == "-" for c in sid)

    def test_empty_channel_raises(self):
        from app.channels.adk_text_adapter import derive_session_id

        with pytest.raises(ValueError, match="channel"):
            derive_session_id("", "2348001234567")

    def test_empty_user_id_raises(self):
        from app.channels.adk_text_adapter import derive_session_id

        with pytest.raises(ValueError, match="user_id"):
            derive_session_id("whatsapp", "")

    def test_none_channel_raises(self):
        from app.channels.adk_text_adapter import derive_session_id

        with pytest.raises((ValueError, TypeError)):
            derive_session_id(None, "2348001234567")

    def test_none_user_id_raises(self):
        from app.channels.adk_text_adapter import derive_session_id

        with pytest.raises((ValueError, TypeError)):
            derive_session_id("whatsapp", None)

    def test_whitespace_only_channel_raises(self):
        from app.channels.adk_text_adapter import derive_session_id

        with pytest.raises(ValueError, match="channel"):
            derive_session_id("   ", "2348001234567")

    def test_whitespace_only_user_id_raises(self):
        from app.channels.adk_text_adapter import derive_session_id

        with pytest.raises(ValueError, match="user_id"):
            derive_session_id("whatsapp", "  ")


# ─── Test: Image size validation ─────────────────────────────


class TestImageSizeValidation:
    """Test that oversized images are rejected gracefully."""

    @pytest.mark.asyncio
    async def test_oversized_image_returns_friendly_error(self):
        from app.channels.adk_text_adapter import send_image_message, _MAX_MEDIA_BYTES

        mock_runner = MagicMock()
        mock_session_service = MagicMock()

        oversized = b"x" * (_MAX_MEDIA_BYTES + 1)
        result = await send_image_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            image_bytes=oversized,
            mime_type="image/jpeg",
        )

        assert "too large" in result["text"].lower()
        mock_runner.run_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_image_at_limit_is_accepted(self):
        from app.channels.adk_text_adapter import send_image_message, _MAX_MEDIA_BYTES

        mock_runner = MagicMock()
        mock_runner.run_async = MagicMock(
            return_value=_async_gen(_make_event("Analysis done."))
        )
        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock(
            return_value=MagicMock(id="sess-img")
        )

        exactly_at_limit = b"x" * _MAX_MEDIA_BYTES
        result = await send_image_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            image_bytes=exactly_at_limit,
        )

        assert result["text"] == "Analysis done."
        mock_runner.run_async.assert_called_once()


# ─── Test: Channel config ─────────────────────────────────────


class TestChannelConfig:
    """Test channel-specific configuration."""

    def test_whatsapp_max_chars(self):
        from app.channels.adk_text_adapter import CHANNEL_LIMITS

        assert CHANNEL_LIMITS["whatsapp"]["max_chars"] == 4096

    def test_sms_max_chars(self):
        from app.channels.adk_text_adapter import CHANNEL_LIMITS

        assert CHANNEL_LIMITS["sms"]["max_chars"] == 160

    def test_default_channel_has_limit(self):
        from app.channels.adk_text_adapter import CHANNEL_LIMITS

        assert "default" in CHANNEL_LIMITS


# ─── Test: empty media guard ─────────────────────────────────


class TestEmptyMediaGuard:
    """Empty media bytes should return early with a helpful message."""

    @pytest.mark.asyncio
    async def test_empty_media_bytes_returns_error(self):
        from app.channels.adk_text_adapter import send_media_message

        mock_runner = MagicMock()
        mock_session_service = MagicMock()

        result = await send_media_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            media_bytes=b"",
            mime_type="image/jpeg",
        )

        assert "empty" in result["text"].lower()
        mock_runner.run_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_media_bytes_preserves_channel(self):
        from app.channels.adk_text_adapter import send_media_message

        mock_runner = MagicMock()
        mock_session_service = MagicMock()

        result = await send_media_message(
            runner=mock_runner,
            session_service=mock_session_service,
            app_name="ekaette",
            user_id="wa_user",
            media_bytes=b"",
            mime_type="video/mp4",
            channel="whatsapp",
        )

        assert result["channel"] == "whatsapp"
