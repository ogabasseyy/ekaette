"""SMS channel business logic.

Inbound SMS → Gemini text bridge, outbound send, campaign, truncation.
Routes delegate here — no business logic in sms.py.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)

SMS_MAX_CHARS = 160
DEFAULT_SMS_FALLBACK_REPLY = "Thanks for your message. How can I help you today?"
_DLR_STATUS_KEYS = ("status", "deliveryStatus", "messageStatus")
_DLR_MESSAGE_ID_KEYS = ("messageId", "message_id")
_DLR_FAILURE_KEYS = ("failureReason", "failure_reason", "errorMessage")
_TEXT_KEYS = ("text", "body", "message")


def _first_present(payload: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        raw_value = payload.get(key, "")
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value:
                return value
    return ""


def truncate_sms(text: str) -> str:
    """Truncate text to SMS character limit."""
    if len(text) <= SMS_MAX_CHARS:
        return text
    return text[: SMS_MAX_CHARS - 3] + "..."


def fallback_sms_reply() -> str:
    """Short, safe reply when AI generation is unavailable."""
    return DEFAULT_SMS_FALLBACK_REPLY


def is_delivery_report_payload(payload: Mapping[str, str]) -> bool:
    """Return True when an AT webhook payload looks like an SMS delivery report."""
    if _first_present(payload, *_TEXT_KEYS):
        return False
    return bool(
        _first_present(payload, *_DLR_STATUS_KEYS)
        or _first_present(payload, *_DLR_MESSAGE_ID_KEYS)
        or _first_present(payload, *_DLR_FAILURE_KEYS)
    )


def delivery_report_event_type(status: str) -> str | None:
    """Normalize AT/provider delivery statuses into analytics event types."""
    lowered = (status or "").strip().lower()
    if not lowered:
        return None
    if any(token in lowered for token in ("fail", "error", "reject", "block", "undeliver")):
        return "failed"
    if any(token in lowered for token in ("success", "deliver", "sent", "submit", "accept", "buffer")):
        return "delivered"
    return None
