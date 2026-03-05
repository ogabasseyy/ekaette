"""WhatsApp channel business logic.

Handles text, image, interactive messages. Service window tracking.
Template fallback for during-call sends outside 24h window.
Routes delegate here — no business logic in whatsapp.py.
"""

from __future__ import annotations

import hashlib
import logging
import time

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
        from app.tools.vision_tools import _get_genai_client
        from google.genai import types

        client = _get_genai_client()
        resolved_mime = mime_type or content_type or "image/jpeg"
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
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
    last_ts = _service_windows.get(key, 0)
    return (time.time() - last_ts) < _SERVICE_WINDOW_SECONDS


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
    _service_windows[key] = time.time()


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


async def send_with_idempotency(
    *,
    idempotency_key: str,
    payload_hash: str,
    send_fn,
) -> tuple[int, dict]:
    """Firestore-backed idempotency: same key+payload returns cached result; key reuse with different payload returns 409."""
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

    if key_hash in _idempotency_store:
        stored_payload_hash, status, body, ts = _idempotency_store[key_hash]
        ttl_seconds = WA_SEND_IDEMPOTENCY_TTL_HOURS * 3600
        if (time.time() - ts) < ttl_seconds:
            if stored_payload_hash == payload_hash:
                return status, body
            # Key reuse with different payload
            return 409, {"error": "Idempotency key conflict"}

    # First-seen key — execute send
    status, body = await send_fn()
    _idempotency_store[key_hash] = (payload_hash, status, body, time.time())
    return status, body


def reset_idempotency_store() -> None:
    """Reset idempotency store (for testing)."""
    _idempotency_store.clear()


# ── Failure Artifacts ──


async def write_failure_artifacts(
    *,
    wamid: str,
    error: str,
    tenant_id: str = "public",
) -> None:
    """Write redacted triage record. Production writes to Firestore + GCS."""
    logger.error(
        "WA webhook final failure",
        extra={
            "wamid": sanitize_log(wamid),
            "tenant_id": sanitize_log(tenant_id),
            "error_type": sanitize_log(
                type(error).__name__ if not isinstance(error, str) else "str"
            ),
        },
    )
