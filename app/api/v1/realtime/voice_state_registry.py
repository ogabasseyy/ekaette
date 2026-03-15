"""Ephemeral live voice-state registry for guard-critical session fields.

This is a runtime fallback for live audio sessions where the streaming loop and
tool callbacks do not reliably observe the same mutable session-state object.
It stores only the small subset of fields needed to enforce opening/transfer
guards safely.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

VOICE_STATE_BOOL_KEYS = (
    "temp:greeted",
    "temp:opening_greeting_complete",
    "temp:opening_greeting_server_owned",
    "temp:opening_phase_complete",
    "temp:first_user_turn_started",
    "temp:first_user_turn_complete",
)
VOICE_STATE_STR_KEYS = (
    "temp:last_user_turn",
    "temp:last_agent_turn",
    "temp:recent_customer_context",
    "temp:vision_media_handoff_state",
    "temp:background_vision_status",
    "temp:last_media_request_status",
    "temp:tradein_fulfillment_phase",
    "temp:last_delivery_quote_status",
    "temp:pending_media_received_voice_ack",
    "temp:pending_media_request_voice_ack",
    "temp:pending_questionnaire_voice_ack",
    "temp:pending_questionnaire_voice_text",
    "temp:pending_valuation_result_voice_ack",
    "temp:pending_handoff_target_agent",
    "temp:pending_handoff_latest_user",
    "temp:pending_handoff_latest_agent",
    "temp:pending_handoff_recent_customer_context",
    "temp:pending_transfer_bootstrap_target_agent",
    "temp:pending_transfer_bootstrap_reason",
)
VOICE_STATE_JSON_KEYS = (
    "temp:last_analysis",
    "temp:tradein_questionnaire_state",
    "temp:last_delivery_quote_details",
)
VOICE_STATE_INT_KEYS = (
    "temp:model_turn_count",
    "temp:last_offer_amount",
)
VOICE_STATE_KEYS = frozenset(
    VOICE_STATE_BOOL_KEYS
    + VOICE_STATE_STR_KEYS
    + VOICE_STATE_JSON_KEYS
    + VOICE_STATE_INT_KEYS
)

_LOCK = threading.Lock()
_BY_SESSION: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
_TTL_SECONDS = 3600.0


def _prune_expired(now: float) -> None:
    """Remove expired entries from _BY_SESSION. Caller must hold _LOCK."""
    expired = [
        key for key, (_payload, expires_at) in _BY_SESSION.items() if expires_at <= now
    ]
    for key in expired:
        _BY_SESSION.pop(key, None)


def update_voice_state(
    *,
    user_id: str,
    session_id: str,
    ttl_seconds: float = _TTL_SECONDS,
    **fields: Any,
) -> None:
    """Persist guard-relevant voice session state for this live call."""
    user_key = user_id.strip()
    session_key = session_id.strip()
    if not (user_key and session_key):
        return

    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in VOICE_STATE_KEYS:
            continue
        if key in VOICE_STATE_BOOL_KEYS:
            # These are monotonic session markers. They are cleared by
            # clear_registered_voice_state() at session teardown, not toggled
            # back to False mid-call.
            if bool(value):
                normalized[key] = True
            continue
        if key in VOICE_STATE_STR_KEYS:
            if isinstance(value, str):
                normalized[key] = value.strip()
            continue
        if key in VOICE_STATE_JSON_KEYS:
            if isinstance(value, dict):
                normalized[key] = copy.deepcopy(value)
            continue
        if key in VOICE_STATE_INT_KEYS:
            try:
                parsed = int(value or 0)
            except (TypeError, ValueError):
                parsed = 0
            normalized[key] = parsed

    if not normalized:
        return

    now = time.time()
    expires_at = now + max(60.0, float(ttl_seconds))
    with _LOCK:
        _prune_expired(now)
        session_key_tuple = (user_key, session_key)
        current_payload, _current_expires_at = _BY_SESSION.get(session_key_tuple, ({}, 0.0))
        merged = dict(current_payload)
        merged.update(normalized)
        _BY_SESSION[session_key_tuple] = (merged, expires_at)


def get_registered_voice_state(*, user_id: str, session_id: str) -> dict[str, Any]:
    """Return a copy of the remembered live voice state when present."""
    user_key = user_id.strip()
    session_key = session_id.strip()
    if not (user_key and session_key):
        return {}

    now = time.time()
    with _LOCK:
        _prune_expired(now)
        payload = _BY_SESSION.get((user_key, session_key))
        if not payload:
            return {}
        return copy.deepcopy(payload[0])


def clear_registered_voice_state(*, user_id: str, session_id: str) -> None:
    """Remove remembered live voice state for a session."""
    user_key = user_id.strip()
    session_key = session_id.strip()
    if not (user_key and session_key):
        return

    with _LOCK:
        _BY_SESSION.pop((user_key, session_key), None)
