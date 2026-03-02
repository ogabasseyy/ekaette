"""Tests for SIP dialog helpers — SIP request parsing, G.711 SDP, response building.

TDD Red phase — tests for sip_dialog module.
"""

from __future__ import annotations


# ---- Sample SIP INVITE for tests ----

SAMPLE_INVITE = (
    "INVITE sip:agent1.ekaette@34.69.236.219:6060 SIP/2.0\r\n"
    "Via: SIP/2.0/UDP 196.201.214.100:5060;branch=z9hG4bK776asdhds\r\n"
    "Max-Forwards: 70\r\n"
    "From: <sip:+2348001234567@ng.sip.africastalking.com>;tag=1928301774\r\n"
    "To: <sip:agent1.ekaette@ng.sip.africastalking.com>\r\n"
    "Call-ID: a84b4c76e66710@196.201.214.100\r\n"
    "CSeq: 314159 INVITE\r\n"
    "Contact: <sip:+2348001234567@196.201.214.100:5060>\r\n"
    "Content-Type: application/sdp\r\n"
    "Content-Length: 200\r\n"
    "\r\n"
    "v=0\r\n"
    "o=- 0 0 IN IP4 196.201.214.100\r\n"
    "s=AT Call\r\n"
    "c=IN IP4 196.201.214.100\r\n"
    "t=0 0\r\n"
    "m=audio 30000 RTP/AVP 0 101\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=rtpmap:101 telephone-event/8000\r\n"
    "a=ptime:20\r\n"
    "a=sendrecv\r\n"
)


class TestParseSipRequest:
    """Parse raw SIP requests into method, headers, body."""

    def test_extracts_method(self):
        from sip_bridge.sip_dialog import parse_sip_request

        result = parse_sip_request(SAMPLE_INVITE)
        assert result["method"] == "INVITE"

    def test_extracts_headers(self):
        from sip_bridge.sip_dialog import parse_sip_request

        result = parse_sip_request(SAMPLE_INVITE)
        assert result["headers"]["Call-ID"] == "a84b4c76e66710@196.201.214.100"
        assert result["headers"]["CSeq"] == "314159 INVITE"
        assert "Via" in result["headers"]
        assert "From" in result["headers"]
        assert "To" in result["headers"]

    def test_extracts_sdp_body(self):
        from sip_bridge.sip_dialog import parse_sip_request

        result = parse_sip_request(SAMPLE_INVITE)
        assert "v=0" in result["body"]
        assert "m=audio" in result["body"]

    def test_empty_body_when_no_sdp(self):
        from sip_bridge.sip_dialog import parse_sip_request

        msg = (
            "BYE sip:agent1@example.com SIP/2.0\r\n"
            "Call-ID: test123\r\n"
            "CSeq: 2 BYE\r\n"
            "\r\n"
        )
        result = parse_sip_request(msg)
        assert result["method"] == "BYE"
        assert result["body"] == ""


class TestParseSdpG711:
    """Parse G.711 SDP to extract media IP, port, codec info."""

    def test_extracts_ip_and_port(self):
        from sip_bridge.sip_dialog import parse_sdp_g711

        sdp = (
            "v=0\r\n"
            "o=- 0 0 IN IP4 196.201.214.100\r\n"
            "c=IN IP4 196.201.214.100\r\n"
            "t=0 0\r\n"
            "m=audio 30000 RTP/AVP 0 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
        )
        result = parse_sdp_g711(sdp)
        assert result["media_ip"] == "196.201.214.100"
        assert result["media_port"] == 30000

    def test_pcmu_payload_type_default(self):
        from sip_bridge.sip_dialog import parse_sdp_g711

        sdp = (
            "v=0\r\n"
            "c=IN IP4 10.0.0.1\r\n"
            "m=audio 5000 RTP/AVP 0\r\n"
        )
        result = parse_sdp_g711(sdp)
        assert result["pcmu_payload_type"] == 0

    def test_missing_connection_line_returns_empty_ip(self):
        from sip_bridge.sip_dialog import parse_sdp_g711

        sdp = "v=0\r\nm=audio 5000 RTP/AVP 0\r\n"
        result = parse_sdp_g711(sdp)
        assert result["media_ip"] == ""
        assert result["media_port"] == 5000

    def test_detects_dtmf_payload_type(self):
        from sip_bridge.sip_dialog import parse_sdp_g711

        sdp = (
            "v=0\r\n"
            "c=IN IP4 10.0.0.1\r\n"
            "m=audio 5000 RTP/AVP 0 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
        )
        result = parse_sdp_g711(sdp)
        assert result["dtmf_payload_type"] == 101


class TestBuildSdpAnswer:
    """Build G.711 SDP answer."""

    def test_contains_media_line(self):
        from sip_bridge.sip_dialog import build_sdp_answer

        sdp = build_sdp_answer("34.69.236.219", 12000)
        assert "m=audio 12000 RTP/AVP 0" in sdp

    def test_contains_pcmu_rtpmap(self):
        from sip_bridge.sip_dialog import build_sdp_answer

        sdp = build_sdp_answer("34.69.236.219", 12000)
        assert "a=rtpmap:0 PCMU/8000" in sdp

    def test_contains_connection_line(self):
        from sip_bridge.sip_dialog import build_sdp_answer

        sdp = build_sdp_answer("34.69.236.219", 12000)
        assert "c=IN IP4 34.69.236.219" in sdp

    def test_contains_ptime(self):
        from sip_bridge.sip_dialog import build_sdp_answer

        sdp = build_sdp_answer("34.69.236.219", 12000)
        assert "a=ptime:20" in sdp

    def test_contains_sendrecv(self):
        from sip_bridge.sip_dialog import build_sdp_answer

        sdp = build_sdp_answer("34.69.236.219", 12000)
        assert "a=sendrecv" in sdp


class TestBuildSipResponse:
    """Build SIP response messages (100 Trying, 200 OK)."""

    def _invite_headers(self) -> dict[str, str]:
        return {
            "Via": "SIP/2.0/UDP 196.201.214.100:5060;branch=z9hG4bK776asdhds",
            "From": "<sip:+2348001234567@ng.sip.africastalking.com>;tag=1928301774",
            "To": "<sip:agent1.ekaette@ng.sip.africastalking.com>",
            "Call-ID": "a84b4c76e66710@196.201.214.100",
            "CSeq": "314159 INVITE",
        }

    def test_100_trying_status_line(self):
        from sip_bridge.sip_dialog import build_sip_response

        resp = build_sip_response(
            100, "Trying", self._invite_headers(), sdp_body=None,
            contact_uri="<sip:agent1.ekaette@34.69.236.219:6060>",
        )
        assert resp.startswith("SIP/2.0 100 Trying\r\n")

    def test_100_trying_no_body(self):
        from sip_bridge.sip_dialog import build_sip_response

        resp = build_sip_response(
            100, "Trying", self._invite_headers(), sdp_body=None,
            contact_uri="<sip:agent1.ekaette@34.69.236.219:6060>",
        )
        assert "Content-Type" not in resp
        assert "Content-Length: 0" in resp

    def test_200_ok_with_sdp(self):
        from sip_bridge.sip_dialog import build_sip_response

        sdp = "v=0\r\nc=IN IP4 34.69.236.219\r\nm=audio 12000 RTP/AVP 0\r\n"
        resp = build_sip_response(
            200, "OK", self._invite_headers(), sdp_body=sdp,
            contact_uri="<sip:agent1.ekaette@34.69.236.219:6060>",
        )
        assert resp.startswith("SIP/2.0 200 OK\r\n")
        assert "Content-Type: application/sdp" in resp
        assert f"Content-Length: {len(sdp)}" in resp
        assert resp.endswith("\r\n\r\n" + sdp)

    def test_copies_via_from_to_callid_cseq(self):
        from sip_bridge.sip_dialog import build_sip_response

        headers = self._invite_headers()
        resp = build_sip_response(
            200, "OK", headers, sdp_body=None,
            contact_uri="<sip:agent1.ekaette@34.69.236.219:6060>",
        )
        assert "Via: SIP/2.0/UDP 196.201.214.100:5060;branch=z9hG4bK776asdhds" in resp
        assert 'From: <sip:+2348001234567@ng.sip.africastalking.com>;tag=1928301774' in resp
        assert "Call-ID: a84b4c76e66710@196.201.214.100" in resp
        assert "CSeq: 314159 INVITE" in resp

    def test_to_header_gets_tag(self):
        from sip_bridge.sip_dialog import build_sip_response

        headers = self._invite_headers()
        resp = build_sip_response(
            200, "OK", headers, sdp_body=None,
            contact_uri="<sip:agent1.ekaette@34.69.236.219:6060>",
        )
        # To header should have a tag added
        for line in resp.split("\r\n"):
            if line.startswith("To:"):
                assert ";tag=" in line
                break
        else:
            raise AssertionError("No To: header found in response")

    def test_contact_header_included(self):
        from sip_bridge.sip_dialog import build_sip_response

        resp = build_sip_response(
            200, "OK", self._invite_headers(), sdp_body=None,
            contact_uri="<sip:agent1.ekaette@34.69.236.219:6060>",
        )
        assert "Contact: <sip:agent1.ekaette@34.69.236.219:6060>" in resp
