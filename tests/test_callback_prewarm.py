"""Tests for shared callback prewarm reservations."""

from __future__ import annotations

from shared.callback_prewarm import (
    clear_callback_prewarm,
    get_callback_prewarm,
    request_callback_prewarm,
    reset_callback_prewarms,
    update_callback_prewarm_status,
)


def setup_function() -> None:
    reset_callback_prewarms()


def teardown_function() -> None:
    reset_callback_prewarms()


def test_callback_prewarm_local_lifecycle(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)

    created = request_callback_prewarm(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
        ttl_seconds=30.0,
    )
    assert created["status"] == "pending"

    fetched = get_callback_prewarm(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
    )
    assert fetched is not None
    assert fetched["status"] == "pending"

    update_callback_prewarm_status(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
        status="ready",
        detail="Warm session ready",
    )
    updated = get_callback_prewarm(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
    )
    assert updated is not None
    assert updated["status"] == "ready"
    assert updated["detail"] == "Warm session ready"

    clear_callback_prewarm(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
    )
    assert get_callback_prewarm(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
    ) is None


def test_callback_prewarm_firestore_read_overrides_stale_local(monkeypatch):
    request_callback_prewarm(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
        ttl_seconds=30.0,
    )

    class _FakeSnap:
        exists = True

        def to_dict(self):
            return {
                "key": "public:ekaette-electronics:+2348012345678",
                "tenant_id": "public",
                "company_id": "ekaette-electronics",
                "phone": "+2348012345678",
                "status": "ready",
                "detail": "Remote VM ready",
                "requested_at": 1.0,
                "updated_at": 2.0,
                "expires_at": 9999999999.0,
            }

    class _FakeDocRef:
        def get(self):
            return _FakeSnap()

    monkeypatch.setattr("shared.callback_prewarm._uses_firestore", lambda: True)
    monkeypatch.setattr("shared.callback_prewarm._reservation_doc_ref", lambda key: _FakeDocRef())

    fetched = get_callback_prewarm(
        tenant_id="public",
        company_id="ekaette-electronics",
        phone="+2348012345678",
    )
    assert fetched is not None
    assert fetched["status"] == "ready"
    assert fetched["detail"] == "Remote VM ready"
