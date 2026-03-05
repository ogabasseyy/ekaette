"""Tests for WhatsApp SIP TLS server (WaSIPServer in wa_main.py).

Covers:
- TLS server start/stop lifecycle
- Inbound INVITE → 407 challenge → re-INVITE with auth → 200 OK → session
- BYE handling and session teardown
- IP allowlist enforcement
- Rate limiting (max concurrent calls)
- Connection handling with mock streams
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestWaSIPServerLifecycle:
    """Server start/stop and basic properties."""

    def test_server_tracks_active_sessions(self):
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config()
        server = WaSIPServer(config=config)
        assert len(server.active_sessions) == 0

    async def test_server_stop_shuts_down_sessions(self):
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config()
        server = WaSIPServer(config=config)
        # Add a mock session
        mock_session = MagicMock()
        server.active_sessions["call-1"] = mock_session
        await server.stop()
        mock_session.shutdown.assert_called_once()
        assert len(server.active_sessions) == 0


class TestInboundCallHandler:
    """Handle inbound INVITE flow: challenge → auth → 200 OK → session."""

    async def test_invite_triggers_407_challenge(self):
        """First INVITE without auth must get 407 challenge response."""
        from sip_bridge.sip_tls import SipMessage, serialize_message
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config)

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "test-call-1",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
                "content-length": "0",
            },
            body="",
        )
        response = await server.handle_sip_message(invite, ("10.0.0.1", 5061))
        assert response is not None
        assert response.status_code == 407
        assert "proxy-authenticate" in response.headers

    async def test_authenticated_invite_creates_session(self):
        """INVITE with valid Proxy-Authorization gets 200 OK and session created."""
        from sip_bridge.sip_auth import (
            build_auth_header,
            build_challenge_header,
            parse_challenge,
        )
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="test-pass",
        )
        server = WaSIPServer(config=config)

        # First INVITE → get 407
        invite1 = _make_invite("call-auth-1")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407

        # Parse challenge and build auth header
        challenge_value = resp1.headers["proxy-authenticate"]
        params = parse_challenge(f"Proxy-Authenticate: {challenge_value}")
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm=params["realm"],
            password="test-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@example.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )
        auth_value = auth_header.split(": ", 1)[1]

        # Re-INVITE with auth + SDP
        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111 126\r\n"
            "c=IN IP4 157.240.19.130\r\n"
            "a=crypto:1 AES_CM_128_HMAC_SHA1_80 "
            "inline:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "a=fmtp:111 maxplaybackrate=16000;useinbandfec=1\r\n"
            "a=rtpmap:126 telephone-event/8000\r\n"
            "a=ptime:20\r\n"
        )
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-auth-1",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": auth_value,
                "content-type": "application/sdp",
                "content-length": str(len(sdp)),
            },
            body=sdp,
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        assert resp2.status_code == 200
        assert "call-auth-1" in server.active_sessions

    async def test_bye_terminates_session(self):
        """BYE message should terminate the active session."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config)

        # Manually add a mock session
        mock_session = MagicMock()
        server.active_sessions["call-bye-1"] = mock_session

        bye = SipMessage(
            first_line="BYE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-bye-1",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>;tag=to1",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "3 BYE",
                "content-length": "0",
            },
            body="",
        )
        resp = await server.handle_sip_message(bye, ("10.0.0.1", 5061))
        assert resp.status_code == 200
        mock_session.shutdown.assert_called_once()
        assert "call-bye-1" not in server.active_sessions

    async def test_invalid_auth_returns_403(self):
        """INVITE with wrong credentials should get 403 Forbidden."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="correct-pass",
        )
        server = WaSIPServer(config=config)

        # First INVITE → 407
        invite1 = _make_invite("call-bad-auth")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407

        # Re-INVITE with WRONG credentials
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-bad-auth",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": 'Digest username="wrong", realm="test", '
                'nonce="fake", uri="sip:test", response="bad"',
                "content-length": "0",
            },
            body="",
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        assert resp2.status_code == 403


class TestIPAllowlist:
    """IP allowlist enforcement."""

    async def test_blocked_ip_returns_403(self):
        """Non-allowlisted IP should be rejected."""
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=False,
            sip_allowed_cidrs=frozenset({"192.168.1.0/24"}),
        )
        server = WaSIPServer(config=config)

        invite = _make_invite("call-blocked")
        resp = await server.handle_sip_message(invite, ("10.0.0.1", 5061))
        assert resp.status_code == 403

    async def test_allowed_ip_passes(self):
        """Allowlisted IP should proceed to 407 challenge."""
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=False,
            sip_allowed_cidrs=frozenset({"10.0.0.0/8"}),
        )
        server = WaSIPServer(config=config)

        invite = _make_invite("call-allowed")
        resp = await server.handle_sip_message(invite, ("10.0.0.1", 5061))
        # Should get 407 (challenge), not 403 (blocked)
        assert resp.status_code == 407

    async def test_sandbox_mode_allows_all(self):
        """Sandbox mode with no CIDRs should allow all IPs."""
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True, sip_allowed_cidrs=frozenset())
        server = WaSIPServer(config=config)

        invite = _make_invite("call-sandbox")
        resp = await server.handle_sip_message(invite, ("10.0.0.1", 5061))
        assert resp.status_code == 407  # Allowed through


class TestConcurrencyLimit:
    """Rate limiting: max concurrent calls."""

    async def test_reject_when_max_calls_reached(self):
        """Should return 503 when max concurrent calls exceeded."""
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config, max_concurrent_calls=2)

        # Fill up to limit
        for i in range(2):
            server.active_sessions[f"call-{i}"] = MagicMock()

        invite = _make_invite("call-over-limit")
        resp = await server.handle_sip_message(invite, ("10.0.0.1", 5061))
        assert resp.status_code == 503


class TestTLSEnforcement:
    """Server must refuse to start without TLS in non-sandbox mode."""

    async def test_start_raises_without_tls_in_production(self):
        """start() must raise when no TLS cert/key and not sandbox."""
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=False, sip_allowed_cidrs=frozenset({"10.0.0.0/8"}))
        config.tls_certfile = ""
        config.tls_keyfile = ""
        server = WaSIPServer(config=config)
        with pytest.raises(RuntimeError, match="[Tt][Ll][Ss]"):
            await server.start()

    async def test_start_allows_no_tls_in_sandbox(self):
        """Sandbox mode may start without TLS (local dev)."""
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        config.tls_certfile = ""
        config.tls_keyfile = ""
        server = WaSIPServer(config=config)
        # Should not raise — starts plaintext for sandbox
        await server.start()
        await server.stop()


class TestSessionMediaWiring:
    """Accepted INVITEs must create sessions with full media dependencies."""

    async def test_session_has_codec_bridge(self):
        """Accepted INVITE must wire codec_bridge into session."""
        from sip_bridge.wa_main import WaSIPServer

        server, session = await _create_authenticated_session()
        assert session.codec_bridge is not None

    async def test_session_has_srtp_contexts(self):
        """Accepted INVITE must wire SRTP sender and receiver."""
        server, session = await _create_authenticated_session()
        assert session.srtp_sender is not None
        assert session.srtp_receiver is not None

    async def test_session_has_remote_media_addr(self):
        """Accepted INVITE must parse remote media address from SDP."""
        server, session = await _create_authenticated_session()
        assert session.remote_media_addr is not None
        assert session.remote_media_addr[0] == "157.240.19.130"
        assert session.remote_media_addr[1] == 3480

    async def test_session_has_gemini_config(self):
        """Accepted INVITE must pass Gemini config for session to connect."""
        server, session = await _create_authenticated_session()
        assert session.gemini_api_key == "test-key"
        assert session.gemini_model_id == "gemini-test"

    async def test_session_has_whatsapp_tool_context(self):
        """Accepted INVITE must wire caller phone and bridge config for WA tools."""
        server, session = await _create_authenticated_session()
        assert session._caller_phone == "+1234"
        assert session._bridge_config is server.config

    async def test_session_owns_server_created_transport(self):
        """Session must own the media_transport created by _handle_invite,
        so that run() closes it on shutdown (no socket leak)."""
        server, session = await _create_authenticated_session()
        assert session.media_transport is not None
        assert session._owns_transport is True
        # Clean up
        session.shutdown()
        session.media_transport.close()


class TestSDPAnswerPort:
    """Finding 2: SDP answer must advertise a local port, not echo the remote port."""

    async def test_sdp_answer_port_differs_from_remote(self):
        """SDP answer local_port must NOT be the remote SDP media port."""
        server, session = await _create_authenticated_session()

        # The remote SDP has media port 3480. The SDP answer must NOT
        # use 3480 as local_port — it should use the port the local
        # UDP socket is actually bound to.
        # Check via the session's media_transport bind address
        assert session.media_transport is not None
        local_addr = session.media_transport.getsockname()
        local_port = local_addr[1]
        # Local port must NOT be the remote port (3480)
        assert local_port != 3480
        assert local_port > 0

        # Clean up
        session.shutdown()
        session.media_transport.close()

    async def test_sdp_answer_contains_local_bound_port(self):
        """The 200 OK SDP body must contain the actual bound local port."""
        from sip_bridge.sip_auth import build_auth_header, parse_challenge
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="test-pass",
        )
        server = WaSIPServer(config=config)

        # Step 1: INVITE → 407
        invite1 = _make_invite("call-sdp-port")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407

        # Step 2: Build auth
        challenge_value = resp1.headers["proxy-authenticate"]
        params = parse_challenge(f"Proxy-Authenticate: {challenge_value}")
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm=params["realm"],
            password="test-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@example.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )
        auth_value = auth_header.split(": ", 1)[1]

        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111 126\r\n"
            "c=IN IP4 157.240.19.130\r\n"
            "a=crypto:1 AES_CM_128_HMAC_SHA1_80 "
            "inline:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "a=fmtp:111 maxplaybackrate=16000;useinbandfec=1\r\n"
            "a=rtpmap:126 telephone-event/8000\r\n"
            "a=ptime:20\r\n"
        )
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-sdp-port",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": auth_value,
                "content-type": "application/sdp",
                "content-length": str(len(sdp)),
            },
            body=sdp,
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        assert resp2.status_code == 200

        # The SDP body must NOT contain "m=audio 3480" (remote port)
        # It should contain a different, locally bound port
        assert "m=audio 3480 " not in resp2.body
        # It must still have an m=audio line
        assert "m=audio " in resp2.body

        # Clean up
        session = server.active_sessions["call-sdp-port"]
        session.shutdown()
        if session.media_transport:
            session.media_transport.close()


class TestNonceReplayEnforcement:
    """Challenge nonce must be validated on re-INVITE."""

    async def test_stale_nonce_rejected(self):
        """Re-INVITE with a nonce we never issued must be rejected,
        even if the digest response itself is correct for that nonce."""
        from sip_bridge.sip_auth import build_auth_header, compute_digest_response
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="test-pass",
        )
        server = WaSIPServer(config=config)

        # Trigger a challenge to populate _pending_challenges
        invite1 = _make_invite("call-nonce-test")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407

        # Build a VALID digest response using a fabricated nonce.
        # The digest hash is correct for this nonce, so verify_digest
        # would return True — the server MUST reject based on nonce tracking.
        fake_nonce = "fabricated-nonce-never-issued"
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm="0.0.0.0",
            password="test-pass",
            nonce=fake_nonce,
            method="INVITE",
            uri="sip:+2348001234567@example.com",
        )
        auth_value = auth_header.split(": ", 1)[1]

        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111\r\n"
            "c=IN IP4 157.240.19.130\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
        )
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-nonce-test",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": auth_value,
                "content-type": "application/sdp",
                "content-length": str(len(sdp)),
            },
            body=sdp,
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        # Must reject — nonce was never issued by this server
        assert resp2.status_code == 403
        # Session must NOT be created
        assert "call-nonce-test" not in server.active_sessions


class TestPendingChallengeCleanup:
    """Pending challenges must be cleaned up on failed auth to prevent unbounded growth."""

    async def test_nonce_mismatch_clears_pending_challenge(self):
        """After 403 for nonce mismatch, _pending_challenges entry must be removed."""
        from sip_bridge.sip_auth import build_auth_header
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config)

        # INVITE → 407 (creates pending challenge)
        invite1 = _make_invite("call-cleanup-1")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407
        assert "call-cleanup-1" in server._pending_challenges

        # Re-INVITE with fabricated nonce → 403
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm="0.0.0.0",
            password="test-pass",
            nonce="fabricated-nonce",
            method="INVITE",
            uri="sip:test",
        )
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-cleanup-1",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": auth_header.split(": ", 1)[1],
                "content-length": "0",
            },
            body="",
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        assert resp2.status_code == 403
        # Pending challenge must be cleaned up
        assert "call-cleanup-1" not in server._pending_challenges

    async def test_invalid_credentials_clears_pending_challenge(self):
        """After 403 for bad credentials, _pending_challenges entry must be removed."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="correct-pass",
        )
        server = WaSIPServer(config=config)

        # INVITE → 407
        invite1 = _make_invite("call-cleanup-2")
        await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert "call-cleanup-2" in server._pending_challenges

        # Re-INVITE with wrong credentials → 403
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-cleanup-2",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": 'Digest username="wrong", realm="test", '
                'nonce="fake", uri="sip:test", response="bad"',
                "content-length": "0",
            },
            body="",
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        assert resp2.status_code == 403
        # Pending challenge must be cleaned up
        assert "call-cleanup-2" not in server._pending_challenges

    async def test_pending_challenges_bounded_by_max_calls(self):
        """_pending_challenges must not grow beyond max_concurrent_calls * 2."""
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config, max_concurrent_calls=2)

        # Send many unauthenticated INVITEs with unique call-ids
        for i in range(10):
            invite = _make_invite(f"call-flood-{i}")
            await server.handle_sip_message(invite, ("10.0.0.1", 5061))

        # Pending challenges must be bounded
        assert len(server._pending_challenges) <= 2 * 2  # max_concurrent_calls * 2


class TestSDPErrorHandling:
    """Finding: malformed SDP must not leak UDP sockets or crash without SIP response."""

    async def test_bad_crypto_returns_sip_error_not_crash(self):
        """If SRTP crypto parsing raises, _handle_invite must return a SIP
        error response (488 or 500), NOT propagate the exception."""
        from sip_bridge.sip_auth import build_auth_header, parse_challenge
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="test-pass",
        )
        server = WaSIPServer(config=config)

        # Step 1: INVITE → 407
        invite1 = _make_invite("call-bad-crypto")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407

        # Step 2: Build valid auth
        challenge_value = resp1.headers["proxy-authenticate"]
        params = parse_challenge(f"Proxy-Authenticate: {challenge_value}")
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm=params["realm"],
            password="test-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@example.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )
        auth_value = auth_header.split(": ", 1)[1]

        # SDP with unsupported crypto suite → parse_sdes_crypto raises SRTPError
        bad_sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111\r\n"
            "c=IN IP4 157.240.19.130\r\n"
            "a=crypto:1 AES_256_CM_HMAC_SHA1_80 "
            "inline:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
        )
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-bad-crypto",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": auth_value,
                "content-type": "application/sdp",
                "content-length": str(len(bad_sdp)),
            },
            body=bad_sdp,
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        # Must return a SIP error, NOT raise an exception
        assert resp2 is not None
        assert resp2.status_code in (488, 500)
        # No session should be created
        assert "call-bad-crypto" not in server.active_sessions

    async def test_bad_sdp_does_not_leak_socket(self):
        """If SDP processing fails after socket creation, the socket must be closed."""
        import socket as _socket
        from unittest.mock import patch

        from sip_bridge.sip_auth import build_auth_header, parse_challenge
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="test-pass",
        )
        server = WaSIPServer(config=config)

        # Step 1: INVITE → 407
        invite1 = _make_invite("call-leak-test")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407

        # Build valid auth
        challenge_value = resp1.headers["proxy-authenticate"]
        params = parse_challenge(f"Proxy-Authenticate: {challenge_value}")
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm=params["realm"],
            password="test-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@example.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )
        auth_value = auth_header.split(": ", 1)[1]

        # Track sockets created and closed
        created_sockets = []
        original_socket = _socket.socket

        def tracking_socket(*args, **kwargs):
            sock = original_socket(*args, **kwargs)
            created_sockets.append(sock)
            return sock

        # SDP with unsupported suite
        bad_sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111\r\n"
            "c=IN IP4 157.240.19.130\r\n"
            "a=crypto:1 AES_256_CM_HMAC_SHA1_80 "
            "inline:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
        )
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-leak-test",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": auth_value,
                "content-type": "application/sdp",
                "content-length": str(len(bad_sdp)),
            },
            body=bad_sdp,
        )
        with patch("sip_bridge.wa_main.socket.socket", side_effect=tracking_socket):
            await server.handle_sip_message(invite2, ("10.0.0.1", 5061))

        # Socket must have been created and then closed (not leaked)
        assert len(created_sockets) >= 1
        for sock in created_sockets:
            # fileno() returns -1 when socket is closed
            assert sock.fileno() == -1, "UDP socket was leaked (not closed after SDP error)"


class TestMissingRemoteMedia:
    """Finding: INVITE with missing remote media endpoint must be rejected."""

    async def test_invite_without_media_ip_returns_488(self):
        """If SDP has no c= line (no media_ip), must reject with 488."""
        from sip_bridge.sip_auth import build_auth_header, parse_challenge
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(
            sandbox_mode=True,
            sip_username="+2348001234567",
            sip_password="test-pass",
        )
        server = WaSIPServer(config=config)

        # Step 1: INVITE → 407
        invite1 = _make_invite("call-no-media")
        resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
        assert resp1.status_code == 407

        # Build valid auth
        challenge_value = resp1.headers["proxy-authenticate"]
        params = parse_challenge(f"Proxy-Authenticate: {challenge_value}")
        auth_header = build_auth_header(
            status_code=407,
            username="+2348001234567",
            realm=params["realm"],
            password="test-pass",
            nonce=params["nonce"],
            method="INVITE",
            uri="sip:+2348001234567@example.com",
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
        )
        auth_value = auth_header.split(": ", 1)[1]

        # SDP WITHOUT c= line (no media IP) and port 0 (no media)
        no_media_sdp = (
            "v=0\r\n"
            "m=audio 0 RTP/SAVP 111\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
        )
        invite2 = SipMessage(
            first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
            headers={
                "call-id": "call-no-media",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "2 INVITE",
                "proxy-authorization": auth_value,
                "content-type": "application/sdp",
                "content-length": str(len(no_media_sdp)),
            },
            body=no_media_sdp,
        )
        resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
        # Must reject — no usable remote media endpoint
        assert resp2 is not None
        assert resp2.status_code == 488
        assert "call-no-media" not in server.active_sessions


class TestHealthEndpoint:
    """M4: Health/readiness HTTP endpoint for monitoring."""

    async def test_healthz_returns_200(self):
        """GET /healthz must return 200 when server is running."""
        import aiohttp

        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config)
        await server.start()

        try:
            async with aiohttp.ClientSession() as client:
                async with client.get(
                    f"http://127.0.0.1:{config.health_port}/healthz"
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["status"] == "ok"
        finally:
            await server.stop()

    async def test_readyz_reports_active_sessions(self):
        """GET /readyz must report active session count."""
        import aiohttp

        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config)
        await server.start()

        # Add a mock session
        server.active_sessions["call-health-1"] = MagicMock()

        try:
            async with aiohttp.ClientSession() as client:
                async with client.get(
                    f"http://127.0.0.1:{config.health_port}/readyz"
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["active_sessions"] == 1
        finally:
            await server.stop()

    async def test_readyz_returns_503_at_capacity(self):
        """GET /readyz must return 503 when at max concurrent calls."""
        import aiohttp

        from sip_bridge.wa_main import WaSIPServer

        config = _make_config(sandbox_mode=True)
        server = WaSIPServer(config=config, max_concurrent_calls=2)
        await server.start()

        # Fill to capacity
        for i in range(2):
            server.active_sessions[f"call-cap-{i}"] = MagicMock()

        try:
            async with aiohttp.ClientSession() as client:
                async with client.get(
                    f"http://127.0.0.1:{config.health_port}/readyz"
                ) as resp:
                    assert resp.status == 503
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    sandbox_mode: bool = True,
    sip_username: str = "+2348001234567",
    sip_password: str = "test-pass",
    sip_allowed_cidrs: frozenset[str] | None = None,
) -> MagicMock:
    """Create a mock WhatsAppBridgeConfig."""
    config = MagicMock()
    config.sip_host = "0.0.0.0"
    config.sip_port = 5061
    config.sip_username = sip_username
    config.sip_password = sip_password
    config.sip_allowed_cidrs = sip_allowed_cidrs or frozenset()
    config.tls_certfile = ""
    config.tls_keyfile = ""
    config.sandbox_mode = sandbox_mode
    config.gemini_api_key = "test-key"
    config.live_model_id = "gemini-test"
    config.system_instruction = "Test assistant"
    config.gemini_voice = "Aoede"
    config.company_id = "test-company"
    config.tenant_id = "public"
    config.health_port = 8082
    return config


def _make_invite(call_id: str):
    """Create a basic INVITE SipMessage without auth."""
    from sip_bridge.sip_tls import SipMessage

    return SipMessage(
        first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
        headers={
            "call-id": call_id,
            "from": "<sip:+1234@wa.meta.vc>;tag=from1",
            "to": "<sip:+5678@example.com>",
            "via": "SIP/2.0/TLS 10.0.0.1:5061",
            "cseq": "1 INVITE",
            "content-length": "0",
        },
        body="",
    )


async def _create_authenticated_session():
    """Run the full 407→auth→200 OK flow and return (server, session)."""
    from sip_bridge.sip_auth import build_auth_header, parse_challenge
    from sip_bridge.sip_tls import SipMessage
    from sip_bridge.wa_main import WaSIPServer

    config = _make_config(
        sandbox_mode=True,
        sip_username="+2348001234567",
        sip_password="test-pass",
    )
    server = WaSIPServer(config=config)

    # Step 1: INVITE → 407
    invite1 = _make_invite("call-wired")
    resp1 = await server.handle_sip_message(invite1, ("10.0.0.1", 5061))
    assert resp1.status_code == 407

    # Step 2: Build auth from challenge
    challenge_value = resp1.headers["proxy-authenticate"]
    params = parse_challenge(f"Proxy-Authenticate: {challenge_value}")
    auth_header = build_auth_header(
        status_code=407,
        username="+2348001234567",
        realm=params["realm"],
        password="test-pass",
        nonce=params["nonce"],
        method="INVITE",
        uri="sip:+2348001234567@example.com",
        algorithm=params.get("algorithm", "MD5"),
        qop=params.get("qop"),
    )
    auth_value = auth_header.split(": ", 1)[1]

    # Step 3: Re-INVITE with auth + SDP (includes crypto + media)
    sdp = (
        "v=0\r\n"
        "m=audio 3480 RTP/SAVP 111 126\r\n"
        "c=IN IP4 157.240.19.130\r\n"
        "a=crypto:1 AES_CM_128_HMAC_SHA1_80 "
        "inline:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        "a=fmtp:111 maxplaybackrate=16000;useinbandfec=1\r\n"
        "a=rtpmap:126 telephone-event/8000\r\n"
        "a=ptime:20\r\n"
    )
    invite2 = SipMessage(
        first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
        headers={
            "call-id": "call-wired",
            "from": "<sip:+1234@wa.meta.vc>;tag=from1",
            "to": "<sip:+5678@example.com>",
            "via": "SIP/2.0/TLS 10.0.0.1:5061",
            "cseq": "2 INVITE",
            "proxy-authorization": auth_value,
            "content-type": "application/sdp",
            "content-length": str(len(sdp)),
        },
        body=sdp,
    )
    resp2 = await server.handle_sip_message(invite2, ("10.0.0.1", 5061))
    assert resp2.status_code == 200

    session = server.active_sessions["call-wired"]
    return server, session
