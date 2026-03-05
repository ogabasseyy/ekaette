"""ADK text channel adapter — unified text/image routing through the agent graph.

This module replaces direct Gemini calls (bridge_text.py) with proper ADK Runner
integration. All text channels (WhatsApp, SMS, future) route through here to get
full access to the multi-agent hierarchy, tools, session state, and memory.

Usage:
    result = await send_text_message(
        runner=runner,
        session_service=session_service,
        app_name="ekaette",
        user_id="wa_2348001234567",
        message_text="I want to swap my iPhone",
        channel="whatsapp",
    )
    reply = result["text"]
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from google.genai import types

logger = logging.getLogger(__name__)

# ─── Channel limits ───────────────────────────────────────────

CHANNEL_LIMITS: dict[str, dict[str, int]] = {
    "whatsapp": {"max_chars": 4096},
    "sms": {"max_chars": 160},
    "default": {"max_chars": 4096},
}

_DEFAULT_FALLBACK = "Thanks for your message. How can I help you today?"
_DEFAULT_IMAGE_PROMPT = "The customer sent this image. Analyze it and respond helpfully."
_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB — generous limit for any channel


# ─── Session ID derivation ────────────────────────────────────


def derive_session_id(channel: str, user_id: str) -> str:
    """Derive a deterministic, safe session ID from channel + user.

    Session IDs are stable across messages so the same WhatsApp user
    maintains conversation continuity with full agent state.

    Raises ValueError if channel or user_id is empty/None.
    """
    if not channel:
        raise ValueError("channel must be a non-empty string")
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    raw = f"{channel}:{user_id}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"{channel}-{digest}"


# ─── Core: send text ──────────────────────────────────────────


async def send_text_message(
    *,
    runner: Any,
    session_service: Any,
    app_name: str,
    user_id: str,
    message_text: str,
    channel: str = "whatsapp",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> dict[str, Any]:
    """Send a text message through the ADK agent graph and collect the response.

    Args:
        runner: ADK Runner instance.
        session_service: ADK session service for get/create sessions.
        app_name: ADK app name (e.g. "ekaette").
        user_id: Channel-specific user identifier (e.g. phone number).
        message_text: The user's text message.
        channel: Channel name ("whatsapp", "sms", etc.).
        tenant_id: Multi-tenant scoping.
        company_id: Company scoping.

    Returns:
        Dict with text, session_id, channel keys.
    """
    session_id = derive_session_id(channel, user_id)

    session_id = await _ensure_session(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        company_id=company_id,
    )

    content = types.Content(
        parts=[types.Part(text=message_text)],
        role="user",
    )

    text = await _run_and_collect_text(
        runner=runner,
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    )

    limit = CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["default"])["max_chars"]

    return {
        "text": text[:limit],
        "session_id": session_id,
        "channel": channel,
    }


# ─── Core: send image ────────────────────────────────────────


async def send_image_message(
    *,
    runner: Any,
    session_service: Any,
    app_name: str,
    user_id: str,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    caption: str = "",
    channel: str = "whatsapp",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> dict[str, Any]:
    """Send an image through the ADK agent graph and collect the response.

    The image is packaged as inline data in the Content message alongside
    the caption (or a default prompt). This lets the root agent route to
    vision_agent for analysis, then valuation_agent for pricing.

    Args:
        runner: ADK Runner instance.
        session_service: ADK session service.
        app_name: ADK app name.
        user_id: Channel-specific user identifier.
        image_bytes: Raw image data.
        mime_type: Image MIME type.
        caption: Optional user caption/instruction.
        channel: Channel name.
        tenant_id: Multi-tenant scoping.
        company_id: Company scoping.

    Returns:
        Dict with text, session_id, channel keys.
    """
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        logger.warning("Image too large: %d bytes (limit %d)", len(image_bytes), _MAX_IMAGE_BYTES)
        return {
            "text": "Sorry, that image is too large to process. Please send a smaller image.",
            "session_id": derive_session_id(channel, user_id),
            "channel": channel,
        }

    session_id = derive_session_id(channel, user_id)

    session_id = await _ensure_session(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        company_id=company_id,
    )

    text_prompt = caption.strip() if caption and caption.strip() else _DEFAULT_IMAGE_PROMPT

    content = types.Content(
        parts=[
            types.Part(
                inline_data=types.Blob(
                    mime_type=mime_type,
                    data=image_bytes,
                )
            ),
            types.Part(text=text_prompt),
        ],
        role="user",
    )

    text = await _run_and_collect_text(
        runner=runner,
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    )

    limit = CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["default"])["max_chars"]

    return {
        "text": text[:limit],
        "session_id": session_id,
        "channel": channel,
    }


# ─── Internals ────────────────────────────────────────────────


async def _ensure_session(
    *,
    session_service: Any,
    app_name: str,
    user_id: str,
    session_id: str,
    tenant_id: str,
    company_id: str,
) -> str:
    """Get existing session or create a new one with bootstrapped state.

    Returns the resolved session_id (may differ if Vertex auto-generates IDs).
    """
    session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
    )

    if session is not None:
        resolved_id = getattr(session, "id", session_id)
        return resolved_id if isinstance(resolved_id, str) and resolved_id else session_id

    initial_state: dict[str, Any] = {
        "app:tenant_id": tenant_id,
        "app:company_id": company_id,
    }

    # Load registry config if available
    try:
        from app.configs import registry_enabled
        from app.configs.registry_loader import resolve_registry_config

        if registry_enabled():
            registry_config = await resolve_registry_config(
                tenant_id=tenant_id,
                company_id=company_id,
            )
            if registry_config is not None:
                industry = getattr(registry_config, "industry_template_id", "electronics")
                initial_state["app:industry"] = industry
                initial_state["app:industry_template_id"] = industry
                caps = getattr(registry_config, "capabilities", None)
                if caps:
                    initial_state["app:capabilities"] = caps
                version = getattr(registry_config, "registry_version", None)
                if version:
                    initial_state["app:registry_version"] = version
    except Exception as exc:
        logger.debug("Registry state bootstrap skipped: %s", exc)

    created = await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state=initial_state,
    )

    resolved_id = getattr(created, "id", session_id)
    return resolved_id if isinstance(resolved_id, str) and resolved_id else session_id


async def _run_and_collect_text(
    *,
    runner: Any,
    user_id: str,
    session_id: str,
    new_message: types.Content,
) -> str:
    """Run the ADK agent graph and collect text responses.

    Iterates over runner.run_async() events, collecting only final (non-partial)
    text parts. Skips audio/image events.
    """
    text_parts: list[str] = []

    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
        ):
            if getattr(event, "is_partial", False):
                continue

            content = getattr(event, "content", None)
            if content is None:
                continue

            for part in getattr(content, "parts", []):
                if getattr(part, "text", None):
                    text_parts.append(part.text)

    except Exception as exc:
        logger.error("ADK runner error: %s", exc, exc_info=True)
        return _DEFAULT_FALLBACK

    return "".join(text_parts) if text_parts else _DEFAULT_FALLBACK
