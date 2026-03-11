"""Runtime model selection helpers with fallback support."""

from __future__ import annotations

import os

DEFAULT_LIVE_MODEL_ID = "gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_TEXT_MODEL_ID = "gemini-2.5-pro"
DEFAULT_TEXT_FALLBACK_MODEL_ID = "gemini-2.5-flash"
DEFAULT_VISION_MODEL_ID = "gemini-2.5-pro"
DEFAULT_TTS_MODEL_ID = "gemini-2.5-flash-preview-tts"


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def resolve_live_model_id() -> str:
    """Resolve the active live model with optional fallback toggle."""
    primary = os.getenv("LIVE_MODEL_ID", DEFAULT_LIVE_MODEL_ID).strip()
    fallback = os.getenv("LIVE_MODEL_FALLBACK", "").strip()
    use_fallback = _env_flag("LIVE_MODEL_USE_FALLBACK", "false")

    if use_fallback and fallback:
        return fallback
    if primary:
        return primary
    if fallback:
        return fallback
    return DEFAULT_LIVE_MODEL_ID


def resolve_text_model_id() -> str:
    """Resolve the model for text channels (WhatsApp, SMS).

    Text channels use Runner.run_async() which calls generateContent —
    requires a standard API model, not the Live API audio model.
    """
    return os.getenv("TEXT_MODEL_ID", DEFAULT_TEXT_MODEL_ID).strip() or DEFAULT_TEXT_MODEL_ID


def resolve_text_fallback_model_id() -> str:
    """Resolve fallback model for text channels when primary is unavailable."""
    return os.getenv("TEXT_FALLBACK_MODEL_ID", DEFAULT_TEXT_FALLBACK_MODEL_ID).strip() or DEFAULT_TEXT_FALLBACK_MODEL_ID


def resolve_tts_model_id() -> str:
    """TTS model for WhatsApp voice note replies."""
    return os.getenv("TTS_MODEL_ID", DEFAULT_TTS_MODEL_ID).strip() or DEFAULT_TTS_MODEL_ID


def resolve_vision_model_id() -> str:
    """Resolve the model for vision/analysis tools (image grading, device ID)."""
    return os.getenv("VISION_MODEL", DEFAULT_VISION_MODEL_ID).strip() or DEFAULT_VISION_MODEL_ID


def get_live_model_candidates() -> list[str]:
    """Return ordered model candidates for retry/fallback operations."""
    candidates: list[str] = []
    primary = os.getenv("LIVE_MODEL_ID", DEFAULT_LIVE_MODEL_ID).strip()
    fallback = os.getenv("LIVE_MODEL_FALLBACK", "").strip()

    if primary:
        candidates.append(primary)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    if not candidates:
        candidates.append(DEFAULT_LIVE_MODEL_ID)
    return candidates

