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

import asyncio
import hashlib
import logging
import re
from typing import Any

from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from google.genai.errors import APIError, ServerError

from app.tools.vision_tools import cache_latest_image, upload_to_cloud_storage

logger = logging.getLogger(__name__)

# ─── Channel limits ───────────────────────────────────────────

CHANNEL_LIMITS: dict[str, dict[str, int]] = {
    "whatsapp": {"max_chars": 4096},
    "sms": {"max_chars": 160},
    "default": {"max_chars": 4096},
}

_DEFAULT_FALLBACK = "Thanks for your message. How can I help you today?"
_TEXT_ASSISTANT_NAME_PATTERN = re.compile(r"\b(?:ehkaitay|eh[-\s]?kai[-\s]?tay)\b", re.IGNORECASE)


class ModelOverloadedError(Exception):
    """Raised when the model returns 503/overloaded so callers can retry with fallback."""


_DEFAULT_MEDIA_PROMPTS: dict[str, str] = {
    "image": "The customer sent this image. Analyze it and respond helpfully.",
    "video": "The customer sent a video of their device. Analyze the video and respond helpfully.",
    "audio": "The customer sent a voice note. Listen to what they said and respond helpfully.",
    "default": "The customer sent media. Analyze it and respond helpfully.",
}
_MAX_MEDIA_BYTES = 20 * 1024 * 1024  # 20 MB — generous limit for any channel


def _normalize_text_assistant_name(text: str, *, channel: str) -> str:
    """Keep phonetic pronunciation guidance out of written channel output."""
    normalized_channel = (channel or "").strip().lower()
    if normalized_channel not in {"whatsapp", "sms", "text"}:
        return text
    if not isinstance(text, str) or not text.strip():
        return text
    return _TEXT_ASSISTANT_NAME_PATTERN.sub("Ekaette", text)


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
        channel=channel,
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
                channel=channel,
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
        "text": _normalize_text_assistant_name(text, channel=channel)[:limit],
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
    context_prefix: str = "",
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
        channel=channel,
    )
    await _prime_session_media_state(
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        media_bytes=media_bytes,
        mime_type=mime_type,
    )

    customer_prompt = ""
    if caption and caption.strip():
        customer_prompt = caption.strip()
    else:
        media_category = mime_type.split("/")[0] if "/" in mime_type else "default"
        customer_prompt = _DEFAULT_MEDIA_PROMPTS.get(media_category, _DEFAULT_MEDIA_PROMPTS["default"])

    normalized_context_prefix = context_prefix.strip()
    if normalized_context_prefix:
        text_prompt = (
            f"{normalized_context_prefix}\n\n"
            f"Customer message about this media: {customer_prompt}"
        )
    else:
        text_prompt = customer_prompt

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
                channel=channel,
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
        "text": _normalize_text_assistant_name(text, channel=channel)[:limit],
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
    channel: str,
) -> str:
    """Get existing session or create a new one with bootstrapped state.

    Returns the resolved session_id (may differ if Vertex auto-generates IDs).
    """
    async def _load_company_state() -> dict[str, Any]:
        from app.configs import registry_enabled
        from app.configs.company_loader import (
            build_company_session_state,
            load_company_knowledge,
            load_company_profile,
        )
        from app.api.v1.admin import shared as admin_shared

        db = admin_shared.company_config_client or admin_shared.industry_config_client
        if db is None:
            raise RuntimeError("Company config DB client not initialized")

        if registry_enabled():
            company_profile, company_knowledge = await asyncio.gather(
                load_company_profile(db, company_id, tenant_id=tenant_id),
                load_company_knowledge(db, company_id, tenant_id=tenant_id),
            )
        else:
            company_profile, company_knowledge = await asyncio.gather(
                load_company_profile(db, company_id),
                load_company_knowledge(db, company_id),
            )

        return build_company_session_state(
            company_id=company_id,
            profile=company_profile,
            knowledge=company_knowledge,
        )

    async def _repair_existing_session_state(session_obj: Any) -> None:
        session_state = getattr(session_obj, "state", None)
        if not isinstance(session_state, dict):
            return

        missing_company_context = any(
            key not in session_state
            for key in (
                "app:company_name",
                "app:company_profile",
                "app:company_knowledge",
            )
        )
        if not missing_company_context:
            return

        try:
            state_updates = await _load_company_state()
        except Exception as exc:
            logger.error("Text session company bootstrap failed: %s", exc, exc_info=True)
            return

        try:
            await session_service.append_event(
                session=session_obj,
                event=Event(
                    author="system:session_state",
                    actions=EventActions(state_delta=state_updates),
                ),
            )
        except Exception:
            logger.debug("Text session state persistence via append_event skipped", exc_info=True)
        session_state.update(state_updates)

    session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
    )

    if session is not None:
        resolved_id = getattr(session, "id", session_id)
        await _repair_existing_session_state(session)
        return resolved_id if isinstance(resolved_id, str) and resolved_id else session_id

    initial_state: dict[str, Any] = {
        "app:tenant_id": tenant_id,
        "app:company_id": company_id,
        "app:channel": channel,
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
                from app.tools.global_lessons import aload_global_lessons

                global_lessons = await aload_global_lessons(
                    db, tenant_id=tenant_id, company_id=company_id,
                )
                if global_lessons:
                    initial_state["app:global_lessons"] = global_lessons
            except Exception as exc:
                logger.info("Global lessons load skipped: %s", exc)
    except Exception as exc:
        logger.debug("Registry state bootstrap skipped: %s", exc)

    try:
        initial_state.update(await _load_company_state())
    except Exception as exc:
        logger.error("Text session company bootstrap failed: %s", exc, exc_info=True)

    created = await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state=initial_state,
    )

    resolved_id = getattr(created, "id", session_id)
    return resolved_id if isinstance(resolved_id, str) and resolved_id else session_id


async def _prime_session_media_state(
    *,
    session_service: Any,
    app_name: str,
    user_id: str,
    session_id: str,
    media_bytes: bytes,
    mime_type: str,
) -> None:
    """Persist inbound media so vision tools can reload it within and across turns."""
    cache_latest_image(
        user_id=user_id,
        session_id=session_id,
        image_data=media_bytes,
        mime_type=mime_type,
    )

    state_updates: dict[str, Any] = {
        "temp:last_media_mime_type": mime_type,
    }
    try:
        upload_result = await upload_to_cloud_storage(
            image_data=media_bytes,
            mime_type=mime_type,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        logger.debug("Text media pre-upload failed", exc_info=True)
    else:
        gcs_uri = upload_result.get("gcs_uri")
        blob_path = upload_result.get("blob_path")
        if isinstance(gcs_uri, str) and gcs_uri:
            state_updates["temp:last_media_gcs_uri"] = gcs_uri
        if isinstance(blob_path, str) and blob_path:
            state_updates["temp:last_media_blob_path"] = blob_path

    try:
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        logger.debug("Text media session lookup failed", exc_info=True)
        return

    if session is None:
        return

    session_state = getattr(session, "state", None)
    if isinstance(session_state, dict):
        session_state.update(state_updates)

    try:
        await session_service.append_event(
            session=session,
            event=Event(
                author="system:session_media",
                actions=EventActions(state_delta=state_updates),
            ),
        )
    except Exception:
        logger.debug("Text media state persistence via append_event skipped", exc_info=True)


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

    cleaned_parts = [part.strip() for part in text_parts if isinstance(part, str) and part.strip()]
    return "\n\n".join(cleaned_parts) if cleaned_parts else _DEFAULT_FALLBACK
