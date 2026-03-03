"""Dependency injection for AT channels.

AT SDK init and shared httpx client lifecycle.
"""

from __future__ import annotations

import logging

from .settings import AT_USERNAME, AT_API_KEY

logger = logging.getLogger(__name__)


def init_at_sdk() -> None:
    """Initialize Africa's Talking SDK. Call once at app startup."""
    if getattr(init_at_sdk, "_initialized", False):
        return
    if not AT_API_KEY:
        logger.warning("AT credentials not set — SDK not initialized (sandbox mode)")
        return
    try:
        import africastalking
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        setattr(init_at_sdk, "_initialized", True)
        logger.info("AT SDK initialized (username=%s)", AT_USERNAME)
    except Exception:
        logger.exception("Failed to initialize AT SDK")
