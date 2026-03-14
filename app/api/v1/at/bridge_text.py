"""Gemini Standard API text bridge for SMS and WhatsApp channels.

Reuses the _get_genai_client() pattern from app/tools/vision_tools.py:48-57.
"""

from __future__ import annotations

import asyncio
import logging
import re

from google import genai
from google.genai import types

from app.configs.model_resolver import resolve_text_model_id
from app.genai_clients import build_genai_client

logger = logging.getLogger(__name__)

_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    """Get or create the GenAI client for Standard API calls."""
    global _genai_client
    if _genai_client is None:
        _genai_client = build_genai_client(api_version="v1alpha")
    return _genai_client


_CHANNEL_CONFIG = {
    "sms": {
        "max_chars": 160,
        "max_tokens": 64,
        "system_suffix": "Respond concisely in under 160 characters for SMS.",
    },
    "whatsapp": {
        "max_chars": 4096,
        "max_tokens": 1024,
        "system_suffix": (
            "You are chatting on WhatsApp. Be warm, polite, and professional. "
            "Reply in 1-3 short sentences. When greeting or offering help, say "
            "'How may I help you?' or 'How may I be of service?' — never "
            "'let me know if you need help'. No bullet points or long lists "
            "unless asked. Focus on concrete business tasks like trade-ins, "
            "bookings, catalog, and support."
        ),
    },
}

_TEXT_ASSISTANT_NAME_PATTERN = re.compile(r"\b(?:ehkaitay|eh[-\s]?kai[-\s]?tay)\b", re.IGNORECASE)


def _normalize_text_assistant_name(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text
    return _TEXT_ASSISTANT_NAME_PATTERN.sub("Ekaette", text)


async def query_text(
    *,
    user_message: str,
    company_id: str = "ekaette-electronics",
    model: str | None = None,
    channel: str = "sms",
) -> str:
    """Text → Gemini Standard API → response text. Supports SMS and WhatsApp channels."""
    cfg = _CHANNEL_CONFIG.get(channel, _CHANNEL_CONFIG["sms"])
    client = _get_genai_client()
    resolved_model = (model or "").strip() or resolve_text_model_id()
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=resolved_model,
        contents=[user_message],
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are the virtual assistant whose spoken name is pronounced 'eh-KAI-tay'. "
                "When writing in text, spell your assistant name exactly as 'Ekaette'. "
                "Never type the phonetic spelling 'ehkaitay' or 'eh-KAI-tay'. "
                "The middle syllable is exactly 'kai', rhyming with 'sky' when spoken. "
                "Do not mention internal business IDs, slugs, or platform names. "
                f"{cfg['system_suffix']}"
            ),
            max_output_tokens=cfg["max_tokens"],
        ),
    )
    text = _normalize_text_assistant_name((response.text or "").strip())
    if not text:
        text = "Thanks for your message. How can I help you today?"
    return text[: cfg["max_chars"]]
