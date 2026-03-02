"""TDD tests for AT idempotency and callback deduplication.

Covers: preflight/commit cycle, fingerprint mismatch, stale pending,
callback replay, and ordering safety.
"""

from __future__ import annotations

import time
import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _reset_idempotency_state():
    """Clear idempotency stores between tests."""
    from app.api.v1.at import idempotency as idem
    idem._store.clear()
    idem._callback_seen.clear()
    yield
    idem._store.clear()
    idem._callback_seen.clear()


# ── Idempotency Key Validation ──


class TestRequireIdempotencyKey:
    """Header extraction and validation."""

    def test_missing_key_raises_400(self) -> None:
        from app.api.v1.at.idempotency import require_idempotency_key
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {}
        with pytest.raises(HTTPException) as exc_info:
            require_idempotency_key(request)
        assert exc_info.value.status_code == 400

    def test_empty_key_raises_400(self) -> None:
        from app.api.v1.at.idempotency import require_idempotency_key
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"Idempotency-Key": "  "}
        with pytest.raises(HTTPException) as exc_info:
            require_idempotency_key(request)
        assert exc_info.value.status_code == 400

    def test_too_long_key_raises_400(self) -> None:
        from app.api.v1.at.idempotency import require_idempotency_key
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"Idempotency-Key": "x" * 300}
        with pytest.raises(HTTPException) as exc_info:
            require_idempotency_key(request)
        assert exc_info.value.status_code == 400

    def test_valid_key_returned(self) -> None:
        from app.api.v1.at.idempotency import require_idempotency_key
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"Idempotency-Key": "my-key-123"}
        assert require_idempotency_key(request) == "my-key-123"


# ── Preflight / Commit Cycle ──


class TestIdempotencyPreflight:
    """Idempotency preflight checks."""

    def test_first_request_returns_none(self) -> None:
        from app.api.v1.at.idempotency import idempotency_preflight

        result = idempotency_preflight(
            scope="test", tenant_id="public",
            idempotency_key="k1", payload={"to": "+234"},
        )
        assert result is None

    def test_replay_returns_cached_body(self) -> None:
        from app.api.v1.at.idempotency import idempotency_preflight, idempotency_commit

        idempotency_preflight(
            scope="test", tenant_id="public",
            idempotency_key="k2", payload={"to": "+234"},
        )
        idempotency_commit(
            scope="test", tenant_id="public",
            idempotency_key="k2", body={"status": "ok", "result": "queued"},
        )
        cached = idempotency_preflight(
            scope="test", tenant_id="public",
            idempotency_key="k2", payload={"to": "+234"},
        )
        assert cached == {"status": "ok", "result": "queued"}

    def test_different_payload_raises_409(self) -> None:
        from app.api.v1.at.idempotency import idempotency_preflight, idempotency_commit

        idempotency_preflight(
            scope="test", tenant_id="public",
            idempotency_key="k3", payload={"to": "+234"},
        )
        idempotency_commit(
            scope="test", tenant_id="public",
            idempotency_key="k3", body={"status": "ok"},
        )
        with pytest.raises(HTTPException) as exc_info:
            idempotency_preflight(
                scope="test", tenant_id="public",
                idempotency_key="k3", payload={"to": "+999"},  # different
            )
        assert exc_info.value.status_code == 409

    def test_pending_request_raises_409(self) -> None:
        from app.api.v1.at.idempotency import idempotency_preflight

        idempotency_preflight(
            scope="test", tenant_id="public",
            idempotency_key="k4", payload={"to": "+234"},
        )
        # Second request while first is still pending
        with pytest.raises(HTTPException) as exc_info:
            idempotency_preflight(
                scope="test", tenant_id="public",
                idempotency_key="k4", payload={"to": "+234"},
            )
        assert exc_info.value.status_code == 409
        assert "still being processed" in exc_info.value.detail

    def test_stale_pending_is_reclaimed(self) -> None:
        from app.api.v1.at.idempotency import idempotency_preflight, _store

        idempotency_preflight(
            scope="test", tenant_id="public",
            idempotency_key="k5", payload={"to": "+234"},
        )
        # Simulate stale pending by setting expires_at in the past
        store_key = "at:test:public:k5"
        _store[store_key]["expires_at"] = time.time() - 10

        # Should succeed (reclaim stale entry)
        result = idempotency_preflight(
            scope="test", tenant_id="public",
            idempotency_key="k5", payload={"to": "+234"},
        )
        assert result is None

    def test_different_scopes_are_independent(self) -> None:
        from app.api.v1.at.idempotency import idempotency_preflight, idempotency_commit

        # Same key in different scopes should be independent
        idempotency_preflight(
            scope="voice_call", tenant_id="public",
            idempotency_key="k6", payload={"to": "+234"},
        )
        idempotency_commit(
            scope="voice_call", tenant_id="public",
            idempotency_key="k6", body={"status": "ok"},
        )

        # Same key, different scope — should be treated as new
        result = idempotency_preflight(
            scope="voice_transfer", tenant_id="public",
            idempotency_key="k6", payload={"to": "+234"},
        )
        assert result is None  # New request in different scope


# ── Callback Deduplication ──


class TestCallbackDedup:
    """AT at-least-once callback delivery deduplication."""

    def test_first_callback_is_not_duplicate(self) -> None:
        from app.api.v1.at.idempotency import is_duplicate_callback

        assert is_duplicate_callback("session-1", "voice:1") is False

    def test_second_callback_is_duplicate(self) -> None:
        from app.api.v1.at.idempotency import is_duplicate_callback

        is_duplicate_callback("session-2", "voice:1")
        assert is_duplicate_callback("session-2", "voice:1") is True

    def test_different_events_for_same_session_are_independent(self) -> None:
        from app.api.v1.at.idempotency import is_duplicate_callback

        is_duplicate_callback("session-3", "voice:1")
        # Different event_key — should NOT be a duplicate
        assert is_duplicate_callback("session-3", "voice:0") is False

    def test_different_sessions_are_independent(self) -> None:
        from app.api.v1.at.idempotency import is_duplicate_callback

        is_duplicate_callback("session-4", "voice:1")
        assert is_duplicate_callback("session-5", "voice:1") is False

    def test_expired_callback_is_not_duplicate(self) -> None:
        from app.api.v1.at.idempotency import is_duplicate_callback, _callback_seen

        is_duplicate_callback("session-6", "voice:1")
        # Simulate expiration
        _callback_seen["session-6:voice:1"] = time.time() - 400
        # After expiry window, dedup check runs cleanup on next overflow
        # Force overflow to trigger cleanup
        for i in range(1001):
            _callback_seen[f"flood-{i}:x"] = time.time() - 400
        assert is_duplicate_callback("session-6", "voice:1") is False
