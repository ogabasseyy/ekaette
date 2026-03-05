"""Tests for WhatsApp provider URL validation helpers."""

from __future__ import annotations

from app.api.v1.at import providers


def test_allowed_download_url_requires_https() -> None:
    assert providers._is_allowed_download_url("https://lookaside.fbsbx.com/media.bin") is True
    assert providers._is_allowed_download_url("http://lookaside.fbsbx.com/media.bin") is False


def test_allowed_download_url_rejects_unknown_host() -> None:
    assert providers._is_allowed_download_url("https://example.com/media.bin") is False


def test_allowed_download_url_rejects_missing_host() -> None:
    assert providers._is_allowed_download_url("https:///media.bin") is False
