"""ADK tool for ending an active voice call after the current turn drains."""

from __future__ import annotations

from typing import Any


def _get_state_value(state: Any, key: str, default: Any = None) -> Any:
    getter = getattr(state, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    return default


async def end_call(reason: str = "", tool_context=None) -> dict:
    """Request a clean voice-call hangup after the current speech drains."""
    state = getattr(tool_context, "state", None)
    channel = str(_get_state_value(state, "app:channel", "") or "").strip().lower()
    if channel != "voice":
        return {
            "status": "error",
            "error": "not_voice_channel",
            "detail": "end_call is only available during voice sessions.",
        }

    resolved_reason = reason.strip() if isinstance(reason, str) else ""
    already_requested = bool(
        _get_state_value(state, "temp:call_end_after_speaking_requested", False)
    )
    return {
        "status": "ok",
        "action": "end_after_speaking",
        "reason": resolved_reason or "conversation_complete",
        "already_requested": already_requested,
    }
