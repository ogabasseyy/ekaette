"""AT endpoint idempotency for outbound/campaign/transfer.

Follows the same 3-phase pattern as admin idempotency
(preflight → business logic → commit) but self-contained.

At-least-once delivery safety for AT callbacks (sec 6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import threading

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# In-memory store (matches admin pattern; Firestore backend is post-hackathon)
_store: dict[str, dict[str, object]] = {}
_store_lock = threading.Lock()

# TTLs
IDEMPOTENCY_TTL_SECONDS = 3600       # 1h for completed entries
PENDING_TTL_SECONDS = 30             # 30s for in-progress entries


def _fingerprint(payload: object) -> str:
    """SHA-256 of canonical JSON payload."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def require_idempotency_key(request: Request) -> str:
    """FastAPI dependency: extract and validate Idempotency-Key header."""
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )
    if len(key) > 256:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key too long (max 256 chars)",
        )
    return key


def idempotency_preflight(
    *,
    scope: str,
    tenant_id: str,
    idempotency_key: str,
    payload: object,
) -> dict | None:
    """Check if this request was already processed.

    Returns:
        None — first request, proceed with business logic
        dict — cached response body (replay)

    Raises:
        HTTPException 409 — key reused with different payload or still in progress
    """
    store_key = f"at:{scope}:{tenant_id}:{idempotency_key}"
    fp = _fingerprint(payload)
    now = time.time()

    with _store_lock:
        # Prune expired entries periodically (every 100 checks)
        if len(_store) > 100:
            expired = [
                k for k, v in _store.items()
                if now > float(v.get("expires_at", 0))
            ]
            for k in expired:
                _store.pop(k, None)

        existing = _store.get(store_key)
        if existing is None:
            # First request — claim the slot
            _store[store_key] = {
                "fingerprint": fp,
                "state": "pending",
                "expires_at": now + PENDING_TTL_SECONDS,
            }
            return None

        # Key exists — check fingerprint match
        if existing.get("fingerprint") != fp:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key reused with different payload",
            )

        state = existing.get("state")
        if state == "pending":
            # Check if pending entry is stale
            if now > float(existing.get("expires_at", 0)):
                # Stale pending — reclaim
                _store[store_key] = {
                    "fingerprint": fp,
                    "state": "pending",
                    "expires_at": now + PENDING_TTL_SECONDS,
                }
                return None
            raise HTTPException(
                status_code=409,
                detail="Request is still being processed",
            )

        # state == "done" — replay cached response
        return dict(existing.get("body", {}))  # type: ignore[arg-type]


def idempotency_commit(
    *,
    scope: str,
    tenant_id: str,
    idempotency_key: str,
    body: dict,
) -> None:
    """Record the response for future replays."""
    store_key = f"at:{scope}:{tenant_id}:{idempotency_key}"
    now = time.time()

    with _store_lock:
        _store[store_key] = {
            "fingerprint": _store.get(store_key, {}).get("fingerprint", ""),
            "state": "done",
            "body": body,
            "expires_at": now + IDEMPOTENCY_TTL_SECONDS,
        }


# ── Callback deduplication (for at-least-once AT webhook delivery) ──

_callback_seen: dict[str, float] = {}
_callback_lock = threading.Lock()
CALLBACK_DEDUP_WINDOW = 300  # 5 minutes


def is_duplicate_callback(session_id: str, event_key: str) -> bool:
    """Check if this callback event was already processed.

    Returns True if duplicate (skip processing).
    """
    dedup_key = f"{session_id}:{event_key}"
    now = time.time()

    with _callback_lock:
        seen_at = _callback_seen.get(dedup_key)
        if seen_at is not None:
            if now - seen_at <= CALLBACK_DEDUP_WINDOW:
                return True
            _callback_seen.pop(dedup_key, None)

        # Prune old entries
        if len(_callback_seen) > 1000:
            expired = [
                k for k, ts in _callback_seen.items()
                if now - ts > CALLBACK_DEDUP_WINDOW
            ]
            for k in expired:
                _callback_seen.pop(k, None)

        _callback_seen[dedup_key] = now
        return False
