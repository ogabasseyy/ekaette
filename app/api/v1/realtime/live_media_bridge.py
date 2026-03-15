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
from app.configs.model_resolver import get_live_media_vision_model_candidates
from app.api.v1.realtime.models import SessionInitContext, SilenceState
from app.api.v1.realtime.runtime_cache import bind_runtime_values
from app.api.v1.realtime.voice_state_registry import update_voice_state
from app.tools.scoped_queries import tenant_company_collection
from app.tools.vision_tools import analyze_device_media, cache_latest_image, upload_to_cloud_storage
from shared.phone_identity import normalize_phone

logger = logging.getLogger(__name__)

_FIRESTORE_DB = None
_FIRESTORE_DB_LOCK = threading.Lock()
_ACTIVE_LIVE_SESSION_COLLECTION = "active_live_sessions"
_PENDING_EVENT_SUBCOLLECTION = "pending_media_events"
MAX_EVENT_AGE_SECONDS = 300
QUEUE_EXPIRY_SECONDS = 60
MAX_QUEUE_DEPTH = 3


def _background_analysis_timeout_seconds() -> float:
    raw = os.getenv("LIVE_MEDIA_ANALYSIS_TIMEOUT_SECONDS", "30").strip()
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = 30.0
    return max(5.0, parsed)


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


def _build_live_media_guidance(
    *,
    media_kind: str,
    handoff_summary: str,
    provenance: str,
) -> str:
    normalized_kind = (media_kind or "").strip() or "media"
    normalized_provenance = (
        (provenance or "").strip()
        or "The caller sent this media on WhatsApp during the current call."
    )
    guidance = (
        f"[System: {normalized_provenance} "
        f"This is customer-provided {normalized_kind} for the current live call."
    )
    if handoff_summary:
        guidance += f" Prior context: {handoff_summary}"
    guidance += (
        " Continue from that context without asking the caller to repeat it."
        " The runtime handles the spoken media-receipt acknowledgement for this upload."
        " Do not repeat that you have received the media unless the caller asks again."
        " The detailed visual analysis is already running in the background on the backend."
        " Do NOT transfer to vision_agent or call analyze_device_image_tool for this same media again."
        " Keep the call moving by asking one safe non-visual follow-up question while the analysis runs."
        " Start with trade-in questions that are not visible in the media."
        " Safe topics include the desired new device storage, desired new device colour,"
        " battery health, water exposure, repairs, Face ID or fingerprint status, and accessories."
        " Never ask the caller to describe colour, cracks, scratches, dents, screen condition,"
        " body condition, or any other visible cosmetic detail while this analysis runs."
        " Do NOT state any color, model, damage, or condition claim until the tool-backed"
        " analysis result becomes available in shared state.]"
    )
    return guidance


def _cache_injected_media_for_tool_reuse(
    *,
    user_id: str,
    session_id: str,
    media_bytes: bytes,
    mime_type: str,
) -> None:
    if not user_id or not session_id or not media_bytes or not mime_type:
        return
    cache_latest_image(
        user_id=user_id,
        session_id=session_id,
        image_data=media_bytes,
        mime_type=mime_type,
    )


def _persist_vision_media_handoff_state(
    ctx: SessionInitContext,
    *,
    state_value: str,
) -> None:
    normalized = state_value.strip().lower()
    if not normalized:
        return
    try:
        ctx.session_state["temp:vision_media_handoff_state"] = normalized
    except Exception:
        logger.debug("Failed to persist vision media handoff state to session", exc_info=True)
    try:
        update_voice_state(
            user_id=ctx.user_id,
            session_id=ctx.resolved_session_id,
            **{"temp:vision_media_handoff_state": normalized},
        )
    except Exception:
        logger.debug("Failed to persist vision media handoff state to registry", exc_info=True)


def _analysis_state_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_name": analysis.get("device_name", "Unknown"),
        "brand": analysis.get("brand", "Unknown"),
        "device_color": analysis.get("device_color", "unknown"),
        "color_confidence": analysis.get("color_confidence", 0.0),
        "condition": analysis.get("condition", "Unknown"),
        "power_state": analysis.get("power_state", "unknown"),
        "details": analysis.get("details", {}),
    }


async def _persist_session_state_updates(
    ctx: SessionInitContext,
    *,
    state_updates: dict[str, Any],
    async_save_session_state_fn: Any,
    session_service_obj: Any,
    session_app_name: str,
) -> None:
    if not state_updates:
        return
    try:
        ctx.session_state.update(state_updates)
    except Exception:
        logger.debug("Live media session state update skipped", exc_info=True)

    registry_updates: dict[str, Any] = {}
    background_status = state_updates.get("temp:background_vision_status")
    if isinstance(background_status, str) and background_status.strip():
        registry_updates["temp:background_vision_status"] = background_status.strip().lower()
    last_analysis = state_updates.get("temp:last_analysis")
    if isinstance(last_analysis, dict) and last_analysis:
        registry_updates["temp:last_analysis"] = last_analysis
    if registry_updates:
        try:
            update_voice_state(
                user_id=ctx.user_id,
                session_id=ctx.resolved_session_id,
                **registry_updates,
            )
        except Exception:
            logger.debug("Live media registry update skipped", exc_info=True)

    if not async_save_session_state_fn or not session_service_obj or not session_app_name:
        return
    try:
        await async_save_session_state_fn(
            session_service_obj,
            app_name=session_app_name,
            user_id=ctx.user_id,
            session_id=ctx.resolved_session_id,
            state_updates=state_updates,
        )
    except Exception:
        logger.debug("Live media persistent session save skipped", exc_info=True)


async def _run_background_media_analysis(
    *,
    ctx: SessionInitContext,
    event_ref: Any,
    event_payload: dict[str, Any],
    media_bytes: bytes,
    mime_type: str,
    silence_state: SilenceState,
    generation_ref: dict[str, int],
    generation: int,
    async_save_session_state_fn: Any,
    session_service_obj: Any,
    session_app_name: str,
) -> None:
    event_id = str(event_payload.get("event_id", "") or "")
    try:
        logger.info(
            "Background live media analysis started session=%s event=%s mime=%s generation=%s",
            ctx.resolved_session_id,
            event_id or "unknown",
            mime_type or "unknown",
            generation,
        )
        timeout_seconds = _background_analysis_timeout_seconds()
        analysis = await asyncio.wait_for(
            analyze_device_media(
                media_data=media_bytes,
                mime_type=mime_type,
                model_candidates=get_live_media_vision_model_candidates(),
            ),
            timeout=timeout_seconds,
        )
        if generation != generation_ref.get("value"):
            try:
                await _doc_update(
                    event_ref,
                    {
                        "analysis_status": "superseded",
                        "analysis_finished_at": _to_iso(_utc_now()),
                    },
                )
            except Exception:
                logger.debug("Superseded media analysis update skipped", exc_info=True)
            return

        if analysis.get("error"):
            await _persist_session_state_updates(
                ctx,
                state_updates={"temp:background_vision_status": "failed"},
                async_save_session_state_fn=async_save_session_state_fn,
                session_service_obj=session_service_obj,
                session_app_name=session_app_name,
            )
            try:
                await _doc_update(
                    event_ref,
                    {
                        "analysis_status": "failed",
                        "analysis_finished_at": _to_iso(_utc_now()),
                        "analysis_error": str(analysis.get("error", "") or "analysis_failed"),
                    },
                )
            except Exception:
                logger.debug("Failed analysis event update skipped", exc_info=True)
            logger.warning(
                "Background live media analysis failed session=%s event=%s error=%s",
                ctx.resolved_session_id,
                event_id or "unknown",
                str(analysis.get("error", "") or "analysis_failed"),
            )
            return

        await _persist_session_state_updates(
            ctx,
            state_updates={
                "temp:background_vision_status": "ready",
                "temp:last_analysis": _analysis_state_payload(analysis),
            },
            async_save_session_state_fn=async_save_session_state_fn,
            session_service_obj=session_service_obj,
            session_app_name=session_app_name,
        )
        try:
            await _doc_update(
                event_ref,
                {
                    "analysis_status": "ready",
                    "analysis_finished_at": _to_iso(_utc_now()),
                },
            )
        except Exception:
            logger.debug("Completed analysis event update skipped", exc_info=True)
        logger.info(
            "Background live media analysis complete session=%s event=%s device=%s color=%s confidence=%.2f",
            ctx.resolved_session_id,
            event_id or "unknown",
            str(analysis.get("device_name", "") or "Unknown"),
            str(analysis.get("device_color", "") or "unknown"),
            float(analysis.get("color_confidence", 0.0) or 0.0),
        )
    except asyncio.TimeoutError:
        if generation == generation_ref.get("value"):
            await _persist_session_state_updates(
                ctx,
                state_updates={"temp:background_vision_status": "failed"},
                async_save_session_state_fn=async_save_session_state_fn,
                session_service_obj=session_service_obj,
                session_app_name=session_app_name,
            )
        try:
            await _doc_update(
                event_ref,
                {
                    "analysis_status": "timeout",
                    "analysis_finished_at": _to_iso(_utc_now()),
                    "analysis_error": "timeout",
                },
            )
        except Exception:
            logger.debug("Timed out analysis event update skipped", exc_info=True)
        logger.warning(
            "Background live media analysis timed out session=%s event=%s timeout=%.1fs generation=%s",
            ctx.resolved_session_id,
            event_id or "unknown",
            timeout_seconds,
            generation,
        )
        return
    except asyncio.CancelledError:
        logger.info(
            "Background live media analysis cancelled session=%s event=%s generation=%s",
            ctx.resolved_session_id,
            event_id or "unknown",
            generation,
        )
        try:
            await _doc_update(
                event_ref,
                {
                    "analysis_status": "cancelled",
                    "analysis_finished_at": _to_iso(_utc_now()),
                    "analysis_error": "cancelled",
                },
            )
        except Exception:
            logger.debug("Cancelled analysis event update skipped", exc_info=True)
        raise
    except Exception:
        if generation == generation_ref.get("value"):
            await _persist_session_state_updates(
                ctx,
                state_updates={"temp:background_vision_status": "failed"},
                async_save_session_state_fn=async_save_session_state_fn,
                session_service_obj=session_service_obj,
                session_app_name=session_app_name,
            )
        logger.debug("Background live media analysis crashed", exc_info=True)
        try:
            await _doc_update(
                event_ref,
                {
                    "analysis_status": "failed",
                    "analysis_finished_at": _to_iso(_utc_now()),
                    "analysis_error": "exception",
                },
            )
        except Exception:
            logger.debug("Crashed analysis event update skipped", exc_info=True)
    finally:
        if generation == generation_ref.get("value"):
            silence_state.pending_media_analysis = False


def _start_background_media_analysis(
    *,
    ctx: SessionInitContext,
    event_ref: Any,
    event_payload: dict[str, Any],
    media_bytes: bytes,
    mime_type: str,
    silence_state: SilenceState,
    generation_ref: dict[str, int],
    generation: int,
    async_save_session_state_fn: Any,
    session_service_obj: Any,
    session_app_name: str,
) -> asyncio.Task[Any]:
    return asyncio.create_task(
        _run_background_media_analysis(
            ctx=ctx,
            event_ref=event_ref,
            event_payload=event_payload,
            media_bytes=media_bytes,
            mime_type=mime_type,
            silence_state=silence_state,
            generation_ref=generation_ref,
            generation=generation,
            async_save_session_state_fn=async_save_session_state_fn,
            session_service_obj=session_service_obj,
            session_app_name=session_app_name,
        )
    )


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
        event_collection = _event_collection(doc_ref)
        if event_collection is not None:
            now_iso = _to_iso(_utc_now())
            for snapshot in await _query_documents(event_collection):
                payload = _snapshot_dict(snapshot)
                if not payload:
                    continue
                status = str(payload.get("status", "") or "").strip().lower()
                if status not in {"pending", "delivering"}:
                    continue
                try:
                    await _doc_update(
                        snapshot.reference,
                        {
                            "status": "expired",
                            "expired_at": now_iso,
                            "expiry_reason": "session_closed",
                        },
                    )
                except Exception:
                    logger.debug("Live media event expiry-on-close skipped", exc_info=True)
    except Exception:
        logger.debug("Active live session pending event cleanup skipped", exc_info=True)
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
        logger.warning(
            "Active live media injection skipped because channel is disabled conversation=%s session=%s channel=%s",
            conversation_id,
            str(session_data.get("session_id", "") or "").strip(),
            voice_channel or "unknown",
        )
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
    logger.info(
        "Queued active live media event=%s session=%s kind=%s channel=%s",
        event_id,
        target_session_id,
        media_type,
        voice_channel,
    )
    return {
        "status": "queued",
        # Keep the receipt acknowledgement on the live call itself.
        "reply_text": "",
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
    background_analysis_task: asyncio.Task[Any] | None = None
    analysis_generation = {"value": 0}
    try:
        await register_active_live_session(ctx)
        (
            types_mod,
            async_save_session_state_fn,
            session_service_obj,
            session_app_name,
        ) = bind_runtime_values(
            "types",
            "async_save_session_state",
            "session_service",
            "SESSION_APP_NAME",
        )
        if (
            types_mod is None
            or not hasattr(types_mod, "Content")
            or not hasattr(types_mod, "Part")
        ):
            logger.error("Live media bridge runtime types binding is unavailable")
            return
        heartbeat_due = time.monotonic()
        while session_alive.is_set():
            now_monotonic = time.monotonic()
            if now_monotonic >= heartbeat_due:
                try:
                    await heartbeat_active_live_session(ctx)
                except Exception:
                    logger.debug("Active live session heartbeat skipped", exc_info=True)
                heartbeat_due = now_monotonic + 5.0

            if silence_state.greeting_lock_active or silence_state.assistant_output_active:
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
            guidance = _build_live_media_guidance(
                media_kind=media_kind,
                handoff_summary=summary,
                provenance=provenance,
            )
            try:
                _cache_injected_media_for_tool_reuse(
                    user_id=ctx.user_id,
                    session_id=ctx.resolved_session_id,
                    media_bytes=media_bytes,
                    mime_type=mime_type,
                )
                analysis_generation["value"] += 1
                current_generation = analysis_generation["value"]
                await _persist_session_state_updates(
                    ctx,
                    state_updates={
                        "temp:background_vision_status": "running",
                        "temp:vision_media_handoff_state": "",
                        "temp:pending_media_received_voice_ack": media_kind,
                        "temp:last_media_blob_path": str(event_payload.get("blob_path", "") or ""),
                        "temp:last_media_gcs_uri": str(event_payload.get("gcs_uri", "") or ""),
                        "temp:last_media_mime_type": mime_type,
                    },
                    async_save_session_state_fn=async_save_session_state_fn,
                    session_service_obj=session_service_obj,
                    session_app_name=session_app_name,
                )
                silence_state.last_client_activity = now_monotonic
                silence_state.silence_nudge_count = 0
                silence_state.awaiting_agent_response = True
                silence_state.user_spoke_at = now_monotonic
                silence_state.response_nudge_count = 0
                silence_state.pending_media_analysis = True
                if background_analysis_task is not None and not background_analysis_task.done():
                    background_analysis_task.cancel()
                    try:
                        await background_analysis_task
                    except asyncio.CancelledError:
                        pass
                logger.info(
                    "Injecting queued live media session=%s event=%s kind=%s",
                    ctx.resolved_session_id,
                    str(event_payload.get("event_id", "") or ""),
                    media_kind,
                )
                live_request_queue.send_content(
                    types_mod.Content(parts=[types_mod.Part(text=guidance)])
                )
                await _doc_update(
                    event_ref,
                    {"status": "delivered", "delivered_at": _to_iso(_utc_now())},
                )
                background_analysis_task = _start_background_media_analysis(
                    ctx=ctx,
                    event_ref=event_ref,
                    event_payload=event_payload,
                    media_bytes=media_bytes,
                    mime_type=mime_type,
                    silence_state=silence_state,
                    generation_ref=analysis_generation,
                    generation=current_generation,
                    async_save_session_state_fn=async_save_session_state_fn,
                    session_service_obj=session_service_obj,
                    session_app_name=session_app_name,
                )
            except Exception:
                silence_state.pending_media_analysis = False
                await _persist_session_state_updates(
                    ctx,
                    state_updates={"temp:background_vision_status": "failed"},
                    async_save_session_state_fn=async_save_session_state_fn,
                    session_service_obj=session_service_obj,
                    session_app_name=session_app_name,
                )
                logger.debug("Live media injection failed", exc_info=True)
                try:
                    await _doc_update(event_ref, {"status": "failed", "failed_at": _to_iso(_utc_now())})
                except Exception:
                    logger.debug("Live media event failure mark skipped", exc_info=True)
    finally:
        if background_analysis_task is not None and not background_analysis_task.done():
            background_analysis_task.cancel()
            try:
                await background_analysis_task
            except asyncio.CancelledError:
                pass
        await unregister_active_live_session(ctx)


__all__ = [
    "active_live_media_task",
    "enqueue_media_for_active_live_session",
    "heartbeat_active_live_session",
    "register_active_live_session",
    "unregister_active_live_session",
]
