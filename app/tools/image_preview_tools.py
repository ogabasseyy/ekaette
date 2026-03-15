"""Image preview generation tools for upsell flows."""

from __future__ import annotations

import logging
import os
from typing import Any

from google import genai
from google.genai import types

from app.configs.model_resolver import (
    get_image_generation_model_candidates,
    resolve_image_generation_model_id,
)
from app.genai_clients import build_genai_client
from app.tools.wa_messaging import send_whatsapp_image_message

logger = logging.getLogger(__name__)

IMAGE_PREVIEW_MODEL = resolve_image_generation_model_id()
_image_client: genai.Client | None = None


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def image_preview_enabled() -> bool:
    """Return True when the booking upsell image preview feature is enabled."""
    return _env_flag("UPSELL_IMAGE_PREVIEW_ENABLED", "false")


def _get_image_client() -> genai.Client:
    global _image_client
    if _image_client is None:
        image_location = os.getenv("IMAGE_GENERATION_MODEL_LOCATION", "").strip() or None
        _image_client = build_genai_client(
            api_version="v1",
            location=image_location,
        )
    return _image_client


def _image_model_candidates() -> list[str]:
    candidates = [IMAGE_PREVIEW_MODEL]
    for candidate in get_image_generation_model_candidates():
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _preview_prompt(
    *,
    device_name: str,
    case_color: str,
    case_style: str,
    phone_color: str,
    prompt_hint: str,
) -> str:
    visible_phone_color = phone_color.strip() or "realistic"
    extra_hint = prompt_hint.strip()
    lines = [
        "Create a premium ecommerce-style product preview image.",
        f"Subject: {device_name} smartphone with a {case_color} {case_style}.",
        f"Show the phone in a clean studio setup with a {visible_phone_color} phone body where visible.",
        "Render one phone only.",
        "Make the case clearly visible and fitted properly to the phone.",
        "Use realistic lighting, crisp product-detail focus, and a plain uncluttered background.",
        "No text, no watermark, no logo, no hands, no people, no box, no extra accessories.",
        "The result should look like a clean product mockup a retailer could send to a customer on WhatsApp.",
    ]
    if extra_hint:
        lines.append(f"Additional guidance: {extra_hint}")
    return " ".join(lines)


def _extract_generated_image(response: Any) -> tuple[bytes, str] | None:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            mime_type = getattr(inline_data, "mime_type", None)
            if isinstance(data, bytes) and data:
                return data, str(mime_type or "image/png")
    return None


def _extract_text_response(response: Any) -> str:
    text_parts: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
    return " ".join(text_parts).strip()


def _is_model_unavailable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {403, 404, 429}:
        return True
    message = f"{exc}".lower()
    return (
        "not found" in message
        or "does not have access" in message
        or "publisher model" in message
        or "quota exceeded" in message
        or "rate limit" in message
    )


async def generate_case_preview_via_whatsapp(
    device_name: str,
    case_color: str,
    tool_context,
    *,
    case_style: str = "phone case",
    phone_color: str = "",
    prompt_hint: str = "",
) -> dict[str, Any]:
    """Generate a case mockup for the chosen phone and send it to WhatsApp.

    Use only during the upsell/booking stage after the customer explicitly asks
    to see a preview or clearly agrees to receive one on WhatsApp.
    """
    if not image_preview_enabled():
        return {
            "status": "error",
            "detail": "Upsell image previews are not enabled for this environment.",
        }
    if not device_name.strip():
        return {"status": "error", "detail": "device_name is required"}
    if not case_color.strip():
        return {"status": "error", "detail": "case_color is required"}

    prompt = _preview_prompt(
        device_name=device_name.strip(),
        case_color=case_color.strip(),
        case_style=case_style.strip() or "phone case",
        phone_color=phone_color,
        prompt_hint=prompt_hint,
    )

    client = _get_image_client()
    response = None
    last_exc: Exception | None = None
    used_model = IMAGE_PREVIEW_MODEL
    for model_name in _image_model_candidates():
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                        output_mime_type="image/png",
                    ),
                ),
            )
            if response is not None:
                used_model = model_name
                break
        except Exception as exc:
            last_exc = exc
            if _is_model_unavailable_error(exc):
                logger.warning("Image generation model unavailable: %s", model_name)
                continue
            logger.warning("Image generation failed for model=%s", model_name, exc_info=True)
            break

    if response is None:
        detail = "Image generation failed"
        if last_exc is not None:
            detail = f"Image generation failed: {last_exc}"
        return {"status": "error", "detail": detail}

    generated = _extract_generated_image(response)
    if generated is None:
        logger.warning("Image generation response contained no inline image data")
        response_text = _extract_text_response(response)
        detail = "Image generation returned no image"
        if response_text:
            detail = f"{detail}: {response_text[:200]}"
        return {"status": "error", "detail": detail}

    image_bytes, mime_type = generated
    caption = (
        f"Here is a preview of how a {case_color.strip()} {case_style.strip() or 'phone case'} "
        f"could look on the {device_name.strip()}."
    )
    wa_result = await send_whatsapp_image_message(
        media_bytes=image_bytes,
        mime_type=mime_type,
        caption=caption,
        tool_context=tool_context,
        idempotency_namespace="generate_case_preview_via_whatsapp",
    )
    if str(wa_result.get("status", "")).strip().lower() != "sent":
        return wa_result

    return {
        "status": "sent",
        "message_id": wa_result.get("message_id", ""),
        "caption": caption,
        "model": used_model,
        "mime_type": mime_type,
    }
