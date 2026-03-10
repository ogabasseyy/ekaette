"""Tests for shared outbound callback hints."""

from __future__ import annotations

from shared.outbound_callback_hints import (
    consume_outbound_callback_hint,
    mark_outbound_callback_hint,
    reset_outbound_callback_hints,
)


def setup_function() -> None:
    reset_outbound_callback_hints()


def teardown_function() -> None:
    reset_outbound_callback_hints()


def test_hint_is_consumed_once(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)

    mark_outbound_callback_hint(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
        ttl_seconds=30.0,
    )

    assert consume_outbound_callback_hint(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
    ) is True
    assert consume_outbound_callback_hint(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
    ) is False
