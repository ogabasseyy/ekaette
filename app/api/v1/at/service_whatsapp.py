"""WhatsApp channel business logic.

Handles text, image, interactive messages. Service window tracking.
Template fallback for during-call sends outside 24h window.
Routes delegate here — no business logic in whatsapp.py.

When the ADK Runner is initialized (after FastAPI lifespan), all messages
route through the full agent graph (vision, valuation, booking, catalog,
support). Falls back to bridge_text.py when Runner is unavailable (tests,
early startup).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.channels import adk_text_adapter
from app.configs import sanitize_log

from . import bridge_text
from . import providers
from .settings import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WA_SEND_IDEMPOTENCY_TTL_HOURS,
    WA_UTILITY_TEMPLATE_LANGUAGE,
    WA_UTILITY_TEMPLATE_NAME,
)

logger = logging.getLogger(__name__)

WA_MAX_CHARS = 4096

# Supported inbound types for AI processing
SUPPORTED_MESSAGE_TYPES = {"text", "image", "video", "interactive"}

# Unsupported types that get a polite reply (no AI processing)
UNSUPPORTED_MESSAGE_TYPES = {
    "audio", "document", "location", "contacts", "reaction", "sticker",
}


# ─── ADK Runner Access ───


def _get_adk_runner_and_service() -> tuple[Any, Any, Any]:
    """Access the ADK Runner and session_service singletons from main.py.

    Returns (runner, session_service, app_name) or (None, None, None)
    if not yet initialized.
    """
    try:
        import main as main_module
        runner = getattr(main_module, "text_runner", None)
        session_service = getattr(main_module, "session_service", None)
        base_name = getattr(main_module, "SESSION_APP_NAME", None)
        if runner is not None and session_service is not None and base_name:
            return runner, session_service, f"{base_name}_text"
    except Exception:
        logger.debug("ADK runner access failed", exc_info=True)
    return None, None, None


# ── Text Message Handling ──


async def handle_text_message(
    *,
    from_: str,
    text: str,
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> str:
    """Process inbound text through the ADK agent graph. Returns reply text.

    Falls back to bridge_text (standalone Gemini) when Runner is unavailable.
    """
    runner, session_service, app_name = _get_adk_runner_and_service()

    if runner is not None:
        result = await adk_text_adapter.send_text_message(
            runner=runner,
            session_service=session_service,
            app_name=app_name,
            user_id=f"wa_{from_}",
            message_text=text,
            channel="whatsapp",
            tenant_id=tenant_id,
            company_id=company_id,
        )
        reply_text = result.get("text") or ""
        return reply_text[:WA_MAX_CHARS]

    # Fallback: standalone Gemini (no agent graph)
    logger.debug("ADK Runner not available, using bridge_text fallback")
    ai_reply = await bridge_text.query_text(
        user_message=text,
        company_id=company_id,
        channel="whatsapp",
    )
    return ai_reply[:WA_MAX_CHARS]


# ── Image Message Handling ──


async def handle_image_message(
    *,
    from_: str,
    media_id: str,
    mime_type: str = "",
    caption: str = "",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> str:
    """Download image → route through ADK agent graph → reply text."""
    return await _handle_media_message(
        from_=from_,
        media_id=media_id,
        media_type="image",
        mime_type=mime_type,
        default_mime="image/jpeg",
        caption=caption,
        tenant_id=tenant_id,
        company_id=company_id,
    )


async def handle_video_message(
    *,
    from_: str,
    media_id: str,
    mime_type: str = "",
    caption: str = "",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> str:
    """Download video → route through ADK agent graph → reply text."""
    return await _handle_media_message(
        from_=from_,
        media_id=media_id,
        media_type="video",
        mime_type=mime_type,
        default_mime="video/mp4",
        caption=caption,
        tenant_id=tenant_id,
        company_id=company_id,
    )


async def _handle_media_message(
    *,
    from_: str,
    media_id: str,
    media_type: str,
    mime_type: str,
    default_mime: str,
    caption: str,
    tenant_id: str,
    company_id: str,
) -> str:
    """Shared handler for image/video — download, route through ADK, reply.

    Gemini 3 handles both natively via inline_data.
    Falls back to legacy direct Gemini vision when Runner is unavailable.
    """
    media_bytes, content_type = await providers.whatsapp_download_media(
        access_token=WHATSAPP_ACCESS_TOKEN,
        media_id=media_id,
        media_type=media_type,
    )

    if not media_bytes:
        logger.debug(
            "Empty media bytes after download — returning fallback",
            extra={"media_type": sanitize_log(media_type), "media_id": sanitize_log(media_id)},
        )
        return "Sorry, the media file appears to be empty. Please try sending it again."

    resolved_mime = content_type or mime_type or default_mime
    runner, session_service, app_name = _get_adk_runner_and_service()

    if runner is not None:
        result = await adk_text_adapter.send_media_message(
            runner=runner,
            session_service=session_service,
            app_name=app_name,
            user_id=f"wa_{from_}",
            media_bytes=media_bytes,
            mime_type=resolved_mime,
            caption=caption,
            channel="whatsapp",
            tenant_id=tenant_id,
            company_id=company_id,
        )
        reply_text = result.get("text") or ""
        return reply_text[:WA_MAX_CHARS]

    logger.debug("ADK Runner not available, using legacy %s analysis", media_type)
    return await _legacy_media_analysis(
        media_bytes=media_bytes,
        resolved_mime=resolved_mime,
        caption=caption,
        company_id=company_id,
    )


async def _legacy_media_analysis(
    *,
    media_bytes: bytes,
    resolved_mime: str,
    caption: str,
    company_id: str,
) -> str:
    """Legacy fallback: direct Gemini vision call without ADK agent graph."""
    try:
        from app.tools.vision_tools import _get_genai_client, VISION_MODEL
        from google.genai import types

        client = _get_genai_client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=VISION_MODEL,
            contents=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type=resolved_mime,
                        data=media_bytes,
                    )
                ),
                caption or "Analyze this media and provide any relevant assistance.",
            ],
            config=types.GenerateContentConfig(
                system_instruction=(
                    f"You are Ekaette, AI assistant for {company_id}. "
                    "Analyze the media and respond helpfully. Focus on concrete "
                    "business tasks like product identification, trade-in valuation, "
                    "or customer support."
                ),
                max_output_tokens=1024,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            text = "I received your media but couldn't analyze it. Could you send it again or describe what you need?"
    except Exception:
        logger.warning("Media analysis failed", exc_info=True)
        text = "I received your media but had trouble analyzing it. Please try again or send a text message."

    return text[:WA_MAX_CHARS]


# ── Unsupported Message Type ──


async def handle_unsupported_message_type(
    *,
    from_: str,
    message_type: str,
) -> str:
    """Return a polite reply for unsupported content types. No AI processing."""
    return (
        f"Sorry, I can't process {message_type} messages yet. "
        "Please send a text message, image, or video instead."
    )


# ── Interactive Message Sending ──


async def send_interactive_buttons(
    *,
    to: str,
    body_text: str,
    buttons: list[dict[str, str]],
) -> tuple[int, dict]:
    """Send reply buttons (max 3)."""
    validated_buttons: list[dict[str, str]] = []
    for i, btn in enumerate(buttons[:3]):
        title = btn.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Missing or invalid button title at index {i}")
        validated_buttons.append({
            "id": btn.get("id", f"btn_{i}"),
            "title": title.strip()[:20],
        })

    interactive = {
        "type": "button",
        "body": {"text": body_text[:1024]},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {"id": btn["id"], "title": btn["title"]},
                }
                for btn in validated_buttons
            ]
        },
    }
    return await providers.whatsapp_send_interactive(
        access_token=WHATSAPP_ACCESS_TOKEN,
        to=to,
        interactive=interactive,
    )


# ── Service Window Tracking ──

# In-process store for dev; Firestore in production
_service_windows: dict[str, float] = {}
_SERVICE_WINDOW_SECONDS = 24 * 60 * 60  # 24 hours
_SERVICE_WINDOW_MAX_ENTRIES = 50_000
_firestore_state_client = None
_firestore_state_client_lock = threading.Lock()


def _window_key(
    user_phone: str,
    phone_number_id: str,
    tenant_id: str,
    company_id: str,
) -> str:
    """Scoped service window key: tenant:company:phone_number_id:user_phone."""
    return f"{tenant_id}:{company_id}:{phone_number_id}:{user_phone}"


def check_service_window(
    user_phone: str,
    phone_number_id: str = "",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> bool:
    """Check if 24h service window is open for this user."""
    key = _window_key(
        user_phone,
        phone_number_id or WHATSAPP_PHONE_NUMBER_ID,
        tenant_id,
        company_id,
    )
    now = time.time()
    if _state_store_uses_firestore():
        return _check_service_window_firestore(key, now)

    _evict_service_windows(now)
    last_ts = _service_windows.get(key)
    if last_ts is None:
        return False
    return (now - last_ts) < _SERVICE_WINDOW_SECONDS


def record_inbound_timestamp(
    user_phone: str,
    phone_number_id: str = "",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> None:
    """Record inbound message timestamp to open/refresh service window."""
    key = _window_key(
        user_phone,
        phone_number_id or WHATSAPP_PHONE_NUMBER_ID,
        tenant_id,
        company_id,
    )
    now = time.time()
    if _state_store_uses_firestore():
        _record_inbound_timestamp_firestore(key, now)
        return

    _evict_service_windows(now)
    _service_windows[key] = now
    _evict_service_windows(now)


def reset_service_windows() -> None:
    """Reset service windows (for testing)."""
    _service_windows.clear()


# ── Template Fallback ──


async def send_with_template_fallback(
    *,
    to: str,
    text: str,
    phone_number_id: str = "",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> tuple[int, dict]:
    """Try service message; if no window, use utility template."""
    resolved_phone_id = phone_number_id or WHATSAPP_PHONE_NUMBER_ID

    if check_service_window(to, resolved_phone_id, tenant_id, company_id):
        # Within 24h window — send as text
        return await providers.whatsapp_send_text(
            access_token=WHATSAPP_ACCESS_TOKEN,
            phone_number_id=resolved_phone_id,
            to=to,
            body=text[:WA_MAX_CHARS],
        )

    # Outside window — use template
    if not WA_UTILITY_TEMPLATE_NAME:
        raise RuntimeError(
            "WA_UTILITY_TEMPLATE_NAME not configured — cannot send outside service window"
        )

    return await providers.whatsapp_send_template(
        access_token=WHATSAPP_ACCESS_TOKEN,
        phone_number_id=resolved_phone_id,
        to=to,
        template_name=WA_UTILITY_TEMPLATE_NAME,
        language_code=WA_UTILITY_TEMPLATE_LANGUAGE,
        components=[
            {
                "type": "body",
                "parameters": [{"type": "text", "text": text[:1024]}],
            }
        ],
    )


# ── Send Idempotency ──

# In-process store for dev; Firestore in production
_idempotency_store: dict[str, tuple[str, int, dict, float]] = {}
_IDEMPOTENCY_MAX_ENTRIES = 50_000
_idempotency_inflight: dict[str, tuple[str, asyncio.Future[tuple[int, dict]]]] = {}
_idempotency_inflight_guard = threading.Lock()


async def send_with_idempotency(
    *,
    idempotency_key: str,
    payload_hash: str,
    send_fn: Callable[[], Awaitable[tuple[int, dict]]],
) -> tuple[int, dict]:
    """Firestore-backed idempotency: same key+payload returns cached result; key reuse with different payload returns 409."""
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    if _state_store_uses_firestore():
        return await _send_with_idempotency_firestore(
            key_hash=key_hash,
            payload_hash=payload_hash,
            send_fn=send_fn,
        )

    now = time.time()
    _evict_idempotency_store(now)
    cached = _get_cached_idempotency_result(key_hash, payload_hash, now)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    is_owner = False
    with _idempotency_inflight_guard:
        inflight = _idempotency_inflight.get(key_hash)
        if inflight is None:
            future: asyncio.Future[tuple[int, dict]] = loop.create_future()
            _idempotency_inflight[key_hash] = (payload_hash, future)
            is_owner = True
        else:
            inflight_payload_hash, future = inflight
            if inflight_payload_hash != payload_hash:
                return 409, {"error": "Idempotency key conflict"}

    if not is_owner:
        await asyncio.shield(future)
        now = time.time()
        _evict_idempotency_store(now)
        cached = _get_cached_idempotency_result(key_hash, payload_hash, now)
        if cached is not None:
            return cached
        raise RuntimeError("Idempotency in-flight result missing")

    try:
        status, body = await send_fn()
        stored_at = time.time()
        _idempotency_store[key_hash] = (payload_hash, status, body, stored_at)
        _evict_idempotency_store(stored_at)
        if not future.done():
            future.set_result((status, body))
        return status, body
    except Exception as exc:
        if not future.done():
            future.set_exception(exc)
            future.exception()
        raise
    finally:
        with _idempotency_inflight_guard:
            current = _idempotency_inflight.get(key_hash)
            if current is not None and current[1] is future:
                _idempotency_inflight.pop(key_hash, None)


def reset_idempotency_store() -> None:
    """Reset idempotency store (for testing)."""
    _idempotency_store.clear()
    with _idempotency_inflight_guard:
        inflight = list(_idempotency_inflight.values())
        _idempotency_inflight.clear()
    for _, future in inflight:
        if not future.done():
            future.cancel()


# ── Failure Artifacts ──


async def write_failure_artifacts(
    *,
    wamid: str,
    error: str,
    tenant_id: str = "public",
) -> None:
    """Write redacted triage record. Production writes to Firestore + GCS."""
    error_kind = type(error).__name__ if not isinstance(error, str) else "str"
    logger.error(
        "WA webhook final failure",
        extra={
            "has_wamid": bool(sanitize_log(wamid)),
            "has_tenant_id": bool(sanitize_log(tenant_id)),
            "error_type": sanitize_log(error_kind),
        },
    )


def _state_store_uses_firestore() -> bool:
    mode = os.getenv("WA_STATE_STORE_MODE", "auto").strip().lower()
    if mode == "local":
        return False
    if mode == "firestore":
        return True
    if os.getenv("FIRESTORE_EMULATOR_HOST", "").strip():
        return True
    return bool(os.getenv("K_SERVICE", "").strip())


def _get_firestore_state_client():
    global _firestore_state_client
    with _firestore_state_client_lock:
        if _firestore_state_client is None:
            from google.cloud import firestore

            project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
            _firestore_state_client = firestore.Client(project=project)
        return _firestore_state_client


def _service_window_doc_ref(key: str):
    client = _get_firestore_state_client()
    collection_name = os.getenv("WA_SERVICE_WINDOW_COLLECTION", "wa_service_windows")
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    return client.collection(collection_name).document(key_hash)


def _idempotency_doc_ref(key_hash: str):
    client = _get_firestore_state_client()
    collection_name = os.getenv("WA_IDEMPOTENCY_COLLECTION", "wa_send_idempotency")
    return client.collection(collection_name).document(key_hash)


def _check_service_window_firestore(key: str, now: float) -> bool:
    try:
        doc_ref = _service_window_doc_ref(key)
        snapshot = doc_ref.get()
    except Exception:
        logger.error("Service window store read failed", exc_info=True)
        return False

    if not snapshot.exists:
        return False

    data = snapshot.to_dict() or {}
    expires_at = float(data.get("expires_at", 0.0) or 0.0)
    if expires_at <= now:
        try:
            doc_ref.delete()
        except Exception:
            logger.debug("Service window stale delete failed", exc_info=True)
        return False
    return True


def _record_inbound_timestamp_firestore(key: str, now: float) -> None:
    doc_ref = _service_window_doc_ref(key)
    payload = {
        "key": key,
        "updated_at": now,
        "expires_at": now + _SERVICE_WINDOW_SECONDS,
    }
    try:
        doc_ref.set(payload, merge=True)
        _prune_firestore_collection(doc_ref.parent, _SERVICE_WINDOW_MAX_ENTRIES)
    except Exception:
        logger.error("Service window store write failed", exc_info=True)
        # Fall back to local cache to keep message processing available.
        _service_windows[key] = now
        _evict_service_windows(now)


async def _send_with_idempotency_firestore(
    *,
    key_hash: str,
    payload_hash: str,
    send_fn: Callable[[], Awaitable[tuple[int, dict]]],
) -> tuple[int, dict]:
    from google.api_core.exceptions import AlreadyExists

    doc_ref = _idempotency_doc_ref(key_hash)
    ttl_seconds = _idempotency_ttl_seconds()
    wait_deadline = time.time() + max(ttl_seconds, 5.0)

    while True:
        now = time.time()
        snapshot = await asyncio.to_thread(doc_ref.get)
        if snapshot.exists:
            data = snapshot.to_dict() or {}
            expires_at = float(data.get("expires_at", 0.0) or 0.0)

            if expires_at > now:
                stored_payload_hash = str(data.get("payload_hash", ""))
                if stored_payload_hash != payload_hash:
                    return 409, {"error": "Idempotency key conflict"}

                if data.get("state") == "done":
                    status = int(data.get("status", 500))
                    body = data.get("body", {})
                    return status, body if isinstance(body, dict) else {}

                if now >= wait_deadline:
                    raise RuntimeError("Idempotency in-flight result missing")

                await asyncio.sleep(0.1)
                continue

            await asyncio.to_thread(doc_ref.delete)

        claim = {
            "payload_hash": payload_hash,
            "state": "inflight",
            "created_at": now,
            "updated_at": now,
            "expires_at": now + ttl_seconds,
        }
        try:
            await asyncio.to_thread(doc_ref.create, claim)
        except AlreadyExists:
            if now >= wait_deadline:
                raise RuntimeError("Idempotency in-flight result missing")
            await asyncio.sleep(0.05)
            continue

        try:
            status, body = await send_fn()
            safe_body = body if isinstance(body, dict) else {}
            await asyncio.to_thread(
                doc_ref.set,
                {
                    "payload_hash": payload_hash,
                    "state": "done",
                    "status": status,
                    "body": safe_body,
                    "updated_at": time.time(),
                    "expires_at": time.time() + ttl_seconds,
                },
                merge=True,
            )
            await asyncio.to_thread(
                _prune_firestore_collection,
                doc_ref.parent,
                _IDEMPOTENCY_MAX_ENTRIES,
            )
            return status, safe_body
        except Exception:
            try:
                await asyncio.to_thread(doc_ref.delete)
            except Exception:
                logger.debug("Failed to clear idempotency claim", exc_info=True)
            raise


def _prune_firestore_collection(collection_ref, max_entries: int) -> None:
    if max_entries <= 0:
        return
    docs = list(collection_ref.order_by("updated_at").limit(max_entries + 1).stream())
    overflow = len(docs) - max_entries
    if overflow <= 0:
        return
    for doc in docs[:overflow]:
        doc.reference.delete()


def _evict_service_windows(now: float) -> None:
    stale_before = now - _SERVICE_WINDOW_SECONDS
    stale_keys = [key for key, ts in _service_windows.items() if ts < stale_before]
    for key in stale_keys:
        _service_windows.pop(key, None)

    overflow = len(_service_windows) - _SERVICE_WINDOW_MAX_ENTRIES
    if overflow > 0:
        oldest_keys = sorted(_service_windows, key=_service_windows.__getitem__)[:overflow]
        for key in oldest_keys:
            _service_windows.pop(key, None)


def _idempotency_ttl_seconds() -> float:
    return max(float(WA_SEND_IDEMPOTENCY_TTL_HOURS) * 3600.0, 0.0)


def _evict_idempotency_store(now: float) -> None:
    ttl_seconds = _idempotency_ttl_seconds()
    stale_before = now - ttl_seconds
    stale_keys = [
        key for key, (_, _, _, ts) in _idempotency_store.items()
        if ts < stale_before
    ]
    for key in stale_keys:
        _idempotency_store.pop(key, None)

    overflow = len(_idempotency_store) - _IDEMPOTENCY_MAX_ENTRIES
    if overflow > 0:
        oldest_keys = sorted(
            _idempotency_store,
            key=lambda key: _idempotency_store[key][3],
        )[:overflow]
        for key in oldest_keys:
            _idempotency_store.pop(key, None)


def _get_cached_idempotency_result(
    key_hash: str,
    payload_hash: str,
    now: float,
) -> tuple[int, dict] | None:
    record = _idempotency_store.get(key_hash)
    if record is None:
        return None

    stored_payload_hash, status, body, stored_at = record
    if now - stored_at >= _idempotency_ttl_seconds():
        _idempotency_store.pop(key_hash, None)
        return None

    if stored_payload_hash != payload_hash:
        return 409, {"error": "Idempotency key conflict"}
    return status, body
