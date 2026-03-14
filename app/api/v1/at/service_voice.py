"""Voice channel business logic.

XML building, DID→tenant/company resolution, call lifecycle logging.
Routes delegate here — no business logic in voice.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time

from shared.callback_prewarm import (
    clear_callback_prewarm,
    get_callback_prewarm,
    request_callback_prewarm,
)
from shared.outbound_callback_hints import mark_outbound_callback_hint
from shared.phone_identity import normalize_phone

from .settings import (
    AT_CALLBACK_DIAL_FALLBACK,
    AT_VIRTUAL_NUMBER,
    AT_RECORDING_ENABLED,
    AT_RECORDING_DISCLOSURE,
    SIP_BRIDGE_ENDPOINT,
)
from . import providers

logger = logging.getLogger(__name__)

_CALLBACK_REQUESTS_LOCAL: dict[str, dict[str, object]] = {}
_CALLBACK_REQUESTS_LOCK = threading.Lock()
_FIRESTORE_CLIENT = None
_FIRESTORE_CLIENT_LOCK = threading.Lock()


def _read_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


_CALLBACK_PREWARM_TIMEOUT_SECONDS = max(
    1.0,
    _read_float_env("AT_CALLBACK_PREWARM_TIMEOUT_SECONDS", 12.0),
)
_CALLBACK_PREWARM_POLL_SECONDS = max(
    0.1,
    _read_float_env("AT_CALLBACK_PREWARM_POLL_SECONDS", 0.25),
)
_CALLBACK_ATTACH_TIMEOUT_SECONDS = max(
    1.0,
    _read_float_env("AT_CALLBACK_ATTACH_TIMEOUT_SECONDS", 6.0),
)
_CALLBACK_ATTACH_POLL_SECONDS = max(
    0.1,
    _read_float_env("AT_CALLBACK_ATTACH_POLL_SECONDS", 0.5),
)
_CALLBACK_ATTACH_RETRY_MAX = max(
    0,
    int(_read_float_env("AT_CALLBACK_ATTACH_RETRY_MAX", 1)),
)
_CALLBACK_OVERRIDE_SOURCES = frozenset({
    "manual_callback_request",
    "voice_ai_auto_callback",
    "voice_ai_request",
    "voice_agent_callback_promise",
    "voice_user_callback_intent",
})


def _callback_collection_name() -> str:
    return os.getenv("AT_CALLBACK_REQUEST_COLLECTION", "at_callback_requests")


def _callback_cooldown_seconds() -> float:
    raw = os.getenv("AT_CALLBACK_COOLDOWN_SECONDS", "1800")
    try:
        return max(60.0, float(raw))
    except (TypeError, ValueError):
        return 1800.0


def _flash_callback_enabled() -> bool:
    return os.getenv("AT_FLASH_CALLBACK_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _flash_callback_duration_seconds() -> float:
    raw = os.getenv("AT_FLASH_CALLBACK_MAX_DURATION_SECONDS", "8")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 8.0


def _uses_firestore() -> bool:
    if os.getenv("FIRESTORE_EMULATOR_HOST", "").strip():
        return True
    return bool(os.getenv("GOOGLE_CLOUD_PROJECT", "").strip())


def _get_firestore_client():
    global _FIRESTORE_CLIENT
    with _FIRESTORE_CLIENT_LOCK:
        if _FIRESTORE_CLIENT is None:
            from google.cloud import firestore

            project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
            _FIRESTORE_CLIENT = firestore.Client(project=project)
    return _FIRESTORE_CLIENT


def _normalized_phone(phone: str) -> str:
    return normalize_phone(phone) or phone.strip()


def _callback_key(tenant_id: str, company_id: str, phone: str) -> str:
    return f"{tenant_id}:{company_id}:{_normalized_phone(phone)}"


def _callback_doc_ref(key: str):
    client = _get_firestore_client()
    doc_id = hashlib.sha256(key.encode()).hexdigest()
    return client.collection(_callback_collection_name()).document(doc_id)


def _delete_callback_request(tenant_id: str, company_id: str, phone: str) -> None:
    key = _callback_key(tenant_id, company_id, phone)
    with _CALLBACK_REQUESTS_LOCK:
        _CALLBACK_REQUESTS_LOCAL.pop(key, None)

    if not _uses_firestore():
        return

    try:
        _callback_doc_ref(key).delete()
    except Exception:
        logger.warning("Callback request delete failed", exc_info=True)


def _load_callback_request(tenant_id: str, company_id: str, phone: str) -> dict[str, object] | None:
    key = _callback_key(tenant_id, company_id, phone)
    now = time.time()

    with _CALLBACK_REQUESTS_LOCK:
        local = _CALLBACK_REQUESTS_LOCAL.get(key)

    if _uses_firestore():
        try:
            snap = _callback_doc_ref(key).get()
        except Exception:
            logger.warning("Callback request read failed", exc_info=True)
        else:
            if not snap.exists:
                with _CALLBACK_REQUESTS_LOCK:
                    _CALLBACK_REQUESTS_LOCAL.pop(key, None)
                return None

            data = snap.to_dict() or {}
            if not isinstance(data, dict):
                return None

            cooldown_until = float(data.get("cooldown_until", 0.0) or 0.0)
            if cooldown_until and cooldown_until <= now and data.get("status") == "queued":
                logger.info(
                    "Expiring stale queued callback request tenant_id=%s company_id=%s phone=%s",
                    tenant_id,
                    company_id,
                    _normalized_phone(phone),
                )
                _delete_callback_request(tenant_id, company_id, phone)
                return None

            with _CALLBACK_REQUESTS_LOCK:
                _CALLBACK_REQUESTS_LOCAL[key] = dict(data)
            return data

    if not isinstance(local, dict):
        return None

    cooldown_until = float(local.get("cooldown_until", 0.0) or 0.0)
    if cooldown_until and cooldown_until <= now and local.get("status") == "queued":
        with _CALLBACK_REQUESTS_LOCK:
            _CALLBACK_REQUESTS_LOCAL.pop(key, None)
        return None
    return dict(local)


def _save_callback_request(
    tenant_id: str,
    company_id: str,
    phone: str,
    payload: dict[str, object],
) -> bool:
    return _save_callback_request_verified(tenant_id, company_id, phone, payload)


def _save_callback_request_verified(
    tenant_id: str,
    company_id: str,
    phone: str,
    payload: dict[str, object],
) -> bool:
    """Persist a callback request locally and in Firestore when configured."""
    key = _callback_key(tenant_id, company_id, phone)
    record = dict(payload)
    record["key"] = key

    with _CALLBACK_REQUESTS_LOCK:
        _CALLBACK_REQUESTS_LOCAL[key] = dict(record)

    if not _uses_firestore():
        return True

    try:
        _callback_doc_ref(key).set(record, merge=True)
    except Exception:
        logger.warning("Callback request write failed", exc_info=True)
        return False
    return True


def _can_override_callback_cooldown(
    *,
    existing: dict[str, object] | None,
    source: str,
    trigger_after_hangup: bool,
) -> bool:
    """Allow an explicit voice callback request to replace any cooled-down record.

    If the caller is actively asking for a callback again on a fresh voice call,
    we should honor that request immediately rather than suppressing it because a
    previous callback attempt recently happened.
    """
    if not trigger_after_hangup or source not in _CALLBACK_OVERRIDE_SOURCES:
        return False
    if not isinstance(existing, dict):
        return False
    return True


def register_callback_request(
    *,
    phone: str,
    tenant_id: str,
    company_id: str,
    source: str,
    reason: str = "",
    trigger_after_hangup: bool = True,
) -> dict[str, object]:
    """Persist a callback request for the caller's phone number."""
    normalized_phone = _normalized_phone(phone)
    if not normalized_phone:
        return {
            "status": "error",
            "error": "No callback phone",
            "detail": "No callback phone",
        }

    now = time.time()
    existing = _load_callback_request(tenant_id, company_id, normalized_phone)
    cooldown_until = float((existing or {}).get("cooldown_until", 0.0) or 0.0)
    if cooldown_until > now:
        if _can_override_callback_cooldown(
            existing=existing,
            source=source,
            trigger_after_hangup=trigger_after_hangup,
        ):
            logger.info(
                "Overriding queued callback cooldown tenant_id=%s company_id=%s "
                "phone=%s source=%s existing_source=%s cooldown_until=%s",
                tenant_id,
                company_id,
                normalized_phone,
                source,
                (existing or {}).get("source"),
                cooldown_until,
            )
            _delete_callback_request(tenant_id, company_id, normalized_phone)
        else:
            return {
                "status": "cooldown",
                "phone": normalized_phone,
                "cooldown_until": cooldown_until,
            }

    payload = {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "phone": normalized_phone,
        "source": source,
        "reason": reason.strip()[:240],
        "status": "pending",
        "trigger_after_hangup": bool(trigger_after_hangup),
        "requested_at": now,
        "updated_at": now,
        "cooldown_until": 0.0,
    }
    persisted = _save_callback_request_verified(
        tenant_id, company_id, normalized_phone, payload
    )
    if not persisted:
        logger.warning(
            "Callback request persistence failed tenant_id=%s company_id=%s phone=%s source=%s",
            tenant_id,
            company_id,
            normalized_phone,
            source,
        )
        return {
            "status": "error",
            "error": "Callback request unavailable",
            "detail": "Could not queue callback request",
            "phone": normalized_phone,
        }

    stored = _load_callback_request(tenant_id, company_id, normalized_phone)
    if not isinstance(stored, dict) or str(stored.get("status", "")).strip().lower() != "pending":
        logger.warning(
            "Callback request verification failed tenant_id=%s company_id=%s phone=%s stored=%r",
            tenant_id,
            company_id,
            normalized_phone,
            stored,
        )
        return {
            "status": "error",
            "error": "Callback request unavailable",
            "detail": "Could not verify callback request",
            "phone": normalized_phone,
        }

    logger.info(
        "Callback request queued tenant_id=%s company_id=%s phone=%s source=%s",
        tenant_id,
        company_id,
        normalized_phone,
        source,
    )
    return {"status": "pending", "phone": normalized_phone, "source": source}


async def _wait_for_callback_prewarm(
    *,
    phone: str,
    tenant_id: str,
    company_id: str,
) -> dict[str, object] | None:
    """Request a warm callback session on the VM and wait until it is ready."""
    await asyncio.to_thread(
        request_callback_prewarm,
        tenant_id=tenant_id,
        company_id=company_id,
        phone=phone,
    )
    deadline = time.monotonic() + _CALLBACK_PREWARM_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        payload = await asyncio.to_thread(
            get_callback_prewarm,
            tenant_id=tenant_id,
            company_id=company_id,
            phone=phone,
        )
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).strip().lower()
            if status in {"ready", "failed"}:
                return payload
        await asyncio.sleep(_CALLBACK_PREWARM_POLL_SECONDS)

    return await asyncio.to_thread(
        get_callback_prewarm,
        tenant_id=tenant_id,
        company_id=company_id,
        phone=phone,
    )


async def _wait_for_callback_attach(
    *,
    phone: str,
    tenant_id: str,
    company_id: str,
    timeout_seconds: float | None = None,
) -> bool:
    """Wait for the VM to attach the prewarmed callback session to a real leg.

    A successful attach clears the prewarm reservation, so the reservation
    disappearing is the durable signal that the outbound callback leg really
    reached the SIP bridge.
    """
    deadline = time.monotonic() + (timeout_seconds or _CALLBACK_ATTACH_TIMEOUT_SECONDS)
    while time.monotonic() < deadline:
        payload = await asyncio.to_thread(
            get_callback_prewarm,
            tenant_id=tenant_id,
            company_id=company_id,
            phone=phone,
        )
        if payload is None:
            return True
        status = str(payload.get("status", "")).strip().lower() if isinstance(payload, dict) else ""
        if status == "failed":
            return False
        await asyncio.sleep(_CALLBACK_ATTACH_POLL_SECONDS)
    return False


async def trigger_callback(
    *,
    phone: str,
    tenant_id: str,
    company_id: str,
    source: str,
    reason: str = "",
) -> dict[str, object]:
    """Place an outbound callback through Africa's Talking."""
    normalized_phone = _normalized_phone(phone)
    if not normalized_phone:
        return {"status": "error", "detail": "No callback phone"}

    cooldown_until = time.time() + _callback_cooldown_seconds()
    logger.info(
        "trigger_callback starting prewarm wait phone=%s tenant_id=%s company_id=%s",
        normalized_phone,
        tenant_id,
        company_id,
    )
    prewarm_payload = await _wait_for_callback_prewarm(
        phone=normalized_phone,
        tenant_id=tenant_id,
        company_id=company_id,
    )
    prewarm_status = (
        str(prewarm_payload.get("status", "")).strip().lower()
        if isinstance(prewarm_payload, dict)
        else ""
    )
    logger.info(
        "trigger_callback prewarm result phone=%s status=%s",
        normalized_phone,
        prewarm_status or "(none/empty)",
    )
    if prewarm_status != "ready":
        detail = "Callback voice session unavailable"
        if isinstance(prewarm_payload, dict):
            detail = str(prewarm_payload.get("detail", "")).strip() or detail
        logger.warning(
            "Callback prewarm not ready phone=%s prewarm_status=%s detail=%s "
            "prewarm_payload=%r",
            normalized_phone,
            prewarm_status or "(empty)",
            detail,
            prewarm_payload,
        )
        await asyncio.to_thread(
            clear_callback_prewarm,
            tenant_id=tenant_id,
            company_id=company_id,
            phone=normalized_phone,
        )
        payload = {
            "tenant_id": tenant_id,
            "company_id": company_id,
            "phone": normalized_phone,
            "source": source,
            "reason": reason.strip()[:240],
            "status": "failed",
            "updated_at": time.time(),
            "cooldown_until": cooldown_until,
            "detail": detail,
        }
        _save_callback_request(tenant_id, company_id, normalized_phone, payload)
        return {"status": "error", "detail": detail, "phone": normalized_phone}

    mark_outbound_callback_hint(
        tenant_id=tenant_id,
        company_id=company_id,
        phone=normalized_phone,
    )

    async def _place_outbound_callback() -> dict[str, object]:
        return await providers.make_call(from_=AT_VIRTUAL_NUMBER, to=[normalized_phone])

    provider_result: dict[str, object] | None = None
    attach_confirmed = False
    for attempt in range(_CALLBACK_ATTACH_RETRY_MAX + 1):
        if attempt > 0:
            logger.warning(
                "Retrying outbound callback placement phone=%s attempt=%d",
                normalized_phone,
                attempt + 1,
            )
            mark_outbound_callback_hint(
                tenant_id=tenant_id,
                company_id=company_id,
                phone=normalized_phone,
            )
        try:
            provider_result = await _place_outbound_callback()
        except Exception:
            logger.warning("Outbound callback placement failed", exc_info=True)
            await asyncio.to_thread(
                clear_callback_prewarm,
                tenant_id=tenant_id,
                company_id=company_id,
                phone=normalized_phone,
            )
            payload = {
                "tenant_id": tenant_id,
                "company_id": company_id,
                "phone": normalized_phone,
                "source": source,
                "reason": reason.strip()[:240],
                "status": "failed",
                "updated_at": time.time(),
                "cooldown_until": cooldown_until,
            }
            _save_callback_request(tenant_id, company_id, normalized_phone, payload)
            return {"status": "error", "detail": "Voice provider unavailable", "phone": normalized_phone}

        logger.info(
            "Outbound callback provider queued phone=%s attempt=%d result=%r",
            normalized_phone,
            attempt + 1,
            provider_result,
        )
        attach_confirmed = await _wait_for_callback_attach(
            phone=normalized_phone,
            tenant_id=tenant_id,
            company_id=company_id,
        )
        if attach_confirmed:
            break

    if not attach_confirmed:
        logger.warning(
            "Outbound callback leg did not attach after provider acceptance phone=%s attempts=%d",
            normalized_phone,
            _CALLBACK_ATTACH_RETRY_MAX + 1,
        )
        await asyncio.to_thread(
            clear_callback_prewarm,
            tenant_id=tenant_id,
            company_id=company_id,
            phone=normalized_phone,
        )
        payload = {
            "tenant_id": tenant_id,
            "company_id": company_id,
            "phone": normalized_phone,
            "source": source,
            "reason": reason.strip()[:240],
            "status": "failed",
            "updated_at": time.time(),
            "cooldown_until": cooldown_until,
            "detail": "Callback leg did not attach",
            "provider_result": provider_result or {},
        }
        _save_callback_request(tenant_id, company_id, normalized_phone, payload)
        return {
            "status": "error",
            "detail": "Callback leg did not attach",
            "phone": normalized_phone,
        }

    payload = {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "phone": normalized_phone,
        "source": source,
        "reason": reason.strip()[:240],
        "status": "queued",
        "updated_at": time.time(),
        "cooldown_until": cooldown_until,
        "provider_result": provider_result,
    }
    _save_callback_request(tenant_id, company_id, normalized_phone, payload)
    try:
        from app.api.v1.at import voice_analytics

        voice_analytics.mark_callback_triggered(
            tenant_id=tenant_id,
            company_id=company_id,
            phone=normalized_phone,
        )
    except Exception:
        logger.debug("Voice analytics callback trigger skipped", exc_info=True)
    return {"status": "queued", "phone": normalized_phone, "result": provider_result}


async def maybe_trigger_post_call_callback(
    *,
    session_id: str,
    caller_phone: str,
    direction: str,
    duration_seconds: str,
    tenant_id: str,
    company_id: str,
) -> None:
    """Trigger callback after hangup when requested or when a flash is detected."""
    normalized_phone = _normalized_phone(caller_phone)
    if not normalized_phone:
        logger.info("Skipping post-call callback check: no normalized caller phone")
        return

    if direction.strip().lower() == "outbound":
        logger.info(
            "Skipping post-call callback check for outbound leg phone=%s",
            normalized_phone,
        )
        return

    existing = _load_callback_request(tenant_id, company_id, normalized_phone)
    logger.info(
        "Post-call callback check phone=%s existing_status=%s direction=%s duration_seconds=%s",
        normalized_phone,
        (existing or {}).get("status") if isinstance(existing, dict) else None,
        direction,
        duration_seconds,
    )
    if isinstance(existing, dict) and existing.get("status") == "pending":
        logger.info(
            "Triggering pending callback after hangup phone=%s source=%s",
            normalized_phone,
            existing.get("source", "callback_request"),
        )
        await trigger_callback(
            phone=normalized_phone,
            tenant_id=tenant_id,
            company_id=company_id,
            source=str(existing.get("source", "callback_request")),
            reason=str(existing.get("reason", "")),
        )
        return

    if not (_flash_callback_enabled() and AT_CALLBACK_DIAL_FALLBACK):
        logger.info(
            "Skipping flash callback fallback phone=%s flash_enabled=%s dial_fallback=%s",
            normalized_phone,
            _flash_callback_enabled(),
            AT_CALLBACK_DIAL_FALLBACK,
        )
        return

    try:
        duration = float(duration_seconds or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    if duration > _flash_callback_duration_seconds():
        logger.info(
            "Skipping flash callback fallback phone=%s duration=%.2fs threshold=%.2fs",
            normalized_phone,
            duration,
            _flash_callback_duration_seconds(),
        )
        return

    try:
        from app.api.v1.at import voice_analytics

        session_snapshot = voice_analytics.get_session_snapshot(session_id)
    except Exception:
        logger.debug("Flash callback analytics lookup skipped", exc_info=True)
        session_snapshot = None

    if isinstance(session_snapshot, dict):
        transcript_messages_total = _safe_int(session_snapshot.get("transcript_messages_total", 0))
        transfer_count = _safe_int(session_snapshot.get("transfer_count", 0))
        if transcript_messages_total > 0 or transfer_count > 0:
            logger.info(
                "Skipping flash callback fallback phone=%s session_id=%s transcripts=%d transfers=%d",
                normalized_phone,
                session_id,
                transcript_messages_total,
                transfer_count,
            )
            return

    logger.info("Triggering flash callback fallback phone=%s", normalized_phone)
    await trigger_callback(
        phone=normalized_phone,
        tenant_id=tenant_id,
        company_id=company_id,
        source="flash_callback",
        reason="Short inbound call requested callback",
    )


def resolve_tenant_context(destination_number: str) -> tuple[str, str]:
    """Resolve tenant_id and company_id from the called virtual number.

    For now, returns defaults. In production, this will look up a
    DID→tenant/company mapping table.
    """
    # TODO: DID mapping table (Phase 2 production)
    return "public", "ekaette-electronics"


def build_dial_xml(sip_endpoint: str, caller_id: str) -> str:
    """Build AT XML to bridge caller to SIP-to-AI server.

    When SIP endpoint is not configured, returns a <Say> greeting fallback.
    When recording is enabled, prepends a <Say> disclosure per data governance.
    """
    if not sip_endpoint:
        return build_say_fallback_xml()

    record_attr = 'record="true"' if AT_RECORDING_ENABLED else 'record="false"'
    disclosure = ""
    if AT_RECORDING_ENABLED and AT_RECORDING_DISCLOSURE:
        disclosure = f'    <Say>{AT_RECORDING_DISCLOSURE}</Say>\n'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f"{disclosure}"
        f'    <Dial phoneNumbers="{sip_endpoint}" '
        f'{record_attr} sequential="true" '
        f'callerId="{caller_id}"/>\n'
        "</Response>"
    )


def build_say_fallback_xml() -> str:
    """Build AT XML greeting when SIP bridge is not yet configured."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "    <Say>Hello, thank you for calling Ekaette. "
        "Our AI voice assistant is being configured. "
        "Please try again shortly or visit our website for support. Goodbye.</Say>\n"
        "</Response>"
    )


def build_end_xml() -> str:
    """Build empty AT XML response for ended calls."""
    return "<Response/>"


def log_call_bridged(session_id: str, caller: str, direction: str) -> None:
    """Structured log for call bridge initiation."""
    _ = session_id, caller, direction
    tenant_id, company_id = resolve_tenant_context(AT_VIRTUAL_NUMBER)
    logger.info(
        "AT call bridged",
        extra={
            "tenant_id": tenant_id,
            "company_id": company_id,
            "sip_endpoint": SIP_BRIDGE_ENDPOINT,
        },
    )


def log_call_ended(
    session_id: str,
    caller: str,
    duration_seconds: str,
    amount: str,
) -> None:
    """Structured log for call completion."""
    _ = session_id, caller, duration_seconds, amount
    logger.info("AT call ended")
