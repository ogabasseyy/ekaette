"""Shared callback prewarm reservations for AT outbound callback legs.

Cloud Run requests a short-lived prewarm before placing an outbound callback.
The SIP bridge VM watches for pending reservations, starts the gateway/model
session while the phone is still ringing, and marks the reservation ready once
the first outbound audio is buffered.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

from shared.phone_identity import normalize_phone

logger = logging.getLogger(__name__)

_LOCAL_RESERVATIONS: dict[str, dict[str, object]] = {}
_LOCAL_LOCK = threading.Lock()
_FIRESTORE_CLIENT = None
_FIRESTORE_CLIENT_LOCK = threading.Lock()
_DEFAULT_PREWARM_TTL_SECONDS = 45.0


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


def _collection_name() -> str:
    return os.getenv("AT_CALLBACK_PREWARM_COLLECTION", "at_callback_prewarms")


def _prewarm_ttl_seconds() -> float:
    raw = os.getenv("AT_CALLBACK_PREWARM_TTL_SECONDS", str(_DEFAULT_PREWARM_TTL_SECONDS))
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_PREWARM_TTL_SECONDS


def _reservation_key(tenant_id: str, company_id: str, phone: str) -> str:
    normalized_phone = normalize_phone(phone) or phone.strip()
    return f"{tenant_id}:{company_id}:{normalized_phone}"


def _reservation_doc_ref(key: str):
    client = _get_firestore_client()
    doc_id = hashlib.sha256(key.encode()).hexdigest()
    return client.collection(_collection_name()).document(doc_id)


def request_callback_prewarm(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
    ttl_seconds: float | None = None,
) -> dict[str, object]:
    """Create or refresh a pending callback prewarm reservation."""
    normalized_phone = normalize_phone(phone) or phone.strip()
    if not normalized_phone:
        return {"status": "error", "detail": "No phone"}

    now = time.time()
    expires_at = now + (ttl_seconds or _prewarm_ttl_seconds())
    key = _reservation_key(tenant_id, company_id, normalized_phone)
    payload = {
        "key": key,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "phone": normalized_phone,
        "status": "pending",
        "requested_at": now,
        "updated_at": now,
        "expires_at": expires_at,
    }

    with _LOCAL_LOCK:
        _LOCAL_RESERVATIONS[key] = dict(payload)

    if _uses_firestore():
        try:
            _reservation_doc_ref(key).set(payload, merge=True)
        except Exception:
            logger.warning("Failed to persist callback prewarm reservation", exc_info=True)

    return dict(payload)


def get_callback_prewarm(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
) -> dict[str, object] | None:
    """Fetch a single callback prewarm reservation by tenant/company/phone."""
    key = _reservation_key(tenant_id, company_id, phone)
    now = time.time()
    if _uses_firestore():
        try:
            snap = _reservation_doc_ref(key).get()
        except Exception:
            logger.warning("Failed to read callback prewarm reservation", exc_info=True)
        else:
            if snap.exists:
                data = snap.to_dict() or {}
                if isinstance(data, dict):
                    expires_at = float(data.get("expires_at", 0.0) or 0.0)
                    if expires_at and expires_at <= now:
                        try:
                            _reservation_doc_ref(key).delete()
                        except Exception:
                            logger.debug(
                                "Failed to prune expired callback prewarm reservation",
                                exc_info=True,
                            )
                        with _LOCAL_LOCK:
                            _LOCAL_RESERVATIONS.pop(key, None)
                        return None

                    with _LOCAL_LOCK:
                        _LOCAL_RESERVATIONS[key] = dict(data)
                    return data

            with _LOCAL_LOCK:
                _LOCAL_RESERVATIONS.pop(key, None)
            return None

    with _LOCAL_LOCK:
        local = _LOCAL_RESERVATIONS.get(key)
        if not isinstance(local, dict):
            return None
        expires_at = float(local.get("expires_at", 0.0) or 0.0)
        if expires_at and expires_at <= now:
            _LOCAL_RESERVATIONS.pop(key, None)
            return None
        return dict(local)


def list_callback_prewarms() -> list[dict[str, object]]:
    """List currently active callback prewarm reservations."""
    now = time.time()
    results: dict[str, dict[str, object]] = {}

    with _LOCAL_LOCK:
        expired = [
            key for key, value in _LOCAL_RESERVATIONS.items()
            if float(value.get("expires_at", 0.0) or 0.0) <= now
        ]
        for key in expired:
            _LOCAL_RESERVATIONS.pop(key, None)
        for key, value in _LOCAL_RESERVATIONS.items():
            results[key] = dict(value)

    if not _uses_firestore():
        return list(results.values())

    try:
        docs = list(_get_firestore_client().collection(_collection_name()).stream())
    except Exception:
        logger.warning("Failed to list callback prewarm reservations", exc_info=True)
        return list(results.values())

    for snap in docs:
        data = snap.to_dict() or {}
        if not isinstance(data, dict):
            continue
        expires_at = float(data.get("expires_at", 0.0) or 0.0)
        if expires_at and expires_at <= now:
            try:
                snap.reference.delete()
            except Exception:
                logger.debug("Failed to delete expired callback prewarm reservation", exc_info=True)
            continue
        key = str(data.get("key", "")).strip()
        if not key:
            continue
        results[key] = data
        with _LOCAL_LOCK:
            _LOCAL_RESERVATIONS[key] = dict(data)

    return list(results.values())


def update_callback_prewarm_status(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
    status: str,
    detail: str = "",
) -> None:
    """Update a callback prewarm reservation status."""
    key = _reservation_key(tenant_id, company_id, phone)
    now = time.time()

    payload = get_callback_prewarm(
        tenant_id=tenant_id,
        company_id=company_id,
        phone=phone,
    ) or {
        "key": key,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "phone": normalize_phone(phone) or phone.strip(),
        "requested_at": now,
        "expires_at": now + _prewarm_ttl_seconds(),
    }
    payload["status"] = status
    payload["updated_at"] = now
    if detail:
        payload["detail"] = detail[:240]

    with _LOCAL_LOCK:
        _LOCAL_RESERVATIONS[key] = dict(payload)

    if not _uses_firestore():
        return

    try:
        _reservation_doc_ref(key).set(payload, merge=True)
    except Exception:
        logger.warning("Failed to update callback prewarm reservation", exc_info=True)


def clear_callback_prewarm(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
) -> None:
    """Delete a callback prewarm reservation."""
    key = _reservation_key(tenant_id, company_id, phone)
    with _LOCAL_LOCK:
        _LOCAL_RESERVATIONS.pop(key, None)

    if not _uses_firestore():
        return

    try:
        _reservation_doc_ref(key).delete()
    except Exception:
        logger.debug("Failed to clear callback prewarm reservation", exc_info=True)


def reset_callback_prewarms() -> None:
    """Clear local prewarm reservations for tests."""
    with _LOCAL_LOCK:
        _LOCAL_RESERVATIONS.clear()
