"""WhatsApp webhook + service-to-service + Cloud Tasks OIDC security.

Three FastAPI dependencies:
  1. verify_wa_webhook — Meta HMAC-SHA256 on raw bytes + edge rate-limit gate
  2. verify_service_auth — HMAC + timestamp + nonce (for /whatsapp/send)
  3. verify_cloud_tasks_oidc — Google OIDC token + queue/task headers (for /whatsapp/process)
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import logging
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from .settings import (
    WHATSAPP_APP_SECRET,
    WHATSAPP_VERIFY_TOKEN,
    WA_CLOUD_TASKS_AUDIENCE,
    WA_CLOUD_TASKS_QUEUE_NAME,
    WA_EDGE_RATELIMIT_HEADER,
    WA_SERVICE_AUTH_MAX_SKEW_SECONDS,
    WA_SERVICE_SECRET,
    WA_SERVICE_SECRET_PREVIOUS,
    WA_WEBHOOK_RATE_LIMIT_MODE,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Best-effort local rate limiting (fallback only)
from app.api.v1.public.core_helpers import check_rate_limit as _check_rate_limit

_wa_rate_buckets: dict[str, list[float]] = {}
_wa_last_prune: float = 0.0
WA_RATE_LIMIT: int = 60
WA_RATE_WINDOW: int = 60


# ── 1. Meta Webhook HMAC Verification ──


async def verify_wa_webhook(request: Request) -> bytes:
    """FastAPI dependency. Returns raw body bytes on success, raises 403 on failure.

    MUST read raw bytes before JSON parsing — Meta computes HMAC on wire format.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature:
        raise HTTPException(status_code=403, detail="Missing signature")

    if not WHATSAPP_APP_SECRET:
        logger.error("WHATSAPP_APP_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    expected = hmac_mod.new(
        WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    received = signature.removeprefix("sha256=")

    if not hmac_mod.compare_digest(expected, received):
        logger.warning("WA webhook HMAC mismatch")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Edge rate-limit gate
    if WA_WEBHOOK_RATE_LIMIT_MODE == "edge_enforced":
        edge_header = request.headers.get(WA_EDGE_RATELIMIT_HEADER, "")
        if edge_header != "1":
            logger.warning("WA webhook missing edge rate-limit header")
            raise HTTPException(status_code=403, detail="Edge verification required")
    elif WA_WEBHOOK_RATE_LIMIT_MODE == "best_effort_local":
        global _wa_last_prune
        client_ip = request.client.host if request.client else ""
        allowed, _wa_last_prune = _check_rate_limit(
            client_ip=client_ip,
            bucket="wa_webhook",
            limit=WA_RATE_LIMIT,
            window_seconds=WA_RATE_WINDOW,
            max_buckets=1000,
            buckets=_wa_rate_buckets,
            last_global_prune=_wa_last_prune,
        )
        if not allowed:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return raw_body


def verify_wa_verify_token(token: str) -> bool:
    """Verify the hub.verify_token matches our configured token."""
    if not WHATSAPP_VERIFY_TOKEN:
        return False
    return hmac_mod.compare_digest(token, WHATSAPP_VERIFY_TOKEN)


# ── 2. Service-to-Service Auth (/whatsapp/send) ──


def _verify_hmac_with_secret(secret: str, message: str, received_hmac: str) -> bool:
    """Verify HMAC-SHA256 with a given secret."""
    if not secret:
        return False
    expected = hmac_mod.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return hmac_mod.compare_digest(expected, received_hmac)


async def verify_service_auth(request: Request) -> None:
    """Verify X-Service-Auth HMAC, X-Service-Timestamp freshness, X-Service-Nonce uniqueness.

    HMAC = SHA256(WA_SERVICE_SECRET, "{timestamp}:{nonce}:{body}")
    """
    timestamp_str = request.headers.get("X-Service-Timestamp", "")
    nonce = request.headers.get("X-Service-Nonce", "")
    auth_hmac = request.headers.get("X-Service-Auth", "")

    if not timestamp_str or not nonce or not auth_hmac:
        raise HTTPException(status_code=403, detail="Missing service auth headers")

    # Timestamp freshness
    try:
        ts = float(timestamp_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=403, detail="Invalid timestamp")

    skew = abs(time.time() - ts)
    if skew > WA_SERVICE_AUTH_MAX_SKEW_SECONDS:
        raise HTTPException(status_code=403, detail="Timestamp expired")

    # Read body for HMAC
    raw_body = await request.body()
    message = f"{timestamp_str}:{nonce}:{raw_body.decode('utf-8', errors='replace')}"

    # Verify HMAC against current secret (and optionally previous during rotation)
    if not _verify_hmac_with_secret(WA_SERVICE_SECRET, message, auth_hmac):
        if not _verify_hmac_with_secret(WA_SERVICE_SECRET_PREVIOUS, message, auth_hmac):
            raise HTTPException(status_code=403, detail="Invalid service auth")

    # Nonce replay check (Firestore atomic create in production)
    # For now, defer to caller or use in-process set
    # Production: doc_ref.create() on wa_nonces/{nonce}
    if not await _check_nonce(nonce):
        raise HTTPException(status_code=403, detail="Nonce replay detected")


# Nonce storage — in-process for dev, Firestore for prod
_nonce_store: set[str] = set()
_nonce_timestamps: dict[str, float] = {}
_NONCE_TTL = 300  # 5 minutes


async def _check_nonce(nonce: str) -> bool:
    """Check nonce uniqueness. Returns True if nonce is fresh."""
    now = time.time()
    # Prune expired nonces
    expired = [n for n, ts in _nonce_timestamps.items() if now - ts > _NONCE_TTL]
    for n in expired:
        _nonce_store.discard(n)
        _nonce_timestamps.pop(n, None)

    if nonce in _nonce_store:
        return False
    _nonce_store.add(nonce)
    _nonce_timestamps[nonce] = now
    return True


def reset_nonce_store() -> None:
    """Reset nonce store (for testing)."""
    _nonce_store.clear()
    _nonce_timestamps.clear()


# ── 3. Cloud Tasks OIDC Verification (/whatsapp/process) ──


async def verify_cloud_tasks_oidc(request: Request) -> None:
    """Verify OIDC token from Cloud Tasks and validate queue/task headers.

    Checks:
    1. Authorization Bearer token is a valid Google OIDC token
    2. Token audience matches WA_CLOUD_TASKS_AUDIENCE
    3. Token issuer is Google
    4. Service account email matches wa-tasks-invoker
    5. X-CloudTasks-QueueName matches WA_CLOUD_TASKS_QUEUE_NAME
    6. X-CloudTasks-TaskName starts with "wa-"
    """
    # Validate Cloud Tasks headers first (cheaper check)
    queue_name = request.headers.get("X-CloudTasks-QueueName", "")
    task_name = request.headers.get("X-CloudTasks-TaskName", "")

    if not queue_name or not task_name:
        raise HTTPException(status_code=403, detail="Missing Cloud Tasks headers")

    if queue_name != WA_CLOUD_TASKS_QUEUE_NAME:
        logger.warning("Cloud Tasks queue mismatch: %s", queue_name)
        raise HTTPException(status_code=403, detail="Invalid queue")

    if not task_name.startswith("wa-"):
        logger.warning("Cloud Tasks task name invalid: %s", task_name)
        raise HTTPException(status_code=403, detail="Invalid task name")

    # Verify OIDC token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Missing OIDC token")

    token = auth_header.removeprefix("Bearer ")

    try:
        claims = await _verify_oidc_token(token)
    except Exception:
        logger.warning("Cloud Tasks OIDC verification failed", exc_info=True)
        raise HTTPException(status_code=403, detail="Invalid OIDC token")

    # Validate audience
    if claims.get("aud") != WA_CLOUD_TASKS_AUDIENCE:
        raise HTTPException(status_code=403, detail="Invalid OIDC audience")

    # Validate issuer
    issuer = claims.get("iss", "")
    if issuer not in ("https://accounts.google.com", "accounts.google.com"):
        raise HTTPException(status_code=403, detail="Invalid OIDC issuer")

    # Validate service account email
    email = claims.get("email", "")
    if not email.startswith("wa-tasks-invoker@"):
        raise HTTPException(status_code=403, detail="Invalid service account")

    email_verified = claims.get("email_verified")
    if email_verified is not None and not email_verified:
        raise HTTPException(status_code=403, detail="Email not verified")


async def _verify_oidc_token(token: str) -> dict:
    """Verify a Google OIDC token. Returns claims dict.

    Uses google.oauth2.id_token for production.
    Falls back to a stub for testing.
    """
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        import asyncio

        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            token,
            google_requests.Request(),
            audience=WA_CLOUD_TASKS_AUDIENCE,
        )
        return claims
    except ImportError:
        logger.warning("google-auth not available, OIDC verification skipped")
        raise HTTPException(status_code=500, detail="OIDC verification unavailable")
