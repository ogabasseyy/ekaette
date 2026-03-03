"""SMS channel business logic.

Inbound SMS → Gemini text bridge, outbound send, campaign, truncation.
Routes delegate here — no business logic in sms.py.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SMS_MAX_CHARS = 160


def truncate_sms(text: str) -> str:
    """Truncate text to SMS character limit."""
    if len(text) <= SMS_MAX_CHARS:
        return text
    return text[: SMS_MAX_CHARS - 3] + "..."
