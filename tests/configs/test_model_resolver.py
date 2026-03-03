"""Tests for live model resolver helpers."""

import pytest

try:
    from app.configs.model_resolver import (
        DEFAULT_LIVE_MODEL_ID,
        get_live_model_candidates,
        resolve_live_model_id,
    )
except ImportError:
    pytest.skip("app.configs.model_resolver not yet implemented", allow_module_level=True)


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
