"""Tests for WhatsApp silence nudge (2-minute follow-up)."""

from __future__ import annotations

import time

import pytest
from unittest.mock import AsyncMock, patch

from app.api.v1.at.service_whatsapp import (
    record_inbound_timestamp,
    reset_service_windows,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_service_windows()
    yield
    reset_service_windows()


class TestRecordOutboundTimestamp:
    """Track when Ekaette last sent a reply to a user."""

    def test_records_outbound_timestamp(self):
        from app.api.v1.at.service_whatsapp import (
            record_outbound_timestamp,
            _outbound_timestamps,
            _window_key,
        )

        record_outbound_timestamp("2348001234567", "phone123")
        key = _window_key("2348001234567", "phone123", "public", "ekaette-electronics")
        assert key in _outbound_timestamps
        assert isinstance(_outbound_timestamps[key], float)

    def test_outbound_overwrites_previous(self):
        from app.api.v1.at.service_whatsapp import (
            record_outbound_timestamp,
            _outbound_timestamps,
            _window_key,
        )

        record_outbound_timestamp("2348001234567", "phone123")
        key = _window_key("2348001234567", "phone123", "public", "ekaette-electronics")
        first_ts = _outbound_timestamps[key]

        # Small sleep to ensure different timestamp
        import time
        time.sleep(0.01)
        record_outbound_timestamp("2348001234567", "phone123")
        assert _outbound_timestamps[key] > first_ts


class TestCheckNeedsNudge:
    """Determine whether a nudge should be sent."""

    def test_needs_nudge_when_outbound_newer_than_inbound(self):
        from app.api.v1.at.service_whatsapp import (
            check_needs_nudge,
            record_outbound_timestamp,
        )

        # User sent message at time T, Ekaette replied at T+1
        record_inbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_outbound_timestamp("2348001234567", "phone123")

        # Nudge check: outbound is newer → user hasn't replied → needs nudge
        assert check_needs_nudge("2348001234567", "phone123") is True

    def test_no_nudge_when_user_replied_after_outbound(self):
        from app.api.v1.at.service_whatsapp import (
            check_needs_nudge,
            record_outbound_timestamp,
        )

        # Ekaette replied, then user sent another message
        record_outbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_inbound_timestamp("2348001234567", "phone123")

        assert check_needs_nudge("2348001234567", "phone123") is False

    def test_no_nudge_when_no_outbound(self):
        from app.api.v1.at.service_whatsapp import check_needs_nudge

        # No outbound ever sent
        assert check_needs_nudge("2348001234567", "phone123") is False

    def test_no_nudge_when_already_nudged(self):
        from app.api.v1.at.service_whatsapp import (
            check_needs_nudge,
            record_outbound_timestamp,
            mark_nudge_sent,
        )

        record_inbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_outbound_timestamp("2348001234567", "phone123")

        mark_nudge_sent("2348001234567", "phone123")
        assert check_needs_nudge("2348001234567", "phone123") is False

    def test_nudge_resets_after_new_inbound(self):
        """After user sends a new message and gets a reply, nudge is available again."""
        from app.api.v1.at.service_whatsapp import (
            check_needs_nudge,
            record_outbound_timestamp,
            mark_nudge_sent,
        )

        # First cycle: reply → nudge sent
        record_inbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_outbound_timestamp("2348001234567", "phone123")
        mark_nudge_sent("2348001234567", "phone123")
        assert check_needs_nudge("2348001234567", "phone123") is False

        # New conversation turn: user sends message → new reply
        time.sleep(0.01)
        record_inbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_outbound_timestamp("2348001234567", "phone123")

        # Nudge should be available again since new outbound is after nudge
        assert check_needs_nudge("2348001234567", "phone123") is True

    def test_no_nudge_during_cross_session_suppression(self):
        from app.api.v1.at.service_whatsapp import (
            check_needs_nudge,
            record_outbound_timestamp,
            suppress_nudge_for_cross_session,
        )

        record_inbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_outbound_timestamp("2348001234567", "phone123")
        suppress_nudge_for_cross_session(
            "2348001234567",
            "phone123",
            ttl_seconds=120,
        )

        assert check_needs_nudge("2348001234567", "phone123") is False


class TestNudgeMessage:
    """Nudge message content."""

    def test_nudge_message_is_concise(self):
        from app.api.v1.at.service_whatsapp import get_nudge_message

        msg = get_nudge_message()
        assert isinstance(msg, str)
        assert len(msg) < 200  # Keep it short for WhatsApp
        assert len(msg) > 10  # Not empty


class TestNudgeInProcessMessage:
    """Nudge scheduling in the message processing pipeline."""

    @pytest.mark.asyncio
    async def test_nudge_scheduled_after_successful_reply(self):
        """After sending a reply, a nudge check is scheduled."""
        from app.api.v1.at.whatsapp import _process_message

        nudge_scheduled = False

        async def mock_handle_text(**kwargs):
            return "Here's what I found!"

        async def mock_send_text(**kwargs):
            return 200, {"messages": [{"id": "wamid.reply1"}]}

        async def mock_typing(**kwargs):
            pass

        async def mock_schedule_nudge(**kwargs):
            nonlocal nudge_scheduled
            nudge_scheduled = True

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
            side_effect=mock_schedule_nudge,
        ) as sched_mock:
            message = {
                "from": "2348001234567",
                "id": "wamid.inbound123",
                "type": "text",
                "text": {"body": "What phones do you have?"},
            }
            await _process_message(message, "test_phone_id")

        assert nudge_scheduled

    @pytest.mark.asyncio
    async def test_schedule_nudge_returns_early_when_cross_session_is_suppressed(self):
        """Cross-session media replies should not create delayed nudge work."""
        from app.api.v1.at.whatsapp import _schedule_nudge
        from app.api.v1.at import service_whatsapp

        service_whatsapp.suppress_nudge_for_cross_session(
            "2348001234567",
            "test_phone_id",
            ttl_seconds=120,
        )

        with patch(
            "app.api.v1.at.whatsapp.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as to_thread_mock, patch(
            "app.api.v1.at.whatsapp.asyncio.create_task",
        ) as create_task_mock:
            await _schedule_nudge("2348001234567", "test_phone_id")

        to_thread_mock.assert_not_awaited()
        create_task_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_on_failed_send(self):
        """No nudge scheduled when reply send fails."""
        from app.api.v1.at.whatsapp import _process_message

        async def mock_handle_text(**kwargs):
            return "Reply!"

        async def mock_send_text(**kwargs):
            return 500, {"error": "fail"}

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
        ) as sched_mock:
            message = {
                "from": "2348001234567",
                "id": "wamid.inbound456",
                "type": "text",
                "text": {"body": "Hi"},
            }
            with pytest.raises(RuntimeError):
                await _process_message(message, "test_phone_id")

        sched_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_handler_sends_when_no_reply(self):
        """The nudge handler sends a message when user hasn't replied."""
        from app.api.v1.at.whatsapp import _execute_nudge
        from app.api.v1.at.service_whatsapp import record_outbound_timestamp

        # Simulate: Ekaette replied but user hasn't responded
        record_inbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_outbound_timestamp("2348001234567", "phone123")

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            new_callable=AsyncMock,
            return_value=(200, {"messages": [{"id": "wamid.nudge1"}]}),
        ) as send_mock:
            await _execute_nudge("2348001234567", "phone123")

        send_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_handler_skips_when_user_replied(self):
        """The nudge handler does nothing if user already replied."""
        from app.api.v1.at.whatsapp import _execute_nudge
        from app.api.v1.at.service_whatsapp import record_outbound_timestamp

        # Simulate: Ekaette replied AND user replied back
        record_outbound_timestamp("2348001234567", "phone123")
        time.sleep(0.01)
        record_inbound_timestamp("2348001234567", "phone123")

        with patch(
            "app.api.v1.at.whatsapp.providers.whatsapp_send_text",
            new_callable=AsyncMock,
        ) as send_mock:
            await _execute_nudge("2348001234567", "phone123")

        send_mock.assert_not_called()
