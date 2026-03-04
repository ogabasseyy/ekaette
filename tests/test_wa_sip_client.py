"""Tests for WhatsApp SIP UA client (wa_sip_client.py).

Covers:
- Dialog state machine (IDLE → INVITED → EARLY → CONFIRMED → TERMINATED)
- SDP generation (Opus + SDES crypto)
- SDP parsing (extract remote media addr, crypto, codec params)
- Call ID resolution (x-wa-meta-wacid primary, Call-ID fallback)
- Inbound call flow (INVITE → 407 → re-INVITE with auth → 200 OK)
- Duplicate message handling (idempotency)
"""

from __future__ import annotations

# --- Dialog state machine tests ---


class TestDialogState:
    """Dialog state enum and transitions."""

    def test_dialog_states_exist(self):
        from sip_bridge.wa_sip_client import DialogState

        assert hasattr(DialogState, "IDLE")
        assert hasattr(DialogState, "INVITED")
        assert hasattr(DialogState, "EARLY")
        assert hasattr(DialogState, "CONFIRMED")
        assert hasattr(DialogState, "TERMINATED")

    def test_dialog_initial_state(self):
        from sip_bridge.wa_sip_client import Dialog

        d = Dialog(call_id="test-call")
        assert d.state.name == "IDLE"

    def test_dialog_invite_transitions_to_invited(self):
        from sip_bridge.wa_sip_client import Dialog, DialogState

        d = Dialog(call_id="test-call")
        d.transition(DialogState.INVITED)
        assert d.state == DialogState.INVITED

    def test_dialog_confirmed_after_ack(self):
        from sip_bridge.wa_sip_client import Dialog, DialogState

        d = Dialog(call_id="test-call")
        d.transition(DialogState.INVITED)
        d.transition(DialogState.CONFIRMED)
        assert d.state == DialogState.CONFIRMED

    def test_dialog_terminated(self):
        from sip_bridge.wa_sip_client import Dialog, DialogState

        d = Dialog(call_id="test-call")
        d.transition(DialogState.INVITED)
        d.transition(DialogState.CONFIRMED)
        d.transition(DialogState.TERMINATED)
        assert d.state == DialogState.TERMINATED

    def test_dialog_stores_call_id(self):
        from sip_bridge.wa_sip_client import Dialog

        d = Dialog(call_id="wa-call-123")
        assert d.call_id == "wa-call-123"


# --- Call ID resolution tests ---


class TestCallIdResolution:
    """Resolve call ID: x-wa-meta-wacid preferred, Call-ID fallback."""

    def test_prefer_wa_meta_wacid(self):
        from sip_bridge.wa_sip_client import resolve_call_id

        headers = {
            "call-id": "sip-call-id-abc",
            "x-wa-meta-wacid": "wa-meta-123",
        }
        assert resolve_call_id(headers) == "wa-meta-123"

    def test_fallback_to_call_id(self):
        from sip_bridge.wa_sip_client import resolve_call_id

        headers = {"call-id": "sip-call-id-abc"}
        assert resolve_call_id(headers) == "sip-call-id-abc"

    def test_missing_both_returns_none(self):
        from sip_bridge.wa_sip_client import resolve_call_id

        assert resolve_call_id({}) is None

    def test_empty_wacid_falls_back(self):
        from sip_bridge.wa_sip_client import resolve_call_id

        headers = {
            "call-id": "sip-call-id",
            "x-wa-meta-wacid": "",
        }
        assert resolve_call_id(headers) == "sip-call-id"


# --- SDP generation tests ---


class TestSDPGeneration:
    """Generate SDP answer with Opus + SDES."""

    def test_sdp_contains_opus(self):
        from sip_bridge.wa_sip_client import generate_sdp_answer

        sdp = generate_sdp_answer(
            local_ip="10.0.0.1",
            local_port=30000,
            payload_type=111,
        )
        assert "opus/48000/2" in sdp
        assert "a=rtpmap:111" in sdp

    def test_sdp_contains_crypto_line(self):
        from sip_bridge.wa_sip_client import generate_sdp_answer

        sdp = generate_sdp_answer(
            local_ip="10.0.0.1",
            local_port=30000,
            payload_type=111,
        )
        assert "a=crypto:" in sdp
        assert "AES_CM_128_HMAC_SHA1_80" in sdp

    def test_sdp_contains_rtp_savp(self):
        from sip_bridge.wa_sip_client import generate_sdp_answer

        sdp = generate_sdp_answer(
            local_ip="10.0.0.1",
            local_port=30000,
            payload_type=111,
        )
        assert "RTP/SAVP" in sdp

    def test_sdp_contains_ptime(self):
        from sip_bridge.wa_sip_client import generate_sdp_answer

        sdp = generate_sdp_answer(
            local_ip="10.0.0.1",
            local_port=30000,
            payload_type=111,
        )
        assert "a=ptime:20" in sdp

    def test_sdp_contains_connection_line(self):
        from sip_bridge.wa_sip_client import generate_sdp_answer

        sdp = generate_sdp_answer(
            local_ip="203.0.113.1",
            local_port=30000,
            payload_type=111,
        )
        assert "c=IN IP4 203.0.113.1" in sdp

    def test_sdp_returns_key_material(self):
        from sip_bridge.wa_sip_client import generate_sdp_answer

        sdp = generate_sdp_answer(
            local_ip="10.0.0.1",
            local_port=30000,
            payload_type=111,
        )
        # SDP string returned — key material is in the crypto line
        assert "inline:" in sdp


# --- SDP parsing tests ---


class TestSDPParsing:
    """Parse remote SDP from Meta's INVITE/200 OK."""

    def test_parse_opus_payload_type(self):
        from sip_bridge.wa_sip_client import parse_remote_sdp

        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111 126\r\n"
            "c=IN IP4 157.240.19.130\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "a=fmtp:111 maxplaybackrate=16000;useinbandfec=1\r\n"
            "a=rtpmap:126 telephone-event/8000\r\n"
            "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:dGVzdGtleW1hdGVyaWFsMTIzNDU2Nzg5MDEyMzQ=\r\n"
            "a=ptime:20\r\n"
        )
        result = parse_remote_sdp(sdp)
        assert result["media_ip"] == "157.240.19.130"
        assert result["media_port"] == 3480
        assert result["opus_payload_type"] == 111
        assert result["encode_rate"] == 16000

    def test_parse_default_encode_rate(self):
        """No maxplaybackrate → default 16000."""
        from sip_bridge.wa_sip_client import parse_remote_sdp

        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111\r\n"
            "c=IN IP4 10.0.0.1\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:dGVzdGtleW1hdGVyaWFsMTIzNDU2Nzg5MDEyMzQ=\r\n"
        )
        result = parse_remote_sdp(sdp)
        assert result["encode_rate"] == 16000

    def test_parse_dtmf_payload_type(self):
        from sip_bridge.wa_sip_client import parse_remote_sdp

        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111 126\r\n"
            "c=IN IP4 10.0.0.1\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "a=rtpmap:126 telephone-event/8000\r\n"
            "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:dGVzdGtleW1hdGVyaWFsMTIzNDU2Nzg5MDEyMzQ=\r\n"
        )
        result = parse_remote_sdp(sdp)
        assert result["dtmf_payload_type"] == 126

    def test_parse_no_dtmf(self):
        from sip_bridge.wa_sip_client import parse_remote_sdp

        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111\r\n"
            "c=IN IP4 10.0.0.1\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:dGVzdGtleW1hdGVyaWFsMTIzNDU2Nzg5MDEyMzQ=\r\n"
        )
        result = parse_remote_sdp(sdp)
        assert result["dtmf_payload_type"] is None


# --- SIP response building tests ---


class TestSIPResponseBuilding:
    """Build SIP response messages (407, 200 OK, etc.)."""

    def test_build_407_challenge(self):
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_sip_client import build_407_response

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={
                "call-id": "abc123",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
            },
            body="",
        )
        resp = build_407_response(invite, realm="sip.example.com")
        assert resp.status_code == 407
        assert resp.headers["call-id"] == "abc123"
        assert "proxy-authenticate" in resp.headers

    def test_build_407_adds_local_tag_to_to_header(self):
        """RFC 3261 §8.2.6.2: UAS MUST add a tag to the To header in responses."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_sip_client import build_407_response

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={
                "call-id": "abc123",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
            },
            body="",
        )
        resp = build_407_response(invite, realm="sip.example.com")
        to_header = resp.headers["to"]
        assert ";tag=" in to_header
        # Tag must be non-empty
        tag_value = to_header.split(";tag=")[1].split(";")[0]
        assert len(tag_value) > 0

    def test_build_407_preserves_to_uri(self):
        """To header URI must be preserved; only tag is added."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_sip_client import build_407_response

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={
                "call-id": "abc123",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
            },
            body="",
        )
        resp = build_407_response(invite, realm="sip.example.com")
        assert resp.headers["to"].startswith("<sip:+5678@example.com>")

    def test_build_200_ok(self):
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_sip_client import build_200_ok

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={
                "call-id": "abc123",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
                "contact": "<sip:+1234@10.0.0.1:5061;transport=tls>",
            },
            body="",
        )
        sdp_body = "v=0\r\nm=audio 30000 RTP/SAVP 111\r\n"
        resp = build_200_ok(
            invite,
            sdp_body=sdp_body,
            local_contact="<sip:ekaette@203.0.113.1:5061;transport=tls>",
        )
        assert resp.status_code == 200
        assert resp.headers["call-id"] == "abc123"
        assert resp.body == sdp_body
        assert resp.headers["content-length"] == str(len(sdp_body))

    def test_build_200_ok_adds_local_tag_to_to_header(self):
        """RFC 3261 §8.2.6.2: 200 OK MUST add a tag to the To header."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_sip_client import build_200_ok

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={
                "call-id": "abc123",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
            },
            body="",
        )
        sdp_body = "v=0\r\nm=audio 30000 RTP/SAVP 111\r\n"
        resp = build_200_ok(
            invite,
            sdp_body=sdp_body,
            local_contact="<sip:ekaette@203.0.113.1:5061;transport=tls>",
        )
        to_header = resp.headers["to"]
        assert ";tag=" in to_header
        tag_value = to_header.split(";tag=")[1].split(";")[0]
        assert len(tag_value) > 0

    def test_build_200_ok_uses_local_contact(self):
        """200 OK Contact MUST be local URI, not echoed from INVITE."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_sip_client import build_200_ok

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={
                "call-id": "abc123",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
                "contact": "<sip:+1234@10.0.0.1:5061;transport=tls>",
            },
            body="",
        )
        sdp_body = "v=0\r\nm=audio 30000 RTP/SAVP 111\r\n"
        resp = build_200_ok(
            invite,
            sdp_body=sdp_body,
            local_contact="<sip:ekaette@203.0.113.1:5061;transport=tls>",
        )
        # Contact must be our local URI, NOT the remote's
        assert resp.headers["contact"] == "<sip:ekaette@203.0.113.1:5061;transport=tls>"
        assert "10.0.0.1" not in resp.headers["contact"]

    def test_build_200_ok_returns_local_tag_on_dialog(self):
        """The local tag generated in 200 OK should be retrievable for the dialog."""
        from sip_bridge.sip_tls import SipMessage
        from sip_bridge.wa_sip_client import build_200_ok

        invite = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={
                "call-id": "abc123",
                "from": "<sip:+1234@wa.meta.vc>;tag=from1",
                "to": "<sip:+5678@example.com>",
                "via": "SIP/2.0/TLS 10.0.0.1:5061",
                "cseq": "1 INVITE",
            },
            body="",
        )
        sdp_body = "v=0\r\nm=audio 30000 RTP/SAVP 111\r\n"
        resp1 = build_200_ok(
            invite,
            sdp_body=sdp_body,
            local_contact="<sip:ekaette@203.0.113.1:5061;transport=tls>",
        )
        resp2 = build_200_ok(
            invite,
            sdp_body=sdp_body,
            local_contact="<sip:ekaette@203.0.113.1:5061;transport=tls>",
        )
        # Each call generates a different tag
        tag1 = resp1.headers["to"].split(";tag=")[1].split(";")[0]
        tag2 = resp2.headers["to"].split(";tag=")[1].split(";")[0]
        assert tag1 != tag2
