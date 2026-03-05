"""WhatsApp channel business logic.

Handles text, image, interactive messages. Service window tracking.
Template fallback for during-call sends outside 24h window.
Routes delegate here — no business logic in whatsapp.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Awaitable, Callable

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
SUPPORTED_MESSAGE_TYPES = {"text", "image", "interactive"}

# Unsupported types that get a polite reply (no AI processing)
UNSUPPORTED_MESSAGE_TYPES = {
    "audio", "video", "document", "location", "contacts", "reaction", "sticker",
}


# ── Text Message Handling ──


async def handle_text_message(
    *,
    from_: str,
    text: str,
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> str:
    """Process inbound text → AI reply via Gemini. Returns reply text."""
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
    """Download image → Gemini vision → reply text."""
    image_bytes, content_type = await providers.whatsapp_download_media(
        access_token=WHATSAPP_ACCESS_TOKEN,
        media_id=media_id,
        media_type="image",
    )

    # Use Gemini vision for image analysis
    try:
        from app.configs.model_resolver import resolve_live_model_id
        from app.tools.vision_tools import _get_genai_client
        from google.genai import types

        client = _get_genai_client()
        resolved_model = resolve_live_model_id()
        resolved_mime = mime_type or content_type or "image/jpeg"
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=resolved_model,
            contents=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type=resolved_mime,
                        data=image_bytes,
                    )
                ),
                caption or "Describe this image and provide any relevant assistance.",
            ],
            config=types.GenerateContentConfig(
                system_instruction=(
                    f"You are Ekaette, AI assistant for {company_id}. "
                    "Analyze the image and respond helpfully. Focus on concrete "
                    "business tasks like product identification, trade-in valuation, "
                    "or customer support."
                ),
                max_output_tokens=1024,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            text = "I received your image but couldn't analyze it. Could you send it again or describe what you need?"
    except Exception:
        logger.warning("Vision analysis failed", exc_info=True)
        text = "I received your image but had trouble analyzing it. Please try again or send a text message."

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
        "Please send a text message or image instead."
    )


# ── Interactive Message Sending ──


async def send_interactive_buttons(
    *,
    to: str,
    body_text: str,
    buttons: list[dict[str, str]],
) -> tuple[int, dict]:
    """Send reply buttons (max 3)."""
    interactive = {
        "type": "button",
        "body": {"text": body_text[:1024]},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {"id": btn.get("id", f"btn_{i}"), "title": btn["title"][:20]},
                }
                for i, btn in enumerate(buttons[:3])
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
    now = time.time()
    _evict_service_windows(now)
    key = _window_key(
        user_phone,
        phone_number_id or WHATSAPP_PHONE_NUMBER_ID,
        tenant_id,
        company_id,
    )
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
    now = time.time()
    _evict_service_windows(now)
    key = _window_key(
        user_phone,
        phone_number_id or WHATSAPP_PHONE_NUMBER_ID,
        tenant_id,
        company_id,
    )
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
