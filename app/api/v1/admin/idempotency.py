"""Idempotency subsystem — memory and Firestore-backed request deduplication.

Extracted from main.py as Phase A3 of modularization. Zero behavior changes.

Constants (IDEMPOTENCY_TTL_SECONDS, etc.) and mutable state (_idempotency_store)
stay in main.py so test monkeypatching works.  Functions here access them
through `_m.CONSTANT` at call time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from google.api_core.exceptions import AlreadyExists

logger = logging.getLogger(__name__)

from app.api.v1.admin.runtime import runtime as _m

# Firestore helpers (extracted in Phase A2)
from app.api.v1.admin.firestore_helpers import _doc_create, _doc_get, _doc_set


def _idempotency_fingerprint(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_prune(now_epoch: float) -> None:
    expired_keys = [
        key
        for key, entry in _m._idempotency_store.items()
        if float(entry.get("expires_at", 0)) <= now_epoch
    ]
    for key in expired_keys:
        _m._idempotency_store.pop(key, None)


def _idempotency_doc_ref(db: object, *, scope: str, tenant_id: str, idempotency_key: str):
    store_key = f"{scope}:{tenant_id}:{idempotency_key.strip()}"
    doc_id = hashlib.sha256(store_key.encode("utf-8")).hexdigest()
    return (
        db.collection("_runtime")
        .document("idempotency")
        .collection("entries")
        .document(doc_id)
    )


def _idempotency_uses_firestore() -> bool:
    return _m.IDEMPOTENCY_STORE_BACKEND == "firestore"


def _idempotency_json_response_from_cached(entry: dict[str, object]) -> JSONResponse:
    cached_body = entry.get("body")
    cached_status = entry.get("status_code")
    return JSONResponse(
        status_code=int(cached_status) if isinstance(cached_status, int) else 200,
        content=cached_body if isinstance(cached_body, dict) else {},
        headers={"Idempotency-Replayed": "true"},
    )


async def _idempotency_firestore_begin_with_key(
    *,
    scope: str,
    tenant_id: str,
    idempotency_key: str,
    payload: object,
) -> tuple[str | None, str | None, JSONResponse | None]:
    db = _m._registry_db_client()
    if db is None:
        logger.warning("Idempotency configured for firestore, but storage client is unavailable.")
        return _idempotency_memory_begin_with_key(
            scope=scope,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    request_fingerprint = _idempotency_fingerprint(payload)
    now_epoch = time.time()
    doc_ref = _idempotency_doc_ref(
        db,
        scope=scope,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
    )

    pending_record = {
        "scope": scope,
        "tenant_id": tenant_id,
        "idempotency_key": idempotency_key.strip(),
        "fingerprint": request_fingerprint,
        "state": "pending",
        "status_code": 0,
        "body": {},
        "created_at_epoch": now_epoch,
        "updated_at_epoch": now_epoch,
        "expires_at_epoch": now_epoch + _m.IDEMPOTENCY_PENDING_TTL_SECONDS,
    }
    try:
        await _doc_create(doc_ref, pending_record)
        return idempotency_key, request_fingerprint, None
    except AlreadyExists:
        pass
    except Exception as exc:
        logger.warning("Firestore idempotency claim failed (fallback to memory): %s", exc)
        return _idempotency_memory_begin_with_key(
            scope=scope,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    try:
        snapshot = await _doc_get(doc_ref)
        entry = snapshot.to_dict() if getattr(snapshot, "exists", False) else {}
    except Exception as exc:
        logger.warning("Firestore idempotency read failed (fallback to memory): %s", exc)
        return _idempotency_memory_begin_with_key(
            scope=scope,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    if not isinstance(entry, dict):
        entry = {}

    if entry.get("fingerprint") != request_fingerprint:
        return (
            None,
            None,
            JSONResponse(
                status_code=409,
                content={
                    "error": "Idempotency key already used with different payload",
                    "code": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
                },
            ),
        )

    state = str(entry.get("state") or "pending").strip().lower()
    expires_at = float(entry.get("expires_at_epoch", 0) or 0)

    if state == "done" and expires_at > now_epoch:
        return None, None, _idempotency_json_response_from_cached(entry)
    if state == "done":
        return (
            None,
            None,
            JSONResponse(
                status_code=409,
                content={
                    "error": "Idempotency key expired; retry with a new key",
                    "code": "IDEMPOTENCY_KEY_EXPIRED",
                },
            ),
        )
    if expires_at <= now_epoch:
        return (
            None,
            None,
            JSONResponse(
                status_code=409,
                content={
                    "error": "Idempotency key is stale and no longer claimable",
                    "code": "IDEMPOTENCY_KEY_STALE_PENDING",
                },
            ),
        )
    return (
        None,
        None,
        JSONResponse(
            status_code=409,
            content={
                "error": "Idempotency request with same key is still in progress",
                "code": "IDEMPOTENCY_KEY_IN_PROGRESS",
            },
        ),
    )


def _idempotency_key_dependency(request: Request) -> str | JSONResponse:
    """Extract Idempotency-Key from request headers for mutating admin routes."""
    idempotency_key = (request.headers.get("idempotency-key") or "").strip()
    if not idempotency_key:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Missing required header: Idempotency-Key",
                "code": "IDEMPOTENCY_KEY_REQUIRED",
            },
        )
    return idempotency_key


def _idempotency_memory_begin_with_key(
    *,
    scope: str,
    tenant_id: str,
    idempotency_key: str,
    payload: object,
) -> tuple[str | None, str | None, JSONResponse | None]:
    request_fingerprint = _idempotency_fingerprint(payload)

    now_epoch = time.time()
    with _m._idempotency_store_lock:
        _idempotency_prune(now_epoch)
        store_key = f"{scope}:{tenant_id}:{idempotency_key.strip()}"
        cached_response = _m._idempotency_store.get(store_key)
        if cached_response:
            if cached_response.get("fingerprint") != request_fingerprint:
                return (
                    None,
                    None,
                    JSONResponse(
                        status_code=409,
                        content={
                            "error": "Idempotency key already used with different payload",
                            "code": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
                        },
                    ),
                )
            if cached_response.get("state") == "pending":
                return (
                    None,
                    None,
                    JSONResponse(
                        status_code=409,
                        content={
                            "error": "Idempotency request with same key is still in progress",
                            "code": "IDEMPOTENCY_KEY_IN_PROGRESS",
                        },
                    ),
                )
            cached_body = cached_response.get("body")
            cached_status = cached_response.get("status_code")
            return (
                None,
                None,
                JSONResponse(
                    status_code=int(cached_status) if isinstance(cached_status, int) else 200,
                    content=cached_body if isinstance(cached_body, dict) else {},
                    headers={"Idempotency-Replayed": "true"},
                ),
            )

        _m._idempotency_store[store_key] = {
            "fingerprint": request_fingerprint,
            "state": "pending",
            "status_code": 0,
            "body": {},
            "expires_at": now_epoch + _m.IDEMPOTENCY_PENDING_TTL_SECONDS,
        }
    return idempotency_key, request_fingerprint, None


async def _idempotency_preflight(
    *,
    scope: str,
    tenant_id: str,
    payload: object,
    idempotency_key_or_response: str | JSONResponse,
) -> tuple[str | None, str | None, JSONResponse | None]:
    if isinstance(idempotency_key_or_response, JSONResponse):
        return None, None, idempotency_key_or_response
    if _idempotency_uses_firestore():
        return await _idempotency_firestore_begin_with_key(
            scope=scope,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key_or_response,
            payload=payload,
        )
    return _idempotency_memory_begin_with_key(
        scope=scope,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key_or_response,
        payload=payload,
    )


def _idempotency_memory_record(
    *,
    scope: str,
    tenant_id: str,
    idempotency_key: str,
    fingerprint: str,
    status_code: int,
    body: dict[str, object],
) -> None:
    store_key = f"{scope}:{tenant_id}:{idempotency_key.strip()}"
    with _m._idempotency_store_lock:
        _m._idempotency_store[store_key] = {
            "fingerprint": fingerprint,
            "state": "done",
            "status_code": status_code,
            "body": body,
            "expires_at": time.time() + _m.IDEMPOTENCY_TTL_SECONDS,
        }


async def _idempotency_firestore_record(
    *,
    scope: str,
    tenant_id: str,
    idempotency_key: str,
    fingerprint: str,
    status_code: int,
    body: dict[str, object],
) -> None:
    db = _m._registry_db_client()
    if db is None:
        _idempotency_memory_record(
            scope=scope,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            status_code=status_code,
            body=body,
        )
        return
    now_epoch = time.time()
    doc_ref = _idempotency_doc_ref(
        db,
        scope=scope,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
    )
    payload = {
        "scope": scope,
        "tenant_id": tenant_id,
        "idempotency_key": idempotency_key.strip(),
        "fingerprint": fingerprint,
        "state": "done",
        "status_code": status_code,
        "body": body,
        "updated_at_epoch": now_epoch,
        "expires_at_epoch": now_epoch + _m.IDEMPOTENCY_TTL_SECONDS,
    }
    try:
        await _doc_set(doc_ref, payload, merge=True)
    except Exception as exc:
        logger.warning("Failed to persist firestore idempotency response: %s", exc)
        _idempotency_memory_record(
            scope=scope,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            status_code=status_code,
            body=body,
        )


async def _idempotency_begin(
    request: Request,
    *,
    scope: str,
    tenant_id: str,
    payload: object,
) -> tuple[str | None, str | None, JSONResponse | None]:
    return await _idempotency_preflight(
        scope=scope,
        tenant_id=tenant_id,
        payload=payload,
        idempotency_key_or_response=_idempotency_key_dependency(request),
    )


async def _idempotency_commit(
    *,
    scope: str,
    tenant_id: str,
    idempotency_key: str | None,
    fingerprint: str | None,
    status_code: int,
    body: dict[str, object],
) -> JSONResponse:
    if idempotency_key and fingerprint:
        if _idempotency_uses_firestore():
            await _idempotency_firestore_record(
                scope=scope,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                status_code=status_code,
                body=body,
            )
        else:
            _idempotency_memory_record(
                scope=scope,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                status_code=status_code,
                body=body,
            )
    return JSONResponse(
        status_code=status_code,
        content=body,
        headers={"Idempotency-Replayed": "false"},
    )


# Legacy Depends() helper
def require_idempotency_key(request: Request):
    return _idempotency_key_dependency(request)
