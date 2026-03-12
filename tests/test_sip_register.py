"""Tests for SIP REGISTER client (AT registrar integration).

TDD Red phase — tests for SIP REGISTER message building, 401 handling,
and periodic re-registration.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sip_bridge.config import BridgeConfig


def _make_config(**overrides) -> BridgeConfig:
    """Build a BridgeConfig with sensible defaults for registration tests."""
    defaults = dict(
        sip_host="0.0.0.0",
        sip_port=6060,
        sip_public_ip="34.69.236.219",
        sip_allowed_peers=frozenset(),
        gemini_api_key="test-key",
        live_model_id="gemini-live-2.5-flash-native-audio",
        system_instruction="Test",
        gemini_voice="Aoede",
        company_id="ekaette-electronics",
        tenant_id="public",
        health_port=8081,
        sip_registrar="ng.sip.africastalking.com",
        sip_username="ekaette.ekaette@ng.sip.africastalking.com",
        sip_password="Kokoete96!",
        sip_register_interval=300,
    )
    defaults.update(overrides)
    return BridgeConfig(**defaults)


class TestBuildRegisterMessage:
    """REGISTER SIP message construction."""

    def test_build_initial_register(self):
        from sip_bridge.sip_register import build_register_message

        msg = build_register_message(
            registrar="ng.sip.africastalking.com",
            username="ekaette.ekaette@ng.sip.africastalking.com",
            public_ip="34.69.236.219",
            port=6060,
            call_id="test-call-id",
            cseq=1,
            expires=300,
        )
        assert msg.startswith("REGISTER sip:ng.sip.africastalking.com SIP/2.0\r\n")
        assert "Via: SIP/2.0/UDP 34.69.236.219:6060" in msg
        assert "From: <sip:ekaette.ekaette@ng.sip.africastalking.com>" in msg
        assert "To: <sip:ekaette.ekaette@ng.sip.africastalking.com>" in msg
        assert "Call-ID: test-call-id" in msg
        assert "CSeq: 1 REGISTER" in msg
        assert "Contact: <sip:ekaette.ekaette@34.69.236.219:6060>" in msg
        assert "Expires: 300" in msg
        assert "Content-Length: 0" in msg

    def test_build_register_with_auth(self):
        from sip_bridge.sip_register import build_register_message

        auth_header = 'Authorization: Digest username="test", realm="test", nonce="n", response="r"'
        msg = build_register_message(
            registrar="ng.sip.africastalking.com",
            username="ekaette.ekaette@ng.sip.africastalking.com",
            public_ip="34.69.236.219",
            port=6060,
            call_id="test-call-id",
            cseq=2,
            expires=300,
            auth_header=auth_header,
        )
        assert "CSeq: 2 REGISTER" in msg
        assert auth_header in msg

    def test_branch_parameter_unique(self):
        from sip_bridge.sip_register import build_register_message

        msg1 = build_register_message(
            registrar="test.com",
            username="user@test.com",
            public_ip="1.2.3.4",
            port=5060,
            call_id="cid",
            cseq=1,
            expires=300,
        )
        msg2 = build_register_message(
            registrar="test.com",
            username="user@test.com",
            public_ip="1.2.3.4",
            port=5060,
            call_id="cid",
            cseq=2,
            expires=300,
        )
        # Extract branch parameters — they should differ
        import re

        b1 = re.search(r"branch=(z9hG4bK[^\s;]+)", msg1)
        b2 = re.search(r"branch=(z9hG4bK[^\s;]+)", msg2)
        assert b1 and b2
        assert b1.group(1) != b2.group(1)


class TestParseRegisterResponse:
    """Parse SIP REGISTER responses (200 OK, 401 Unauthorized)."""

    def test_parse_200_ok(self):
        from sip_bridge.sip_register import parse_sip_response

        response = (
            "SIP/2.0 200 OK\r\n"
            "Via: SIP/2.0/UDP 34.69.236.219:6060;branch=z9hG4bKtest\r\n"
            "From: <sip:user@test.com>;tag=abc\r\n"
            "To: <sip:user@test.com>;tag=xyz\r\n"
            "Call-ID: cid\r\n"
            "CSeq: 1 REGISTER\r\n"
            "Contact: <sip:user@34.69.236.219:6060>;expires=300\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        result = parse_sip_response(response)
        assert result["status_code"] == 200
        assert result["reason"] == "OK"

    def test_parse_401_with_challenge(self):
        from sip_bridge.sip_register import parse_sip_response

        response = (
            "SIP/2.0 401 Unauthorized\r\n"
            "Via: SIP/2.0/UDP 34.69.236.219:6060;branch=z9hG4bKtest\r\n"
            "From: <sip:user@test.com>;tag=abc\r\n"
            "To: <sip:user@test.com>;tag=xyz\r\n"
            "Call-ID: cid\r\n"
            "CSeq: 1 REGISTER\r\n"
            'WWW-Authenticate: Digest realm="ng.sip.africastalking.com", nonce="abc123", algorithm=MD5, qop="auth"\r\n'
            "Content-Length: 0\r\n"
            "\r\n"
        )
        result = parse_sip_response(response)
        assert result["status_code"] == 401
        assert result["reason"] == "Unauthorized"
        assert "WWW-Authenticate" in result["headers"]

    def test_parse_403_forbidden(self):
        from sip_bridge.sip_register import parse_sip_response

        response = (
            "SIP/2.0 403 Forbidden\r\n"
            "Call-ID: cid\r\n"
            "CSeq: 1 REGISTER\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        result = parse_sip_response(response)
        assert result["status_code"] == 403
        assert result["reason"] == "Forbidden"


class TestSIPRegistrarClient:
    """Integration-level tests for the registrar client loop."""

    def test_registrar_skipped_when_no_credentials(self):
        """Registration should not start if username/password empty."""
        from sip_bridge.sip_register import SIPRegistrar

        config = _make_config(sip_username="", sip_password="")
        registrar = SIPRegistrar(config=config)
        assert registrar.should_register is False

    def test_registrar_enabled_with_credentials(self):
        from sip_bridge.sip_register import SIPRegistrar

        config = _make_config()
        registrar = SIPRegistrar(config=config)
        assert registrar.should_register is True

    @pytest.mark.asyncio
    async def test_send_register_uses_transport(self):
        """send_register should send bytes via the UDP transport."""
        from sip_bridge.sip_register import SIPRegistrar

        config = _make_config()
        registrar = SIPRegistrar(config=config)

        mock_transport = MagicMock()
        registrar._transport = mock_transport

        registrar.send_register()

        mock_transport.sendto.assert_called_once()
        sent_data, addr = mock_transport.sendto.call_args[0]
        assert b"REGISTER sip:ng.sip.africastalking.com" in sent_data
        assert addr[0]  # registrar IP resolved
        assert addr[1] == 5060  # default SIP port

    @pytest.mark.asyncio
    async def test_handle_401_sends_authenticated_register(self):
        """On 401, registrar should re-send with Authorization header."""
        from sip_bridge.sip_register import SIPRegistrar

        config = _make_config()
        registrar = SIPRegistrar(config=config)

        mock_transport = MagicMock()
        registrar._transport = mock_transport

        # Simulate initial REGISTER (CSeq 1)
        registrar.send_register()
        assert mock_transport.sendto.call_count == 1

        response_text = (
            "SIP/2.0 401 Unauthorized\r\n"
            "Via: SIP/2.0/UDP 34.69.236.219:6060;branch=z9hG4bKtest\r\n"
            "From: <sip:ekaette.ekaette@ng.sip.africastalking.com>;tag=abc\r\n"
            "To: <sip:ekaette.ekaette@ng.sip.africastalking.com>;tag=xyz\r\n"
            "Call-ID: cid\r\n"
            "CSeq: 1 REGISTER\r\n"
            'WWW-Authenticate: Digest realm="ng.sip.africastalking.com", '
            'nonce="server-nonce-123", algorithm=MD5, qop="auth"\r\n'
            "Content-Length: 0\r\n"
            "\r\n"
        )
        registrar.handle_response(response_text)

        # Should have sent authenticated REGISTER (CSeq 2)
        assert mock_transport.sendto.call_count == 2
        sent_data = mock_transport.sendto.call_args[0][0]
        assert b"Authorization: Digest" in sent_data
        assert b"CSeq: 2 REGISTER" in sent_data

    @pytest.mark.asyncio
    async def test_handle_200_sets_registered(self):
        """On 200 OK, registrar should mark as registered."""
        from sip_bridge.sip_register import SIPRegistrar

        config = _make_config()
        registrar = SIPRegistrar(config=config)

        response_text = (
            "SIP/2.0 200 OK\r\n"
            "Call-ID: cid\r\n"
            "CSeq: 2 REGISTER\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        registrar.handle_response(response_text)
        assert registrar.registered is True
