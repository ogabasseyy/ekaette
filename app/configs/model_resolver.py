"""Runtime model selection helpers with fallback support."""

from __future__ import annotations

import os

DEFAULT_LIVE_MODEL_ID = "gemini-2.5-flash-native-audio-preview-12-2025"


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

