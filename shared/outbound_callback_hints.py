"""Shared outbound-callback hints for fast-answering AT callback legs.

Cloud Run marks a short-lived hint when an Africa's Talking outbound callback
is about to bridge into the SIP VM. The VM consumes that hint so it can skip
pre-answer greeting buffering for that specific callback leg.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

from shared.phone_identity import normalize_phone

logger = logging.getLogger(__name__)

_LOCAL_HINTS: dict[str, float] = {}
_LOCAL_HINTS_LOCK = threading.Lock()
_FIRESTORE_CLIENT = None
_FIRESTORE_CLIENT_LOCK = threading.Lock()
_DEFAULT_HINT_TTL_SECONDS = 90.0


def _hint_ttl_seconds() -> float:
    raw = os.getenv("AT_OUTBOUND_CALLBACK_HINT_TTL_SECONDS", str(_DEFAULT_HINT_TTL_SECONDS))
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_HINT_TTL_SECONDS


def _uses_firestore() -> bool:
    if os.getenv("FIRESTORE_EMULATOR_HOST", "").strip():
        return True
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    return bool(project)


def _get_firestore_client():
    global _FIRESTORE_CLIENT
    with _FIRESTORE_CLIENT_LOCK:
        if _FIRESTORE_CLIENT is None:
            from google.cloud import firestore

            project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
            _FIRESTORE_CLIENT = firestore.Client(project=project)
    return _FIRESTORE_CLIENT


def _collection_name() -> str:
    return os.getenv("AT_OUTBOUND_CALLBACK_HINT_COLLECTION", "at_outbound_callback_hints")


def _hint_key(tenant_id: str, company_id: str, phone: str) -> str:
    normalized_phone = normalize_phone(phone) or phone.strip()
    return f"{tenant_id}:{company_id}:{normalized_phone}"


def _hint_doc_ref(key: str):
    client = _get_firestore_client()
    doc_id = hashlib.sha256(key.encode()).hexdigest()
    return client.collection(_collection_name()).document(doc_id)


def mark_outbound_callback_hint(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
    ttl_seconds: float | None = None,
) -> None:
    """Write a short-lived hint that an outbound callback leg is imminent."""
    key = _hint_key(tenant_id, company_id, phone)
    now = time.time()
    expires_at = now + (ttl_seconds or _hint_ttl_seconds())

    with _LOCAL_HINTS_LOCK:
        _LOCAL_HINTS[key] = expires_at

    if not _uses_firestore():
        return

    payload = {
        "key": key,
        "updated_at": now,
        "expires_at": expires_at,
    }
    try:
        _hint_doc_ref(key).set(payload, merge=True)
    except Exception:
        logger.warning("Failed to persist outbound callback hint", exc_info=True)


def consume_outbound_callback_hint(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
) -> bool:
    """Return True once when a recent outbound callback hint exists."""
    key = _hint_key(tenant_id, company_id, phone)
    now = time.time()

    with _LOCAL_HINTS_LOCK:
        expires_at = _LOCAL_HINTS.get(key, 0.0)
        if expires_at > now:
            _LOCAL_HINTS.pop(key, None)
            return True
        if key in _LOCAL_HINTS:
            _LOCAL_HINTS.pop(key, None)

    if not _uses_firestore():
        return False

    try:
        doc_ref = _hint_doc_ref(key)
        snap = doc_ref.get()
    except Exception:
        logger.warning("Failed to read outbound callback hint", exc_info=True)
        return False

    if not snap.exists:
        return False

    data = snap.to_dict() or {}
    expires_at = float(data.get("expires_at", 0.0) or 0.0)
    if expires_at <= now:
        try:
            doc_ref.delete()
        except Exception:
            logger.debug("Failed to prune expired outbound callback hint", exc_info=True)
        return False

    try:
        doc_ref.delete()
    except Exception:
        logger.debug("Failed to consume outbound callback hint", exc_info=True)
    return True


def reset_outbound_callback_hints() -> None:
    """Clear local hint cache for tests."""
    with _LOCAL_HINTS_LOCK:
        _LOCAL_HINTS.clear()
