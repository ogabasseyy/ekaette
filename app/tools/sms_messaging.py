"""ADK function tool for during-call SMS messaging.

Uses the caller phone already present in session state and sends via Africa's
Talking SMS with a sender ID derived from company context.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.api.v1.realtime.caller_phone_registry import get_registered_caller_phone

logger = logging.getLogger(__name__)

_SENDER_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _resolve_phone_from_state_like(state_like: Any) -> str:
    """Return caller phone from a mapping/state-like object when present."""
    getter = getattr(state_like, "get", None)
    if not callable(getter):
        return ""
    caller_phone = getter("user:caller_phone", "")
    if isinstance(caller_phone, str) and caller_phone.strip():
        return caller_phone.strip()
    legacy_phone = getter("user:phone", "")
    return legacy_phone.strip() if isinstance(legacy_phone, str) else ""


def _resolve_registry_identity_from_state_like(state_like: Any) -> tuple[str, str]:
    """Extract runtime registry identifiers from state-like objects."""
    getter = getattr(state_like, "get", None)
    if not callable(getter):
        return "", ""

    raw_user_id = getter("app:user_id", "")
    user_id = raw_user_id.strip() if isinstance(raw_user_id, str) else ""
    raw_session_id = getter("app:session_id", "")
    session_id = raw_session_id.strip() if isinstance(raw_session_id, str) else ""
    return user_id, session_id


def resolve_caller_phone_from_state(state: Any) -> str:
    """Return normalized caller phone from session state when present."""
    return _resolve_phone_from_state_like(state)


def resolve_caller_phone_from_context(context: Any) -> str:
    """Resolve caller phone from a tool/callback context with session fallback."""
    direct_state = getattr(context, "state", None)
    phone = _resolve_phone_from_state_like(direct_state)
    if phone:
        return phone

    session = getattr(context, "session", None)
    session_state = getattr(session, "state", None)
    phone = _resolve_phone_from_state_like(session_state)
    if phone:
        return phone

    phone = _resolve_phone_from_state_like(context)
    if phone:
        return phone

    user_id = str(getattr(context, "user_id", "") or "").strip()
    session_id = str(getattr(getattr(context, "session", None), "id", "") or "").strip()
    if not user_id or not session_id:
        state_user_id, state_session_id = _resolve_registry_identity_from_state_like(direct_state)
        user_id = user_id or state_user_id
        session_id = session_id or state_session_id
    if not user_id or not session_id:
        state_user_id, state_session_id = _resolve_registry_identity_from_state_like(session_state)
        user_id = user_id or state_user_id
        session_id = session_id or state_session_id
    return get_registered_caller_phone(user_id=str(user_id), session_id=str(session_id))


def resolve_sms_sender_id_from_state(state: Any) -> str:
    """Resolve an SMS sender ID from company context or configured override.

    Africa's Talking sender IDs are account-managed and typically limited to
    short alphanumeric labels, so we normalize to a compact <=11 char token.
    """
    getter = getattr(state, "get", None)
    company_profile = getter("app:company_profile", {}) if callable(getter) else {}
    company_name = getter("app:company_name", "") if callable(getter) else ""

    explicit = ""
    if isinstance(company_profile, dict):
        raw_profile_sender = company_profile.get("sms_sender_id")
        if isinstance(raw_profile_sender, str):
            explicit = raw_profile_sender.strip()
        if not explicit:
            facts = company_profile.get("facts")
            if isinstance(facts, dict):
                raw_fact_sender = facts.get("sms_sender_id")
                if isinstance(raw_fact_sender, str):
                    explicit = raw_fact_sender.strip()

    if not explicit:
        env_sender = os.getenv("AT_SMS_SENDER_ID", "").strip()
        if env_sender:
            explicit = env_sender

    raw_value = explicit or (company_name.strip() if isinstance(company_name, str) else "")
    tokens = _SENDER_TOKEN_RE.findall(raw_value)
    if not tokens:
        return ""

    joined = "".join(tokens)
    if len(joined) <= 11:
        return joined

    first = tokens[0]
    if len(first) <= 11:
        return first

    return joined[:11]


async def send_sms_message(text: str, tool_context) -> dict:
    """Send an SMS message to the live caller's phone number."""
    from app.api.v1.at import providers
    from app.api.v1.at.service_sms import truncate_sms

    caller_phone = resolve_caller_phone_from_context(tool_context)
    if not caller_phone:
        return {"status": "error", "detail": "No caller phone in session"}

    if not text:
        return {"status": "error", "detail": "No text provided"}

    message = truncate_sms(text)
    sender_id = resolve_sms_sender_id_from_state(getattr(tool_context, "state", {}))

    try:
        provider_result = await providers.send_sms(
            message=message,
            recipients=[caller_phone],
            sender_id=sender_id or None,
        )
    except Exception:
        logger.warning("SMS send request failed", exc_info=True)
        return {"status": "error", "detail": "Request failed"}

    recipients = provider_result.get("SMSMessageData", {}).get("Recipients", [])
    recipient_status = ""
    if isinstance(recipients, list) and recipients:
        first = recipients[0]
        if isinstance(first, dict):
            raw_status = first.get("status")
            recipient_status = raw_status.strip() if isinstance(raw_status, str) else ""

    return {
        "status": "sent",
        "recipient": caller_phone,
        "sender_id": sender_id,
        "provider_status": recipient_status,
    }
