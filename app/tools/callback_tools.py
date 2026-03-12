"""ADK tool for queuing a callback to the live caller."""

from __future__ import annotations

import logging

from app.api.v1.at import service_voice
from app.tools.sms_messaging import resolve_caller_phone_from_context

logger = logging.getLogger(__name__)


async def request_callback(reason: str = "", tool_context=None) -> dict:
    """Queue a callback to the current caller after the active call ends."""
    state = getattr(tool_context, "state", {})
    session_id = str(getattr(state, "get", lambda *_: "")("app:session_id", "") or "").strip()
    if session_id.startswith("sip-callback-"):
        logger.info("request_callback blocked on callback leg session_id=%s", session_id)
        return {
            "status": "error",
            "error": "already_on_callback",
            "detail": "A callback is already in progress for this caller.",
        }
    caller_phone = resolve_caller_phone_from_context(tool_context)
    if not caller_phone:
        logger.warning("request_callback failed: no caller phone in live context")
        return {
            "status": "error",
            "error": "No caller phone in session",
            "detail": "No caller phone in session",
        }

    tenant_id = str(getattr(state, "get", lambda *_: "public")("app:tenant_id", "public") or "public")
    company_id = str(
        getattr(state, "get", lambda *_: "ekaette-electronics")(
            "app:company_id",
            "ekaette-electronics",
        ) or "ekaette-electronics"
    )

    result = service_voice.register_callback_request(
        phone=caller_phone,
        tenant_id=tenant_id,
        company_id=company_id,
        source="voice_ai_request",
        reason=reason or "",
        trigger_after_hangup=True,
    )
    status = str(result.get("status", "")).strip().lower() if isinstance(result, dict) else ""
    if status == "error":
        detail = str(result.get("detail", "")).strip() if isinstance(result, dict) else ""
        logger.warning(
            "request_callback failed phone=%s tenant_id=%s company_id=%s detail=%s",
            caller_phone,
            tenant_id,
            company_id,
            detail,
        )
        result.setdefault("error", detail or "Callback request unavailable")
    elif status in {"pending", "queued", "cooldown"}:
        logger.info(
            "request_callback status=%s phone=%s tenant_id=%s company_id=%s",
            status,
            caller_phone,
            tenant_id,
            company_id,
        )
    else:
        logger.warning(
            "request_callback returned unexpected status=%s phone=%s tenant_id=%s company_id=%s",
            status,
            caller_phone,
            tenant_id,
            company_id,
        )
    return result
