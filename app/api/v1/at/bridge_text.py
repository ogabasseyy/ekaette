"""Gemini Standard API text bridge for SMS channel.

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
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not configured")
        _genai_client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version="v1alpha"),
        )
    return _genai_client


async def query_text(
    *,
    user_message: str,
    company_id: str = "ekaette-electronics",
    model: str = "gemini-3-flash-preview",
) -> str:
    """Text → Gemini Standard API → response text. For SMS only."""
    client = _get_genai_client()
    response = client.models.generate_content(
        model=model,
        contents=[user_message],
        config=types.GenerateContentConfig(
            system_instruction=(
                f"You are Ekaette, AI assistant for {company_id}. "
                "Respond concisely in under 160 characters for SMS."
            ),
            max_output_tokens=64,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        text = "Thanks for your message. How can I help you today?"
    return text[:160]
