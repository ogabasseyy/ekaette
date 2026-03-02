"""AT webhook security: IP allowlist + rate limiting.

Reuses check_rate_limit() from app/api/v1/public/core_helpers.py.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from .settings import ALLOWED_SOURCE_IPS
from app.api.v1.public.core_helpers import check_rate_limit as _check_rate_limit

logger = logging.getLogger(__name__)

# Rate limit state (in-process, matches main.py pattern)
_at_rate_buckets: dict[str, list[float]] = {}
_at_last_prune: float = 0.0
AT_RATE_LIMIT: int = 30       # requests per window per IP
AT_RATE_WINDOW: int = 60      # seconds


async def verify_at_webhook(request: Request) -> None:
    """Verify AT callback authenticity. Raise 403/429 on failure.

    Checks run in order:
    1. IP allowlist (if configured) — 403 on mismatch
    2. Rate limit — 429 on threshold breach
    """
    client_ip = request.client.host if request.client else ""

    # 1. IP allowlist
    if ALLOWED_SOURCE_IPS and client_ip not in ALLOWED_SOURCE_IPS:
        logger.warning("AT webhook rejected: IP %s not in allowlist", client_ip)
        raise HTTPException(status_code=403, detail="Unauthorized source")

    # 2. Rate limit (reuses existing bucket algorithm)
    global _at_last_prune
    allowed, _at_last_prune = _check_rate_limit(
        client_ip=client_ip,
        bucket="at",
        limit=AT_RATE_LIMIT,
        window_seconds=AT_RATE_WINDOW,
        max_buckets=1000,
        buckets=_at_rate_buckets,
        last_global_prune=_at_last_prune,
    )
    if not allowed:
        logger.warning("AT webhook rate-limited: IP %s", client_ip)
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
