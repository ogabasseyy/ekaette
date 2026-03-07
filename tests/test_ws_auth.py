"""Tests for WebSocket authentication token module."""

import time

import pytest

# Inline the secret for tests so we don't need to patch env vars at import time.
_TEST_SECRET = "test-ws-secret-for-unit-tests"


def _make_module():
    """Import the module fresh and configure with test secret."""
    from app.api.v1.public import ws_auth

    ws_auth._WS_TOKEN_SECRET = _TEST_SECRET
    ws_auth._used_jtis.clear()
    return ws_auth


class TestWsTokenCreation:
    """Test WS token creation."""

    def test_create_ws_token_returns_string(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", 300)
        assert isinstance(token, str)
        assert len(token) > 20

    def test_create_ws_token_contains_three_segments(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", 300)
        parts = token.split(".")
        assert len(parts) == 3, f"Expected header.payload.signature, got {len(parts)} parts"

    def test_create_ws_token_raises_without_secret(self):
        ws_auth = _make_module()
        ws_auth._WS_TOKEN_SECRET = ""
        with pytest.raises(ValueError, match="WS_TOKEN_SECRET"):
            ws_auth.create_ws_token("user1", "public", "acme", 300)


class TestWsTokenValidation:
    """Test WS token validation."""

    def test_validate_valid_token_returns_claims(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", 300)
        claims = ws_auth.validate_ws_token(token, expected_user_id="user1")
        assert claims is not None
        assert claims.sub == "user1"
        assert claims.tenant_id == "public"
        assert claims.company_id == "acme"

    def test_validate_token_preserves_caller_phone_claim(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token(
            "user1",
            "public",
            "acme",
            300,
            caller_phone="+2348012345678",
        )
        claims = ws_auth.validate_ws_token(token, expected_user_id="user1")
        assert claims is not None
        assert claims.caller_phone == "+2348012345678"

    def test_validate_expired_token_returns_none(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", ttl_seconds=-1)
        claims = ws_auth.validate_ws_token(token, expected_user_id="user1")
        assert claims is None

    def test_validate_wrong_user_returns_none(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", 300)
        claims = ws_auth.validate_ws_token(token, expected_user_id="user2")
        assert claims is None

    def test_validate_tampered_signature_returns_none(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", 300)
        # Tamper with the last segment
        parts = token.split(".")
        parts[2] = "tampered" + parts[2]
        tampered = ".".join(parts)
        claims = ws_auth.validate_ws_token(tampered, expected_user_id="user1")
        assert claims is None

    def test_validate_reused_jti_returns_none(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", 300)
        # First use should succeed
        claims1 = ws_auth.validate_ws_token(token, expected_user_id="user1")
        assert claims1 is not None
        # Second use of same token should fail (single-use)
        claims2 = ws_auth.validate_ws_token(token, expected_user_id="user1")
        assert claims2 is None

    def test_validate_malformed_token_returns_none(self):
        ws_auth = _make_module()
        assert ws_auth.validate_ws_token("not-a-token", expected_user_id="user1") is None
        assert ws_auth.validate_ws_token("", expected_user_id="user1") is None
        assert ws_auth.validate_ws_token("a.b", expected_user_id="user1") is None

    def test_validate_disabled_when_no_secret(self):
        ws_auth = _make_module()
        token = ws_auth.create_ws_token("user1", "public", "acme", 300)
        ws_auth._WS_TOKEN_SECRET = ""
        # With no secret, validation should return None (can't verify)
        claims = ws_auth.validate_ws_token(token, expected_user_id="user1")
        assert claims is None

    def test_prune_cleans_expired_jtis(self):
        ws_auth = _make_module()
        # Create and consume a token with very short TTL
        token = ws_auth.create_ws_token("user1", "public", "acme", ttl_seconds=1)
        ws_auth.validate_ws_token(token, expected_user_id="user1")
        assert len(ws_auth._used_jtis) == 1

        # Manually expire the JTI entry
        for jti in ws_auth._used_jtis:
            ws_auth._used_jtis[jti] = time.time() - 10

        # Prune should clean it
        ws_auth._prune_used_jtis()
        assert len(ws_auth._used_jtis) == 0
