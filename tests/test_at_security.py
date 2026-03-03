"""TDD tests for AT webhook security: IP allowlist + rate limiting.

Red phase — write tests before implementation.
"""

from __future__ import annotations

from unittest.mock import patch
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


def _build_app(
    *,
    allowed_ips: set[str] | None = None,
    rate_limit: int = 30,
    rate_window: int = 60,
) -> FastAPI:
    """Build a minimal FastAPI app with AT security dependency for testing."""
    import app.api.v1.at.security as sec_mod
    verify_at_webhook = sec_mod.verify_at_webhook

    app = FastAPI()

    @app.post("/test-webhook")
    async def _webhook(_: None = Depends(verify_at_webhook)) -> dict:
        return {"ok": True}

    return app


# ── IP Allowlist Tests ──


class TestIPAllowlist:
    """Webhook source IP validation."""

    def test_reject_unknown_ip_when_allowlist_set(self) -> None:
        """Requests from IPs not in the allowlist get 403."""
        with patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", {"10.0.0.1", "10.0.0.2"}):
            app = _build_app()
            client = TestClient(app)
            resp = client.post("/test-webhook")
            assert resp.status_code == 403
            assert "source" in resp.json()["detail"].lower()

    def test_allow_known_ip_when_allowlist_set(self) -> None:
        """Requests from allowlisted IPs pass the guard."""
        # TestClient connects from 'testclient' host — we add it to allowlist
        with patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", {"testclient"}):
            app = _build_app()
            client = TestClient(app)
            resp = client.post("/test-webhook")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_allow_all_when_allowlist_empty(self) -> None:
        """Empty allowlist means no IP filtering (dev/sandbox mode)."""
        with patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()):
            app = _build_app()
            client = TestClient(app)
            resp = client.post("/test-webhook")
            assert resp.status_code == 200


# ── Rate Limiting Tests ──


class TestRateLimiting:
    """Endpoint-level rate limiting for AT webhooks."""

    def test_rate_limit_allows_under_threshold(self) -> None:
        """Requests under the rate limit threshold pass."""
        with (
            patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
            patch("app.api.v1.at.security.AT_RATE_LIMIT", 5),
            patch("app.api.v1.at.security.AT_RATE_WINDOW", 60),
        ):
            # Reset bucket state
            import app.api.v1.at.security as sec_mod
            sec_mod._at_rate_buckets.clear()
            sec_mod._at_last_prune = 0.0

            app = _build_app()
            client = TestClient(app)
            for _ in range(5):
                resp = client.post("/test-webhook")
                assert resp.status_code == 200

    def test_rate_limit_blocks_over_threshold(self) -> None:
        """Requests exceeding the rate limit get 429."""
        with (
            patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
            patch("app.api.v1.at.security.AT_RATE_LIMIT", 3),
            patch("app.api.v1.at.security.AT_RATE_WINDOW", 60),
        ):
            import app.api.v1.at.security as sec_mod
            sec_mod._at_rate_buckets.clear()
            sec_mod._at_last_prune = 0.0

            app = _build_app()
            client = TestClient(app)
            # First 3 should pass
            for _ in range(3):
                resp = client.post("/test-webhook")
                assert resp.status_code == 200
            # 4th should be blocked
            resp = client.post("/test-webhook")
            assert resp.status_code == 429
            assert "rate limit" in resp.json()["detail"].lower()


# ── Combined Security Tests ──


class TestCombinedSecurity:
    """IP check runs before rate limit check."""

    def test_ip_rejected_before_rate_limit_consumed(self) -> None:
        """A rejected IP should not consume rate limit budget."""
        with (
            patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", {"10.0.0.1"}),
            patch("app.api.v1.at.security.AT_RATE_LIMIT", 1),
        ):
            import app.api.v1.at.security as sec_mod
            sec_mod._at_rate_buckets.clear()
            sec_mod._at_last_prune = 0.0

            app = _build_app()
            client = TestClient(app)
            # This request from unknown IP should be 403, not consume budget
            resp = client.post("/test-webhook")
            assert resp.status_code == 403
            # Buckets should be empty (no budget consumed)
            assert len(sec_mod._at_rate_buckets) == 0
