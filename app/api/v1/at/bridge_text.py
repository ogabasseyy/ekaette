"""Gemini Standard API text bridge for SMS and WhatsApp channels.

Reuses the _get_genai_client() pattern from app/tools/vision_tools.py:48-57.
"""

from __future__ import annotations

import logging
import os

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    """Get or create the GenAI client for Standard API calls."""
    global _genai_client
    if _genai_client is None:
        api_key = os.getenv("GOOGLE_API_KEY", "")
        _genai_client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version="v1alpha"),
        )
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


async def query_text(
    *,
    user_message: str,
    company_id: str = "ekaette-electronics",
    model: str = "gemini-3-flash-preview",
    channel: str = "sms",
) -> str:
    """Text → Gemini Standard API → response text. Supports SMS and WhatsApp channels."""
    cfg = _CHANNEL_CONFIG.get(channel, _CHANNEL_CONFIG["sms"])
    client = _get_genai_client()
    response = client.models.generate_content(
        model=model,
        contents=[user_message],
        config=types.GenerateContentConfig(
            system_instruction=(
                f"You are Ekaette, AI assistant for {company_id}. "
                f"{cfg['system_suffix']}"
            ),
            max_output_tokens=cfg["max_tokens"],
        ),
    )
    text = (response.text or "").strip()
    if not text:
        text = "Thanks for your message. How can I help you today?"
    return text[: cfg["max_chars"]]
