"""Tests for SIP digest authentication (RFC 2617 + RFC 7616).

TDD Red phase — these tests should FAIL until sip_auth.py is implemented.
"""

from __future__ import annotations

import pytest


class TestParseChallenge:
    """Parse WWW-Authenticate / Proxy-Authenticate headers."""

    def test_parse_407_proxy_authenticate(self):
        from sip_bridge.sip_auth import parse_challenge

        header = 'Proxy-Authenticate: Digest realm="wa.meta.vc", nonce="abc123", algorithm=MD5, qop="auth"'
        result = parse_challenge(header)
        assert result["realm"] == "wa.meta.vc"
        assert result["nonce"] == "abc123"
        assert result["algorithm"] == "MD5"
        assert result["qop"] == "auth"

    def test_parse_401_www_authenticate(self):
        from sip_bridge.sip_auth import parse_challenge

        header = 'WWW-Authenticate: Digest realm="example.com", nonce="xyz789", algorithm=SHA-256'
        result = parse_challenge(header)
        assert result["realm"] == "example.com"
        assert result["nonce"] == "xyz789"
        assert result["algorithm"] == "SHA-256"

    def test_parse_missing_algorithm_defaults_md5(self):
        from sip_bridge.sip_auth import parse_challenge

        header = 'Proxy-Authenticate: Digest realm="test", nonce="n1"'
        result = parse_challenge(header)
        assert result["algorithm"] == "MD5"

    def test_parse_with_opaque(self):
        from sip_bridge.sip_auth import parse_challenge

        header = 'Proxy-Authenticate: Digest realm="test", nonce="n1", opaque="opq"'
        result = parse_challenge(header)
        assert result.get("opaque") == "opq"

    def test_parse_invalid_header_raises(self):
        from sip_bridge.sip_auth import AuthParseError, parse_challenge

        with pytest.raises(AuthParseError):
            parse_challenge("Not-A-Digest-Header: Basic realm=test")

    def test_parse_multi_value_qop(self):
        """Challenges often send qop="auth,auth-int" — parser must preserve it."""
        from sip_bridge.sip_auth import parse_challenge

        header = 'Proxy-Authenticate: Digest realm="test", nonce="n1", qop="auth,auth-int"'
        result = parse_challenge(header)
        assert result["qop"] == "auth,auth-int"


class TestDigestResponse:
    """Generate digest auth response headers."""

    def test_md5_known_answer(self):
        """RFC 2617 §3.5 known-answer test."""
        from sip_bridge.sip_auth import compute_digest_response

        result = compute_digest_response(
            username="Mufasa",
            realm="testrealm@host.com",
            password="Circle Of Life",
            nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093",
            method="GET",
            uri="/dir/index.html",
            algorithm="MD5",
            qop="auth",
            nc="00000001",
            cnonce="0a4f113b",
        )
        # RFC 2617 expected response
        assert result == "6629fae49393a05397450978507c4ef1"

    def test_sha256_response(self):
        """RFC 7616 SHA-256 digest."""
        from sip_bridge.sip_auth import compute_digest_response

        result = compute_digest_response(
            username="testuser",
            realm="wa.meta.vc",
            password="testpass",
            nonce="testnonce",
            method="REGISTER",
            uri="sip:wa.meta.vc",
            algorithm="SHA-256",
            qop="auth",
            nc="00000001",
            cnonce="testcnonce",
        )
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest is 64 chars

    def test_md5_without_qop(self):
        """MD5 without qop (legacy RFC 2069 compatibility)."""
        from sip_bridge.sip_auth import compute_digest_response

        result = compute_digest_response(
            username="user",
            realm="realm",
            password="pass",
            nonce="nonce1",
            method="INVITE",
            uri="sip:user@example.com",
            algorithm="MD5",
        )
        assert isinstance(result, str)
        assert len(result) == 32  # MD5 hex

    def test_md5_sess_differs_from_md5(self):
        """MD5-sess HA1 = H(H(user:realm:pass):nonce:cnonce) per RFC 2617."""
        from sip_bridge.sip_auth import compute_digest_response

        common = dict(
            username="user",
            realm="realm",
            password="pass",
            nonce="nonce1",
            method="INVITE",
            uri="sip:user@example.com",
            qop="auth",
            nc="00000001",
            cnonce="testcnonce",
        )
        md5_result = compute_digest_response(**common, algorithm="MD5")
        sess_result = compute_digest_response(**common, algorithm="MD5-sess")
        # They MUST differ because -sess incorporates nonce+cnonce into HA1
        assert md5_result != sess_result
        assert len(sess_result) == 32  # Still MD5 hex

    def test_sha256_sess_differs_from_sha256(self):
        """SHA-256-sess HA1 = H(H(user:realm:pass):nonce:cnonce) per RFC 7616."""
        from sip_bridge.sip_auth import compute_digest_response

        common = dict(
            username="user",
            realm="realm",
            password="pass",
            nonce="nonce1",
            method="INVITE",
            uri="sip:user@example.com",
            qop="auth",
            nc="00000001",
            cnonce="testcnonce",
        )
        sha_result = compute_digest_response(**common, algorithm="SHA-256")
        sess_result = compute_digest_response(**common, algorithm="SHA-256-sess")
        assert sha_result != sess_result
        assert len(sess_result) == 64  # SHA-256 hex

    def test_md5_sess_requires_qop(self):
        """RFC 2617: -sess algorithms require qop (nc+cnonce needed for HA1)."""
        from sip_bridge.sip_auth import compute_digest_response

        with pytest.raises(ValueError, match="nc and cnonce required"):
            compute_digest_response(
                username="user",
                realm="realm",
                password="pass",
                nonce="nonce1",
                method="INVITE",
                uri="sip:user@example.com",
                algorithm="MD5-sess",
            )


class TestBuildAuthHeader:
    """Build complete Authorization / Proxy-Authorization header."""

    def test_build_proxy_authorization_for_407(self):
        from sip_bridge.sip_auth import build_auth_header

        header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm="wa.meta.vc",
            password="meta-generated-pass",
            nonce="server-nonce",
            method="INVITE",
            uri="sip:+2348001234567@wa.meta.vc;transport=tls",
            algorithm="MD5",
            qop="auth",
        )
        assert header.startswith("Proxy-Authorization: Digest")
        assert 'username="+2348001234567"' in header
        assert 'realm="wa.meta.vc"' in header
        assert "response=" in header

    def test_build_authorization_for_401(self):
        from sip_bridge.sip_auth import build_auth_header

        header = build_auth_header(
            status_code=401,
            username="user",
            realm="example.com",
            password="pass",
            nonce="nonce1",
            method="REGISTER",
            uri="sip:example.com",
            algorithm="MD5",
        )
        assert header.startswith("Authorization: Digest")

    def test_header_includes_nc_cnonce_when_qop(self):
        from sip_bridge.sip_auth import build_auth_header

        header = build_auth_header(
            status_code=407,
            username="user",
            realm="test",
            password="pass",
            nonce="n",
            method="INVITE",
            uri="sip:test",
            algorithm="MD5",
            qop="auth",
        )
        assert "nc=" in header
        assert "cnonce=" in header

    def test_header_omits_nc_cnonce_without_qop(self):
        from sip_bridge.sip_auth import build_auth_header

        header = build_auth_header(
            status_code=407,
            username="user",
            realm="test",
            password="pass",
            nonce="n",
            method="INVITE",
            uri="sip:test",
            algorithm="MD5",
        )
        assert "nc=" not in header
        assert "cnonce=" not in header

    def test_header_includes_opaque_when_provided(self):
        from sip_bridge.sip_auth import build_auth_header

        header = build_auth_header(
            status_code=407,
            username="user",
            realm="test",
            password="pass",
            nonce="n",
            method="INVITE",
            uri="sip:test",
            algorithm="MD5",
            opaque="opq123",
        )
        assert 'opaque="opq123"' in header

    def test_multi_value_qop_selects_auth(self):
        """When challenge offers 'auth,auth-int', build_auth_header picks 'auth'."""
        from sip_bridge.sip_auth import build_auth_header

        header = build_auth_header(
            status_code=407,
            username="user",
            realm="test",
            password="pass",
            nonce="n",
            method="INVITE",
            uri="sip:test",
            algorithm="MD5",
            qop="auth,auth-int",
        )
        # Must emit exactly qop=auth (single token), not qop=auth,auth-int
        assert "qop=auth," in header or header.endswith("qop=auth")
        assert "qop=auth,auth-int" not in header
        assert "nc=" in header
        assert "cnonce=" in header

    def test_build_auth_header_with_sess_algorithm(self):
        """build_auth_header should work with MD5-sess."""
        from sip_bridge.sip_auth import build_auth_header

        header = build_auth_header(
            status_code=407,
            username="user",
            realm="test",
            password="pass",
            nonce="n",
            method="INVITE",
            uri="sip:test",
            algorithm="MD5-sess",
            qop="auth",
        )
        assert "algorithm=MD5-sess" in header
        assert "response=" in header


class TestVerifyDigest:
    """Verify incoming Proxy-Authorization / Authorization credentials."""

    def test_verify_valid_credentials(self):
        """Valid credentials should return True."""
        from sip_bridge.sip_auth import (
            build_auth_header,
            build_challenge_header,
            parse_challenge,
            verify_digest,
        )

        # Server sends challenge
        challenge = build_challenge_header(status_code=407, realm="ekaette.example.com")
        params = parse_challenge(challenge)

        # Client builds response
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm=params["realm"],
            password="secret-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@ekaette.example.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )

        # Server verifies — strip header name for the value part
        auth_value = auth_header.split(": ", 1)[1]
        assert verify_digest(
            auth_value=auth_value,
            expected_username="+2348001234567",
            expected_password="secret-pass",
            method="INVITE",
        ) is True

    def test_verify_wrong_password(self):
        """Wrong password should return False."""
        from sip_bridge.sip_auth import (
            build_auth_header,
            build_challenge_header,
            parse_challenge,
            verify_digest,
        )

        challenge = build_challenge_header(status_code=407, realm="test.com")
        params = parse_challenge(challenge)

        auth_header = build_auth_header(
            status_code=407,
            username="user",
            realm=params["realm"],
            password="correct-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:test",
            algorithm="MD5",
            qop=params.get("qop"),
        )

        auth_value = auth_header.split(": ", 1)[1]
        assert verify_digest(
            auth_value=auth_value,
            expected_username="user",
            expected_password="wrong-pass",
            method="INVITE",
        ) is False

    def test_verify_wrong_username(self):
        """Wrong username should return False."""
        from sip_bridge.sip_auth import (
            build_auth_header,
            build_challenge_header,
            parse_challenge,
            verify_digest,
        )

        challenge = build_challenge_header(status_code=407, realm="test.com")
        params = parse_challenge(challenge)

        auth_header = build_auth_header(
            status_code=407,
            username="real-user",
            realm=params["realm"],
            password="pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:test",
            algorithm="MD5",
            qop=params.get("qop"),
        )

        auth_value = auth_header.split(": ", 1)[1]
        assert verify_digest(
            auth_value=auth_value,
            expected_username="different-user",
            expected_password="pass",
            method="INVITE",
        ) is False

    def test_verify_accepts_normalized_username_without_plus(self):
        """Meta examples use digest usernames without the leading plus."""
        from sip_bridge.sip_auth import (
            build_auth_header,
            build_challenge_header,
            parse_challenge,
            verify_digest,
        )

        challenge = build_challenge_header(status_code=407, realm="test.com")
        params = parse_challenge(challenge)

        auth_header = build_auth_header(
            status_code=407,
            username="2348001234567",
            realm=params["realm"],
            password="secret-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@test.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )

        auth_value = auth_header.split(": ", 1)[1]
        assert verify_digest(
            auth_value=auth_value,
            expected_username="+2348001234567",
            expected_password="secret-pass",
            method="INVITE",
        ) is True

    def test_verify_rejects_multiple_leading_plus_variants(self):
        """Only a single leading plus should normalize away."""
        from sip_bridge.sip_auth import (
            build_auth_header,
            build_challenge_header,
            parse_challenge,
            verify_digest,
        )

        challenge = build_challenge_header(status_code=407, realm="test.com")
        params = parse_challenge(challenge)

        auth_header = build_auth_header(
            status_code=407,
            username="++2348001234567",
            realm=params["realm"],
            password="secret-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@test.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )

        auth_value = auth_header.split(": ", 1)[1]
        assert verify_digest(
            auth_value=auth_value,
            expected_username="+2348001234567",
            expected_password="secret-pass",
            method="INVITE",
        ) is False

    def test_verify_malformed_header_returns_false(self):
        """Malformed auth header should return False, not raise."""
        from sip_bridge.sip_auth import verify_digest

        assert verify_digest(
            auth_value="not-a-digest-header",
            expected_username="user",
            expected_password="pass",
            method="INVITE",
        ) is False

    def test_verify_malformed_qop_returns_false_not_raise(self):
        """Auth with qop=auth but missing nc/cnonce must return False, not raise ValueError."""
        from sip_bridge.sip_auth import verify_digest

        # Craft a Digest header with qop=auth but NO nc or cnonce
        # This would cause compute_digest_response to raise ValueError
        auth_value = (
            'Digest username="user", realm="test", nonce="abc", '
            'uri="sip:test", response="bad", algorithm=MD5, qop=auth'
        )
        # Must NOT raise — must return False
        result = verify_digest(
            auth_value=auth_value,
            expected_username="user",
            expected_password="pass",
            method="INVITE",
        )
        assert result is False


class TestBuildChallenge:
    """Build 407/401 challenge headers for inbound auth."""

    def test_build_407_challenge(self):
        from sip_bridge.sip_auth import build_challenge_header

        header = build_challenge_header(
            status_code=407,
            realm="ekaette.sip.example.com",
        )
        assert header.startswith("Proxy-Authenticate: Digest")
        assert 'realm="ekaette.sip.example.com"' in header
        assert "nonce=" in header

    def test_build_401_challenge(self):
        from sip_bridge.sip_auth import build_challenge_header

        header = build_challenge_header(
            status_code=401,
            realm="ekaette.sip.example.com",
        )
        assert header.startswith("WWW-Authenticate: Digest")

    def test_challenge_nonce_is_unique(self):
        from sip_bridge.sip_auth import build_challenge_header

        h1 = build_challenge_header(status_code=407, realm="test")
        h2 = build_challenge_header(status_code=407, realm="test")
        # Extract nonce values — they should differ
        import re

        n1 = re.search(r'nonce="([^"]+)"', h1)
        n2 = re.search(r'nonce="([^"]+)"', h2)
        assert n1 and n2
        assert n1.group(1) != n2.group(1)
