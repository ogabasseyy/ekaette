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
from google.genai.errors import APIError, ServerError

logger = logging.getLogger(__name__)

# ─── Channel limits ───────────────────────────────────────────

CHANNEL_LIMITS: dict[str, dict[str, int]] = {
    "whatsapp": {"max_chars": 4096},
    "sms": {"max_chars": 160},
    "default": {"max_chars": 4096},
}

_DEFAULT_FALLBACK = "Thanks for your message. How can I help you today?"


class ModelOverloadedError(Exception):
    """Raised when the model returns 503/overloaded so callers can retry with fallback."""


_DEFAULT_MEDIA_PROMPTS: dict[str, str] = {
    "image": "The customer sent this image. Analyze it and respond helpfully.",
    "video": "The customer sent a video of their device. Analyze the video and respond helpfully.",
    "audio": "The customer sent a voice note. Listen to what they said and respond helpfully.",
    "default": "The customer sent media. Analyze it and respond helpfully.",
}
_MAX_MEDIA_BYTES = 20 * 1024 * 1024  # 20 MB — generous limit for any channel


# ─── Session ID derivation ────────────────────────────────────


def derive_session_id(channel: str, user_id: str) -> str:
    """Derive a deterministic, safe session ID from channel + user.

    Session IDs are stable across messages so the same WhatsApp user
    maintains conversation continuity with full agent state.

    Raises ValueError if channel or user_id is empty/None.
    """
    if not channel or not channel.strip():
        raise ValueError("channel must be a non-empty string")
    if not user_id or not user_id.strip():
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
    fallback_runner: Any = None,
    fallback_app_name: str = "",
) -> dict[str, Any]:
    """Send a text message through the ADK agent graph and collect the response.

    If the primary runner's model is overloaded (503), automatically retries
    with fallback_runner when provided.

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

    try:
        text = await _run_and_collect_text(
            runner=runner,
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        )
    except ModelOverloadedError:
        if fallback_runner is not None:
            logger.info("Retrying with fallback text runner")
            fb_session_id = derive_session_id(channel, user_id)
            fb_app = fallback_app_name or f"{app_name}_fallback"
            fb_session_id = await _ensure_session(
                session_service=session_service,
                app_name=fb_app,
                user_id=user_id,
                session_id=fb_session_id,
                tenant_id=tenant_id,
                company_id=company_id,
            )
            text = await _run_and_collect_text(
                runner=fallback_runner,
                user_id=user_id,
                session_id=fb_session_id,
                new_message=content,
            )
        else:
            text = _DEFAULT_FALLBACK

    limit = CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["default"])["max_chars"]

    return {
        "text": text[:limit],
        "session_id": session_id,
        "channel": channel,
    }


# ─── Core: send media (image/video) ──────────────────────────


async def send_media_message(
    *,
    runner: Any,
    session_service: Any,
    app_name: str,
    user_id: str,
    media_bytes: bytes,
    mime_type: str = "image/jpeg",
    caption: str = "",
    channel: str = "whatsapp",
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
    fallback_runner: Any = None,
    fallback_app_name: str = "",
) -> dict[str, Any]:
    """Send image or video through the ADK agent graph and collect the response.

    If the primary runner's model is overloaded (503), automatically retries
    with fallback_runner when provided. Both gemini-3-flash and gemini-2.5-flash
    support image and video via inline_data.

    Returns:
        Dict with text, session_id, channel keys.
    """
    session_id = derive_session_id(channel, user_id)

    if not media_bytes:
        logger.warning("Empty media bytes received for session %s", session_id)
        return {
            "text": "Sorry, the media file appears to be empty. Please try sending it again.",
            "session_id": session_id,
            "channel": channel,
        }

    if len(media_bytes) > _MAX_MEDIA_BYTES:
        logger.warning("Media too large: %d bytes (limit %d)", len(media_bytes), _MAX_MEDIA_BYTES)
        return {
            "text": "Sorry, that file is too large to process. Please send a smaller one.",
            "session_id": session_id,
            "channel": channel,
        }

    session_id = await _ensure_session(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        company_id=company_id,
    )

    if caption and caption.strip():
        text_prompt = caption.strip()
    else:
        media_category = mime_type.split("/")[0] if "/" in mime_type else "default"
        text_prompt = _DEFAULT_MEDIA_PROMPTS.get(media_category, _DEFAULT_MEDIA_PROMPTS["default"])

    content = types.Content(
        parts=[
            types.Part(
                inline_data=types.Blob(
                    mime_type=mime_type,
                    data=media_bytes,
                )
            ),
            types.Part(text=text_prompt),
        ],
        role="user",
    )

    try:
        text = await _run_and_collect_text(
            runner=runner,
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        )
    except ModelOverloadedError:
        if fallback_runner is not None:
            logger.info("Retrying media with fallback text runner")
            fb_session_id = derive_session_id(channel, user_id)
            fb_app = fallback_app_name or f"{app_name}_fallback"
            fb_session_id = await _ensure_session(
                session_service=session_service,
                app_name=fb_app,
                user_id=user_id,
                session_id=fb_session_id,
                tenant_id=tenant_id,
                company_id=company_id,
            )
            text = await _run_and_collect_text(
                runner=fallback_runner,
                user_id=user_id,
                session_id=fb_session_id,
                new_message=content,
            )
        else:
            text = _DEFAULT_FALLBACK

    limit = CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["default"])["max_chars"]

    return {
        "text": text[:limit],
        "session_id": session_id,
        "channel": channel,
    }


async def send_image_message(
    *,
    image_bytes: bytes = b"",
    media_bytes: bytes = b"",
    **kwargs: Any,
) -> dict[str, Any]:
    """Backward-compat wrapper — accepts image_bytes or media_bytes."""
    return await send_media_message(
        media_bytes=media_bytes or image_bytes,
        **kwargs,
    )


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
        from app.api.v1.admin import shared as admin_shared

        if registry_enabled():
            db = admin_shared.company_config_client or admin_shared.industry_config_client
            if db is None:
                raise RuntimeError("Registry DB client not initialized")
            registry_config = await resolve_registry_config(
                db,
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

            # Load global lessons (Tier 2 learning)
            try:
                from app.tools.global_lessons import load_global_lessons

                global_lessons = load_global_lessons(
                    db, tenant_id=tenant_id, company_id=company_id,
                )
                if global_lessons:
                    initial_state["app:global_lessons"] = global_lessons
            except Exception as exc:
                logger.info("Global lessons load skipped: %s", exc)
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

    except ServerError as exc:
        logger.warning("Model overloaded, eligible for fallback: %s", exc)
        raise ModelOverloadedError(str(exc)) from exc
    except APIError as exc:
        logger.error("ADK runner API error: %s", exc, exc_info=True)
        return _DEFAULT_FALLBACK
    except Exception as exc:
        logger.error("ADK runner error: %s", exc, exc_info=True)
        return _DEFAULT_FALLBACK

    return "".join(text_parts) if text_parts else _DEFAULT_FALLBACK
