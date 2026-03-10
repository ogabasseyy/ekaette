"""Ephemeral caller-phone registry for live voice sessions.

This is a runtime fallback for tool execution paths where ADK session state
has not yet surfaced ``user:caller_phone`` to the current tool context.
"""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_BY_SESSION: dict[tuple[str, str], tuple[str, float]] = {}
_BY_USER: dict[str, tuple[str, float]] = {}
_TTL_SECONDS = 3600.0


def _prune_expired(now: float) -> None:
    expired_sessions = [
        key for key, (_phone, expires_at) in _BY_SESSION.items() if expires_at <= now
    ]
    for key in expired_sessions:
        _BY_SESSION.pop(key, None)

    expired_users = [
        user_id for user_id, (_phone, expires_at) in _BY_USER.items() if expires_at <= now
    ]
    for user_id in expired_users:
        _BY_USER.pop(user_id, None)


def register_caller_phone(
    *,
    user_id: str,
    session_id: str,
    caller_phone: str,
    ttl_seconds: float = _TTL_SECONDS,
) -> None:
    """Remember caller phone for the active live voice session."""
    user_key = user_id.strip()
    session_key = session_id.strip()
    phone = caller_phone.strip()
    if not (user_key and session_key and phone):
        return

    now = time.time()
    expires_at = now + max(60.0, float(ttl_seconds))
    with _LOCK:
        _prune_expired(now)
        _BY_SESSION[(user_key, session_key)] = (phone, expires_at)
        _BY_USER[user_key] = (phone, expires_at)


def get_registered_caller_phone(*, user_id: str, session_id: str = "") -> str:
    """Return the remembered caller phone for a live session when present."""
    now = time.time()
    user_key = user_id.strip()
    session_key = session_id.strip()
    with _LOCK:
        _prune_expired(now)
        if user_key and session_key:
            payload = _BY_SESSION.get((user_key, session_key))
            if payload:
                return payload[0]
        if user_key:
            payload = _BY_USER.get(user_key)
            if payload:
                return payload[0]
    return ""


def clear_registered_caller_phone(*, user_id: str, session_id: str = "") -> None:
    """Remove remembered caller phone for a live session."""
    user_key = user_id.strip()
    session_key = session_id.strip()
    with _LOCK:
        if user_key and session_key:
            _BY_SESSION.pop((user_key, session_key), None)
        if user_key:
            _BY_USER.pop(user_key, None)

