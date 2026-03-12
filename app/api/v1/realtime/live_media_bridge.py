"""Cross-channel media injection into active live voice sessions.

This module implements a feature-flagged bridge between WhatsApp media intake
and an already active live voice session. It does not attempt to unify ADK
sessions across channels. Instead, it maintains a shared conversation registry
and injects structured media events into the active live session when allowed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.api.v1.admin.firestore_helpers import _doc_delete, _doc_get, _doc_set, _doc_update
from app.api.v1.realtime.models import SessionInitContext, SilenceState
from app.api.v1.realtime.runtime_cache import bind_runtime_values
from app.tools.scoped_queries import tenant_company_collection
from app.tools.vision_tools import upload_to_cloud_storage
from shared.phone_identity import normalize_phone

logger = logging.getLogger(__name__)

_FIRESTORE_DB = None
_FIRESTORE_DB_LOCK = threading.Lock()
_ACTIVE_LIVE_SESSION_COLLECTION = "active_live_sessions"
_PENDING_EVENT_SUBCOLLECTION = "pending_media_events"
MAX_EVENT_AGE_SECONDS = 300
QUEUE_EXPIRY_SECONDS = 60
MAX_QUEUE_DEPTH = 3


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _feature_enabled() -> bool:
    return _env_flag("LIVE_CROSS_CHANNEL_MEDIA_INJECTION_ENABLED", False)


def _channel_enabled(channel: str) -> bool:
    normalized = (channel or "").strip().lower()
    if normalized == "whatsapp_voice":
        return _env_flag("LIVE_CROSS_CHANNEL_MEDIA_INJECTION_WHATSAPP_VOICE", False)
    if normalized == "at_voice":
        return _env_flag("LIVE_CROSS_CHANNEL_MEDIA_INJECTION_AT_VOICE", False)
    if normalized == "web_voice":
        return _env_flag("LIVE_CROSS_CHANNEL_MEDIA_INJECTION_WEB_VOICE", False)
    return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(raw: str) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
            logger.warning("Live media bridge Firestore unavailable: %s", exc)
            _FIRESTORE_DB = None
    return _FIRESTORE_DB


def _conversation_identity(ctx: SessionInitContext) -> str:
    caller_phone = normalize_phone(ctx.caller_phone) or ctx.caller_phone.strip()
    return caller_phone or ctx.user_id


def build_conversation_id(tenant_id: str, company_id: str, phone_or_user: str) -> str:
    normalized = normalize_phone(phone_or_user) or (phone_or_user or "").strip()
    base = f"{tenant_id.strip()}:{company_id.strip()}:{normalized}"
    return hashlib.sha256(base.encode()).hexdigest()[:32]


def _conversation_id_for_ctx(ctx: SessionInitContext) -> str:
    return build_conversation_id(ctx.tenant_id, ctx.company_id, _conversation_identity(ctx))


def _voice_channel_for_session(session_id: str) -> str:
    normalized = (session_id or "").strip().lower()
    if normalized.startswith("wa-"):
        return "whatsapp_voice"
    if normalized.startswith("sip-"):
        return "at_voice"
    return "web_voice"


def _session_doc_ref(db: Any, tenant_id: str, company_id: str, conversation_id: str) -> Any | None:
    collection = tenant_company_collection(
        db,
        tenant_id,
        company_id,
        _ACTIVE_LIVE_SESSION_COLLECTION,
    )
    if collection is None:
        return None
    return collection.document(conversation_id)


def _event_collection(doc_ref: Any) -> Any | None:
    if doc_ref is None:
        return None
    collection_fn = getattr(doc_ref, "collection", None)
    if not callable(collection_fn):
        return None
    return collection_fn(_PENDING_EVENT_SUBCOLLECTION)


def _snapshot_dict(snapshot: Any) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    exists = getattr(snapshot, "exists", False)
    if exists is False:
        return None
    to_dict = getattr(snapshot, "to_dict", None)
    if not callable(to_dict):
        return None
    payload = to_dict() or {}
    return payload if isinstance(payload, dict) else None


async def _query_documents(query: Any) -> list[Any]:
    stream_fn = getattr(query, "stream", None)
    if not callable(stream_fn):
        return []
    return list(await asyncio.to_thread(lambda: list(stream_fn())))


def _build_injection_reply(media_type: str) -> str:
    kind = "media" if not media_type else media_type
    return f"I've received your {kind} and I'm checking it on the call now."


async def register_active_live_session(ctx: SessionInitContext) -> None:
    if not _feature_enabled():
        return
    db = _get_firestore_db()
    if db is None:
        return
    conversation_id = _conversation_id_for_ctx(ctx)
    doc_ref = _session_doc_ref(db, ctx.tenant_id, ctx.company_id, conversation_id)
    if doc_ref is None:
        return
    now = _utc_now()
    payload = {
        "conversation_id": conversation_id,
        "tenant_id": ctx.tenant_id,
        "company_id": ctx.company_id,
        "user_id": ctx.user_id,
        "session_id": ctx.resolved_session_id,
        "caller_phone": normalize_phone(ctx.caller_phone) or ctx.caller_phone.strip(),
        "voice_channel": _voice_channel_for_session(ctx.resolved_session_id),
        "status": "active",
        "heartbeat_at": _to_iso(now),
        "expires_at": _to_iso(now + timedelta(seconds=MAX_EVENT_AGE_SECONDS)),
        "next_sequence_number": 1,
    }
    await _doc_set(doc_ref, payload, merge=True)


async def heartbeat_active_live_session(ctx: SessionInitContext) -> None:
    if not _feature_enabled():
        return
    db = _get_firestore_db()
    if db is None:
        return
    conversation_id = _conversation_id_for_ctx(ctx)
    doc_ref = _session_doc_ref(db, ctx.tenant_id, ctx.company_id, conversation_id)
    if doc_ref is None:
        return
    now = _utc_now()
    await _doc_update(
        doc_ref,
        {
            "heartbeat_at": _to_iso(now),
            "expires_at": _to_iso(now + timedelta(seconds=MAX_EVENT_AGE_SECONDS)),
            "status": "active",
        },
    )


async def unregister_active_live_session(ctx: SessionInitContext) -> None:
    if not _feature_enabled():
        return
    db = _get_firestore_db()
    if db is None:
        return
    conversation_id = _conversation_id_for_ctx(ctx)
    doc_ref = _session_doc_ref(db, ctx.tenant_id, ctx.company_id, conversation_id)
    if doc_ref is None:
        return
    try:
        await _doc_delete(doc_ref)
    except Exception:
        logger.debug("Active live session unregister skipped", exc_info=True)


async def enqueue_media_for_active_live_session(
    *,
    from_: str,
    tenant_id: str,
    company_id: str,
    media_bytes: bytes,
    mime_type: str,
    media_type: str,
    caption: str = "",
    handoff_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Queue cross-channel media into an active live voice session if enabled."""
    if not _feature_enabled():
        return None

    normalized_phone = normalize_phone(from_) or (from_ or "").strip()
    if not normalized_phone:
        return None

    db = _get_firestore_db()
    if db is None:
        return None

    conversation_id = build_conversation_id(tenant_id, company_id, normalized_phone)
    doc_ref = _session_doc_ref(db, tenant_id, company_id, conversation_id)
    if doc_ref is None:
        return None

    snapshot = await _doc_get(doc_ref)
    session_data = _snapshot_dict(snapshot)
    if session_data is None:
        return None
    if str(session_data.get("status", "")).strip().lower() != "active":
        return None

    voice_channel = str(session_data.get("voice_channel", "") or "").strip().lower()
    if not _channel_enabled(voice_channel):
        return None

    expires_at = _from_iso(str(session_data.get("expires_at", "") or ""))
    now = _utc_now()
    if expires_at is not None and expires_at <= now:
        return None

    event_collection = _event_collection(doc_ref)
    if event_collection is None:
        return None

    queue_docs = await _query_documents(event_collection.limit(MAX_QUEUE_DEPTH + 2))
    live_queue_depth = 0
    for item in queue_docs:
        payload = _snapshot_dict(item)
        if not payload:
            continue
        status = str(payload.get("status", "") or "").strip().lower()
        queue_expires_at = _from_iso(str(payload.get("queue_expires_at", "") or ""))
        if queue_expires_at is not None and queue_expires_at <= now:
            continue
        if status in {"pending", "delivering"}:
            live_queue_depth += 1
    if live_queue_depth >= MAX_QUEUE_DEPTH:
        return None

    target_user_id = str(session_data.get("user_id", "") or "").strip() or normalized_phone
    target_session_id = str(session_data.get("session_id", "") or "").strip()
    upload_result = await upload_to_cloud_storage(
        media_bytes,
        mime_type,
        user_id=target_user_id,
        session_id=target_session_id or conversation_id,
    )
    if "error" in upload_result:
        logger.warning(
            "Active live media injection upload failed conversation=%s error=%s",
            conversation_id,
            upload_result.get("error"),
        )
        return None

    sequence_number = int(session_data.get("next_sequence_number", 1) or 1)
    event_id = uuid.uuid4().hex
    event_doc = event_collection.document(event_id)
    handoff_summary = ""
    pending_reason = ""
    if isinstance(handoff_context, dict):
        handoff_summary = str(handoff_context.get("conversation_summary", "") or "").strip()[:480]
        pending_reason = str(handoff_context.get("pending_reason", "") or "").strip()
    source_channel = "whatsapp_chat"
    payload = {
        "event_id": event_id,
        "type": "external_media_received",
        "conversation_id": conversation_id,
        "target_session_id": target_session_id,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "source_channel": source_channel,
        "target_channel": voice_channel,
        "media_kind": media_type,
        "mime_type": mime_type,
        "caption": caption,
        "handoff_summary": handoff_summary,
        "pending_reason": pending_reason,
        "provenance_text": "The caller sent this media on WhatsApp during the current call.",
        "received_at": _to_iso(now),
        "expires_at": _to_iso(now + timedelta(seconds=MAX_EVENT_AGE_SECONDS)),
        "queue_expires_at": _to_iso(now + timedelta(seconds=QUEUE_EXPIRY_SECONDS)),
        "sequence_number": sequence_number,
        "gcs_uri": upload_result.get("gcs_uri", ""),
        "blob_path": upload_result.get("blob_path", ""),
        "status": "pending",
    }
    await _doc_set(event_doc, payload, merge=False)
    await _doc_update(
        doc_ref,
        {
            "next_sequence_number": sequence_number + 1,
            "heartbeat_at": _to_iso(now),
            "expires_at": _to_iso(now + timedelta(seconds=MAX_EVENT_AGE_SECONDS)),
        },
    )
    return {
        "status": "queued",
        "reply_text": _build_injection_reply(media_type),
    }


async def _claim_next_pending_media_event(ctx: SessionInitContext) -> tuple[Any | None, dict[str, Any] | None]:
    db = _get_firestore_db()
    if db is None:
        return None, None
    conversation_id = _conversation_id_for_ctx(ctx)
    doc_ref = _session_doc_ref(db, ctx.tenant_id, ctx.company_id, conversation_id)
    if doc_ref is None:
        return None, None
    event_collection = _event_collection(doc_ref)
    if event_collection is None:
        return None, None

    query = (
        event_collection.where("target_session_id", "==", ctx.resolved_session_id)
        .where("status", "==", "pending")
        .limit(MAX_QUEUE_DEPTH + 2)
    )
    docs = await _query_documents(query)
    best_ref = None
    best_payload = None
    now = _utc_now()
    for snapshot in docs:
        payload = _snapshot_dict(snapshot)
        if not payload:
            continue
        expires_at = _from_iso(str(payload.get("expires_at", "") or ""))
        queue_expires_at = _from_iso(str(payload.get("queue_expires_at", "") or ""))
        if (expires_at is not None and expires_at <= now) or (
            queue_expires_at is not None and queue_expires_at <= now
        ):
            try:
                await _doc_update(snapshot.reference, {"status": "expired"})
            except Exception:
                logger.debug("Live media event expiry update skipped", exc_info=True)
            continue
        if best_payload is None or int(payload.get("sequence_number", 0) or 0) < int(
            best_payload.get("sequence_number", 0) or 0
        ):
            best_ref = snapshot.reference
            best_payload = payload
    if best_ref is None or best_payload is None:
        return None, None
    try:
        await _doc_update(best_ref, {"status": "delivering", "delivering_at": _to_iso(now)})
    except Exception:
        logger.debug("Live media event claim failed", exc_info=True)
        return None, None
    return best_ref, best_payload


async def _load_media_bytes(event_payload: dict[str, Any]) -> tuple[bytes | None, str]:
    blob_path = str(event_payload.get("blob_path", "") or "").strip()
    if not blob_path:
        return None, ""
    try:
        from app.tools.vision_tools import MEDIA_BUCKET, _get_storage_client

        storage_client = _get_storage_client()
        if storage_client is None or not MEDIA_BUCKET:
            return None, ""
        bucket = storage_client.bucket(MEDIA_BUCKET)
        blob = bucket.blob(blob_path)
        content = await asyncio.to_thread(blob.download_as_bytes)
        mime_type = str(event_payload.get("mime_type", "") or "").strip()
        return content, mime_type
    except Exception:
        logger.debug("Live media download failed", exc_info=True)
        return None, ""


async def active_live_media_task(
    ctx: SessionInitContext,
    live_request_queue,
    session_alive: asyncio.Event,
    silence_state: SilenceState,
) -> None:
    """Heartbeat active session registry and inject queued cross-channel media."""
    if not _feature_enabled():
        return
    try:
        await register_active_live_session(ctx)
        (types_mod,) = bind_runtime_values("types")
        heartbeat_due = time.monotonic()
        while session_alive.is_set():
            now_monotonic = time.monotonic()
            if now_monotonic >= heartbeat_due:
                try:
                    await heartbeat_active_live_session(ctx)
                except Exception:
                    logger.debug("Active live session heartbeat skipped", exc_info=True)
                heartbeat_due = now_monotonic + 5.0

            if silence_state.greeting_lock_active or silence_state.agent_busy:
                await asyncio.sleep(0.25)
                continue

            event_ref, event_payload = await _claim_next_pending_media_event(ctx)
            if event_ref is None or event_payload is None:
                await asyncio.sleep(0.35)
                continue

            media_bytes, mime_type = await _load_media_bytes(event_payload)
            if not media_bytes or not mime_type:
                try:
                    await _doc_update(event_ref, {"status": "failed", "failed_at": _to_iso(_utc_now())})
                except Exception:
                    logger.debug("Live media event fail mark skipped", exc_info=True)
                continue

            summary = str(event_payload.get("handoff_summary", "") or "").strip()
            media_kind = str(event_payload.get("media_kind", "") or "media").strip()
            provenance = str(event_payload.get("provenance_text", "") or "").strip()
            guidance = (
                "[System: "
                + (provenance or "The caller sent this media on WhatsApp during the current call.")
            )
            if summary:
                guidance += f" Prior context: {summary}"
            guidance += (
                " Continue from it now, do not ask the customer to repeat the context, "
                f"and use it to assess the {media_kind}.]"
            )
            try:
                live_request_queue.send_realtime(types_mod.Blob(mime_type=mime_type, data=media_bytes))
                live_request_queue.send_content(
                    types_mod.Content(parts=[types_mod.Part(text=guidance)])
                )
                await _doc_update(
                    event_ref,
                    {"status": "delivered", "delivered_at": _to_iso(_utc_now())},
                )
            except Exception:
                logger.debug("Live media injection failed", exc_info=True)
                try:
                    await _doc_update(event_ref, {"status": "failed", "failed_at": _to_iso(_utc_now())})
                except Exception:
                    logger.debug("Live media event failure mark skipped", exc_info=True)
    finally:
        await unregister_active_live_session(ctx)


__all__ = [
    "active_live_media_task",
    "enqueue_media_for_active_live_session",
    "heartbeat_active_live_session",
    "register_active_live_session",
    "unregister_active_live_session",
]
