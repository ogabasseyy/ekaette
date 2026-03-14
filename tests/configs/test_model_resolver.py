"""Tests for live model resolver helpers."""

from app.configs.model_resolver import (
    DEFAULT_LIVE_MEDIA_ANALYSIS_MODEL_ID,
    DEFAULT_LIVE_MODEL_ID,
    DEFAULT_VISION_FALLBACK_MODEL_ID,
    DEFAULT_VISION_MODEL_ID,
    get_live_media_vision_model_candidates,
    get_vision_model_candidates,
    get_live_model_candidates,
    resolve_live_model_id,
)


def test_resolve_live_model_uses_primary_by_default(monkeypatch):
    monkeypatch.setenv("LIVE_MODEL_ID", "model-primary")
    monkeypatch.delenv("LIVE_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("LIVE_MODEL_USE_FALLBACK", raising=False)
    assert resolve_live_model_id() == "model-primary"


def test_resolve_live_model_uses_fallback_when_enabled(monkeypatch):
    monkeypatch.setenv("LIVE_MODEL_ID", "model-primary")
    monkeypatch.setenv("LIVE_MODEL_FALLBACK", "model-fallback")
    monkeypatch.setenv("LIVE_MODEL_USE_FALLBACK", "true")
    assert resolve_live_model_id() == "model-fallback"


def test_get_live_model_candidates_dedupes_primary_and_fallback(monkeypatch):
    monkeypatch.setenv("LIVE_MODEL_ID", "model-primary")
    monkeypatch.setenv("LIVE_MODEL_FALLBACK", "model-fallback")
    assert get_live_model_candidates() == ["model-primary", "model-fallback"]


def test_get_live_model_candidates_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("LIVE_MODEL_ID", raising=False)
    monkeypatch.delenv("LIVE_MODEL_FALLBACK", raising=False)
    assert get_live_model_candidates() == [DEFAULT_LIVE_MODEL_ID]


# ─── Text model resolver ───


def test_resolve_text_model_defaults_to_2_5_pro(monkeypatch):
    from app.configs.model_resolver import DEFAULT_TEXT_MODEL_ID, resolve_text_model_id

    monkeypatch.delenv("TEXT_MODEL_ID", raising=False)
    result = resolve_text_model_id()
    assert result == DEFAULT_TEXT_MODEL_ID
    assert result == "gemini-2.5-pro"


def test_resolve_text_model_reads_env(monkeypatch):
    from app.configs.model_resolver import resolve_text_model_id

    monkeypatch.setenv("TEXT_MODEL_ID", "gemini-2.5-flash")
    assert resolve_text_model_id() == "gemini-2.5-flash"


# ─── TTS model resolver ───


def test_resolve_tts_model_default(monkeypatch):
    from app.configs.model_resolver import DEFAULT_TTS_MODEL_ID, resolve_tts_model_id

    monkeypatch.delenv("TTS_MODEL_ID", raising=False)
    result = resolve_tts_model_id()
    assert result == DEFAULT_TTS_MODEL_ID
    assert result == "gemini-2.5-flash-tts"


def test_resolve_tts_model_env_override(monkeypatch):
    from app.configs.model_resolver import resolve_tts_model_id

    monkeypatch.setenv("TTS_MODEL_ID", "gemini-2.5-flash-tts")
    assert resolve_tts_model_id() == "gemini-2.5-flash-tts"


def test_resolve_tts_model_strips_whitespace(monkeypatch):
    from app.configs.model_resolver import resolve_tts_model_id

    monkeypatch.setenv("TTS_MODEL_ID", "  gemini-2.5-flash-tts  ")
    assert resolve_tts_model_id() == "gemini-2.5-flash-tts"


# ─── Vision model resolver ───


def test_get_vision_model_candidates_prefers_primary_and_fallback(monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "gemini-custom-primary")
    monkeypatch.setenv("VISION_MODEL_FALLBACK", "gemini-custom-fallback")
    monkeypatch.setenv("VISION_MODEL_CANDIDATES", " gemini-extra-a , gemini-extra-b ")

    assert get_vision_model_candidates() == [
        "gemini-custom-primary",
        "gemini-custom-fallback",
        "gemini-extra-a",
        "gemini-extra-b",
        DEFAULT_VISION_MODEL_ID,
        DEFAULT_VISION_FALLBACK_MODEL_ID,
    ]


def test_get_vision_model_candidates_falls_back_to_stable_defaults(monkeypatch):
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("VISION_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("VISION_MODEL_CANDIDATES", raising=False)

    assert get_vision_model_candidates() == [
        DEFAULT_VISION_MODEL_ID,
        DEFAULT_VISION_FALLBACK_MODEL_ID,
    ]


def test_get_vision_model_candidates_dedupes_primary_and_fallback(monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "gemini-custom-primary")
    monkeypatch.setenv("VISION_MODEL_FALLBACK", "gemini-custom-primary")
    monkeypatch.delenv("VISION_MODEL_CANDIDATES", raising=False)

    assert get_vision_model_candidates() == [
        "gemini-custom-primary",
        DEFAULT_VISION_MODEL_ID,
        DEFAULT_VISION_FALLBACK_MODEL_ID,
    ]


def test_get_live_media_vision_model_candidates_prefers_dedicated_override(monkeypatch):
    monkeypatch.setenv("LIVE_MEDIA_ANALYSIS_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("LIVE_MEDIA_ANALYSIS_MODEL_FALLBACK", "gemini-3.1-pro-preview")
    monkeypatch.setenv(
        "LIVE_MEDIA_ANALYSIS_MODEL_CANDIDATES",
        " gemini-extra-a , gemini-extra-b ",
    )

    assert get_live_media_vision_model_candidates() == [
        "gemini-2.5-pro",
        "gemini-3.1-pro-preview",
        "gemini-extra-a",
        "gemini-extra-b",
    ]


def test_get_live_media_vision_model_candidates_defaults_to_2_5_pro(monkeypatch):
    monkeypatch.delenv("LIVE_MEDIA_ANALYSIS_MODEL", raising=False)
    monkeypatch.delenv("LIVE_MEDIA_ANALYSIS_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("LIVE_MEDIA_ANALYSIS_MODEL_CANDIDATES", raising=False)

    assert get_live_media_vision_model_candidates() == [
        DEFAULT_LIVE_MEDIA_ANALYSIS_MODEL_ID,
    ]
