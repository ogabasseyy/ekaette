"""WebSocket authentication token generation and validation.

Uses HMAC-SHA256 signed tokens (no PyJWT dependency).
Tokens are single-use (JTI tracking) and short-lived.

When WS_TOKEN_SECRET is empty, WS auth is disabled (dev fallback).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import NamedTuple

# Mutable at module level so tests can override without env patching.
_WS_TOKEN_SECRET: str = ""

# In-memory used-JTI set for single-use enforcement.
_used_jtis: dict[str, float] = {}  # jti -> expiration timestamp
_used_jtis_lock = threading.Lock()
_MAX_USED_JTIS = 10_000


class WsTokenClaims(NamedTuple):
    sub: str  # user_id
    tenant_id: str
    company_id: str
    exp: float  # expiration unix timestamp
    jti: str  # unique token ID


def _get_secret() -> bytes:
    if not _WS_TOKEN_SECRET:
        raise ValueError("WS_TOKEN_SECRET not configured")
    return _WS_TOKEN_SECRET.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_ws_token(
    user_id: str,
    tenant_id: str,
    company_id: str,
    ttl_seconds: int,
) -> str:
    """Create a signed WS auth token (compact HMAC JWT-like)."""
    secret = _get_secret()

    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "WS"}).encode())
    payload_dict = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "exp": time.time() + ttl_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    payload = _b64url_encode(json.dumps(payload_dict).encode())

    signing_input = f"{header}.{payload}".encode()
    signature = _b64url_encode(
        hmac.new(secret, signing_input, hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def validate_ws_token(token: str, expected_user_id: str) -> WsTokenClaims | None:
    """Validate and consume a WS auth token.  Returns claims or None."""
    if not _WS_TOKEN_SECRET or not token:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    header_b64, payload_b64, sig_b64 = parts

    # Verify signature
    try:
        secret = _get_secret()
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
    except Exception:
        return None

    # Decode payload
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None

    # Check expiration
    exp = payload.get("exp", 0)
    if not isinstance(exp, (int, float)):
        return None
    if time.time() > exp:
        return None

    # Check user match
    sub = payload.get("sub", "")
    if sub != expected_user_id:
        return None

    # Single-use JTI check
    jti = payload.get("jti", "")
    if not jti:
        return None

    with _used_jtis_lock:
        _prune_used_jtis()

        if jti in _used_jtis:
            return None
        _used_jtis[jti] = exp

    return WsTokenClaims(
        sub=sub,
        tenant_id=payload.get("tenant_id", ""),
        company_id=payload.get("company_id", ""),
        exp=exp,
        jti=jti,
    )


def _prune_used_jtis() -> None:
    """Remove expired JTI entries to prevent unbounded growth."""
    now = time.time()
    expired = [jti for jti, exp in _used_jtis.items() if now > exp]
    for jti in expired:
        del _used_jtis[jti]

    # Safety cap
    if len(_used_jtis) > _MAX_USED_JTIS:
        oldest = sorted(_used_jtis, key=_used_jtis.get)[:len(_used_jtis) - _MAX_USED_JTIS]
        for jti in oldest:
            del _used_jtis[jti]
