"""Cross-channel voice -> WhatsApp handoff helpers.

Durable handoff context lives in Firestore so that a voice session can ask for
media on WhatsApp and the later WhatsApp media session can continue with the
same business context even though it runs under a different ADK app namespace.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.api.v1.admin.firestore_helpers import _doc_get, _doc_set, _doc_update
from app.tools.scoped_queries import tenant_company_collection
from app.tools.sms_messaging import resolve_caller_phone_from_context
from app.tools.wa_messaging import send_whatsapp_message
from shared.phone_identity import normalize_phone

logger = logging.getLogger(__name__)

_FIRESTORE_DB = None
_FIRESTORE_DB_LOCK = threading.Lock()
_CROSS_CHANNEL_CONTEXT_COLLECTION = "cross_channel_context"


def _context_ttl_seconds() -> int:
    raw = os.getenv("CROSS_CHANNEL_CONTEXT_TTL_SECONDS", "1800")
    try:
        return max(300, int(raw))
    except (TypeError, ValueError):
        return 1800


def _get_firestore_db() -> Any | None:
    global _FIRESTORE_DB
    if _FIRESTORE_DB is not None:
        return _FIRESTORE_DB

    with _FIRESTORE_DB_LOCK:
        if _FIRESTORE_DB is not None:
            return _FIRESTORE_DB
        try:
            from google.cloud import firestore

            project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
            _FIRESTORE_DB = firestore.Client(project=project)
        except Exception as exc:
            logger.warning("Cross-channel Firestore client unavailable: %s", exc)
            _FIRESTORE_DB = None
    return _FIRESTORE_DB


def _normalized_phone(phone: str) -> str:
    normalized = normalize_phone(phone)
    if normalized:
        return normalized
    return phone.strip()


def _context_doc_id(tenant_id: str, company_id: str, phone: str) -> str:
    normalized_phone = _normalized_phone(phone)
    return hashlib.sha256(
        f"{tenant_id}:{company_id}:{normalized_phone}".encode()
    ).hexdigest()[:32]


def _context_doc_ref(db: Any, tenant_id: str, company_id: str, phone: str) -> Any | None:
    collection = tenant_company_collection(
        db,
        tenant_id,
        company_id,
        _CROSS_CHANNEL_CONTEXT_COLLECTION,
    )
    if collection is None:
        return None
    return collection.document(_context_doc_id(tenant_id, company_id, phone))


def _normalize_summary(summary: str) -> str:
    normalized = " ".join((summary or "").split())
    return normalized[:480]


def _media_request_message(summary: str) -> str:
    base = (
        "Please send a clear photo or short video of your device here on WhatsApp. "
        "I already have the context from our call, so you do not need to repeat yourself."
    )
    if not summary:
        return base
    return f"{base}\n\nContext: {summary}"


def _extract_snapshot_data(snapshot: Any) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    exists = getattr(snapshot, "exists", False)
    if exists is False:
        return None
    to_dict = getattr(snapshot, "to_dict", None)
    if not callable(to_dict):
        return None
    data = to_dict() or {}
    return data if isinstance(data, dict) else None


def _validate_pending_context(data: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    status = str(data.get("status", "") or "").strip().lower()
    if status != "pending":
        return None, None

    created_at = data.get("created_at")
    try:
        created_ts = float(created_at)
    except (TypeError, ValueError):
        return None, None

    now = time.time()
    if created_ts + float(_context_ttl_seconds()) <= now:
        return None, "expired"

    summary = str(data.get("conversation_summary", "") or "").strip()
    if not summary:
        return None, None

    return data, None


def _get_state_value(state: Any, key: str, default: str) -> str:
    getter = getattr(state, "get", None)
    if callable(getter):
        return str(getter(key, default) or default).strip()
    return default


async def request_media_via_whatsapp(
    reason: str,
    summary: str,
    tool_context=None,
) -> dict[str, Any]:
    """Persist media handoff context and prompt the caller on WhatsApp.

    2026 best-practice choice in this codebase:
    - durable Firestore handoff record is the source of truth
    - ADK session/user state is only a local hint
    """
    state = getattr(tool_context, "state", {})
    caller_phone = resolve_caller_phone_from_context(tool_context)
    if not caller_phone:
        return {"status": "error", "detail": "No caller phone in session"}

    normalized_summary = _normalize_summary(summary)
    if not normalized_summary:
        return {"status": "error", "detail": "No conversation summary provided"}

    tenant_id = _get_state_value(state, "app:tenant_id", "public")
    company_id = _get_state_value(state, "app:company_id", "ekaette-electronics")
    voice_session_id = _get_state_value(state, "app:session_id", "")
    voice_user_id = _get_state_value(state, "app:user_id", "")
    db = _get_firestore_db()
    if db is None:
        return {"status": "error", "detail": "Cross-channel context store unavailable"}

    doc_ref = _context_doc_ref(db, tenant_id, company_id, caller_phone)
    if doc_ref is None:
        return {"status": "error", "detail": "Cross-channel context scope unavailable"}

    created_at = time.time()
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "phone": _normalized_phone(caller_phone),
        "voice_session_id": voice_session_id,
        "voice_user_id": voice_user_id,
        "pending_reason": str(reason or "").strip(),
        "conversation_summary": normalized_summary,
        "status": "pending",
        "created_at": created_at,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=_context_ttl_seconds()),
    }

    try:
        await _doc_set(doc_ref, payload, merge=True)
    except Exception:
        logger.warning("Cross-channel context write failed", exc_info=True)
        return {"status": "error", "detail": "Failed to persist media handoff context"}

    try:
        if hasattr(state, "__setitem__"):
            state["temp:cross_channel_media_request_pending"] = True
            state["temp:cross_channel_media_request_reason"] = payload["pending_reason"]
            state["temp:cross_channel_media_request_doc_id"] = getattr(doc_ref, "id", "")
    except Exception:
        logger.debug("Cross-channel state hint update skipped", exc_info=True)

    wa_result = await send_whatsapp_message(_media_request_message(normalized_summary), tool_context)
    if str(wa_result.get("status", "")).strip().lower() != "sent":
        try:
            await _doc_update(
                doc_ref,
                {
                    "delivery_status": "failed",
                    "delivery_error": str(wa_result.get("detail", "") or "send_failed"),
                },
            )
        except Exception:
            logger.debug("Cross-channel delivery failure update skipped", exc_info=True)
        return {
            "status": "error",
            "detail": str(wa_result.get("detail", "") or "Failed to send WhatsApp prompt"),
        }

    try:
        await _doc_update(
            doc_ref,
            {
                "delivery_status": "sent",
                "delivery_message_id": str(wa_result.get("message_id", "") or ""),
            },
        )
    except Exception:
        logger.debug("Cross-channel delivery success update skipped", exc_info=True)

    return {
        "status": "sent",
        "phone": _normalized_phone(caller_phone),
        "expires_in_minutes": _context_ttl_seconds() // 60,
        "context_id": getattr(doc_ref, "id", ""),
        "message_id": str(wa_result.get("message_id", "") or ""),
    }


def _consume_pending_context_sync(db: Any, tenant_id: str, company_id: str, phone: str) -> dict[str, Any] | None:
    doc_ref = _context_doc_ref(db, tenant_id, company_id, phone)
    if doc_ref is None:
        return None

    def _fallback_consume() -> dict[str, Any] | None:
        logger.warning(
            "Using non-transactional fallback for cross-channel consume; "
            "race conditions possible under high concurrency"
        )
        snapshot = doc_ref.get()
        data = _extract_snapshot_data(snapshot)
        if data is None:
            return None
        valid_data, terminal_status = _validate_pending_context(data)
        if valid_data is None:
            if terminal_status == "expired":
                try:
                    doc_ref.update({"status": "expired", "expired_at": time.time()})
                except Exception:
                    logger.debug("Cross-channel expiry update skipped", exc_info=True)
            return None
        consumed_at = time.time()
        try:
            doc_ref.update({"status": "consumed", "consumed_at": consumed_at})
        except Exception:
            logger.warning("Cross-channel consume update failed", exc_info=True)
            return None
        valid_data["status"] = "consumed"
        valid_data["consumed_at"] = consumed_at
        return valid_data

    try:
        from google.cloud import firestore

        if hasattr(db, "transaction"):
            transaction = db.transaction()

            @firestore.transactional
            def _consume_in_tx(tx):
                snapshot = doc_ref.get(transaction=tx)
                data = _extract_snapshot_data(snapshot)
                if data is None:
                    return None
                valid_data, terminal_status = _validate_pending_context(data)
                if valid_data is None:
                    if terminal_status == "expired":
                        tx.update(doc_ref, {"status": "expired", "expired_at": time.time()})
                    return None
                consumed_at = time.time()
                tx.update(doc_ref, {"status": "consumed", "consumed_at": consumed_at})
                valid_data["status"] = "consumed"
                valid_data["consumed_at"] = consumed_at
                return valid_data

            return _consume_in_tx(transaction)
    except Exception:
        logger.debug("Cross-channel transactional consume unavailable", exc_info=True)

    return _fallback_consume()


async def load_and_consume_cross_channel_context(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
) -> dict[str, Any] | None:
    """Load one pending cross-channel handoff record and mark it consumed.

    This must be used by the WhatsApp side instead of assuming any voice-side
    ADK state was auto-inherited into the `_text` app namespace.
    """
    db = _get_firestore_db()
    if db is None:
        return None
    try:
        return await asyncio.to_thread(
            _consume_pending_context_sync,
            db,
            tenant_id,
            company_id,
            phone,
        )
    except Exception:
        logger.warning("Cross-channel context load failed", exc_info=True)
        return None
