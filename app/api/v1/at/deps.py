"""Dependency injection for AT channels.

AT SDK init and shared httpx client lifecycle.
"""

from __future__ import annotations

import logging

from .settings import AT_USERNAME, AT_API_KEY

logger = logging.getLogger(__name__)

_sdk_initialized: bool = False


def init_at_sdk() -> None:
    """Initialize Africa's Talking SDK. Call once at app startup."""
    global _sdk_initialized
    if _sdk_initialized:
        return
    if not AT_API_KEY:
        logger.warning("AT credentials not set — SDK not initialized (sandbox mode)")
        return
    try:
        import africastalking
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        _sdk_initialized = True
        logger.info("AT SDK initialized (username=%s)", AT_USERNAME)
    except Exception:
        logger.exception("Failed to initialize AT SDK")
