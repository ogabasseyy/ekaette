"""Tests for SIP TLS transport (sip_tls.py).

TDD Red phase — covers:
- SIP message parsing from TCP stream (framing)
- Header normalization (case-insensitive, compact forms)
- Size limits (64KB message, 100 headers)
- Content-Length validation
- SipMessage dataclass
"""

from __future__ import annotations

import asyncio

import pytest


# --- SipMessage dataclass tests ---


class TestSipMessage:
    """SipMessage holds parsed SIP request/response."""

    def test_request_first_line(self):
        from sip_bridge.sip_tls import SipMessage

        msg = SipMessage(
            first_line="INVITE sip:+2348001234567@wa.meta.vc SIP/2.0",
            headers={"call-id": "abc123", "from": "<sip:test>"},
            body="",
        )
        assert msg.method == "INVITE"
        assert msg.is_request is True

    def test_response_first_line(self):
        from sip_bridge.sip_tls import SipMessage

        msg = SipMessage(
            first_line="SIP/2.0 407 Proxy Authentication Required",
            headers={"call-id": "abc123"},
            body="",
        )
        assert msg.status_code == 407
        assert msg.is_request is False

    def test_response_200(self):
        from sip_bridge.sip_tls import SipMessage

        msg = SipMessage(
            first_line="SIP/2.0 200 OK",
            headers={"call-id": "abc123"},
            body="",
        )
        assert msg.status_code == 200

    def test_headers_lowercase(self):
        from sip_bridge.sip_tls import SipMessage

        msg = SipMessage(
            first_line="SIP/2.0 200 OK",
            headers={"call-id": "abc123", "content-length": "0"},
            body="",
        )
        assert msg.headers["call-id"] == "abc123"

    def test_body_access(self):
        from sip_bridge.sip_tls import SipMessage

        sdp = "v=0\r\nm=audio 3480 RTP/SAVP 111\r\n"
        msg = SipMessage(
            first_line="SIP/2.0 200 OK",
            headers={"content-length": str(len(sdp))},
            body=sdp,
        )
        assert msg.body == sdp


# --- Header normalization tests ---


class TestHeaderNormalization:
    """SIP headers are case-insensitive and have compact forms."""

    def test_normalize_mixed_case(self):
        from sip_bridge.sip_tls import normalize_header_name

        assert normalize_header_name("Call-ID") == "call-id"
        assert normalize_header_name("Content-Length") == "content-length"
        assert normalize_header_name("CONTACT") == "contact"

    def test_compact_form_call_id(self):
        from sip_bridge.sip_tls import normalize_header_name

        assert normalize_header_name("i") == "call-id"

    def test_compact_form_from(self):
        from sip_bridge.sip_tls import normalize_header_name

        assert normalize_header_name("f") == "from"

    def test_compact_form_to(self):
        from sip_bridge.sip_tls import normalize_header_name

        assert normalize_header_name("t") == "to"

    def test_compact_form_via(self):
        from sip_bridge.sip_tls import normalize_header_name

        assert normalize_header_name("v") == "via"

    def test_compact_form_contact(self):
        from sip_bridge.sip_tls import normalize_header_name

        assert normalize_header_name("m") == "contact"

    def test_compact_form_content_length(self):
        from sip_bridge.sip_tls import normalize_header_name

        assert normalize_header_name("l") == "content-length"


# --- Stream parser tests (mock asyncio streams) ---


class TestParseMessageFromStream:
    """Parse SIP messages from async TCP stream."""

    def _make_reader(self, data: bytes) -> asyncio.StreamReader:
        """Create a StreamReader pre-loaded with data."""
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return reader

    async def test_parse_simple_request(self):
        from sip_bridge.sip_tls import parse_message

        raw = (
            b"INVITE sip:+2348001234567@wa.meta.vc SIP/2.0\r\n"
            b"Call-ID: abc123\r\n"
            b"From: <sip:test>\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        reader = self._make_reader(raw)
        msg = await parse_message(reader)
        assert msg is not None
        assert msg.method == "INVITE"
        assert msg.headers["call-id"] == "abc123"
        assert msg.headers["from"] == "<sip:test>"
        assert msg.body == ""

    async def test_parse_response_with_body(self):
        from sip_bridge.sip_tls import parse_message

        body = "v=0\r\nm=audio 3480 RTP/SAVP 111\r\n"
        raw = (
            b"SIP/2.0 200 OK\r\n"
            b"Call-ID: abc123\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n"
            + body.encode()
        )
        reader = self._make_reader(raw)
        msg = await parse_message(reader)
        assert msg is not None
        assert msg.status_code == 200
        assert msg.body == body

    async def test_parse_compact_headers(self):
        """Compact header forms (i, f, t, v) are expanded."""
        from sip_bridge.sip_tls import parse_message

        raw = (
            b"BYE sip:test@example.com SIP/2.0\r\n"
            b"i: call-xyz\r\n"
            b"f: <sip:from@example.com>\r\n"
            b"t: <sip:to@example.com>\r\n"
            b"l: 0\r\n"
            b"\r\n"
        )
        reader = self._make_reader(raw)
        msg = await parse_message(reader)
        assert msg is not None
        assert msg.headers["call-id"] == "call-xyz"
        assert msg.headers["from"] == "<sip:from@example.com>"
        assert msg.headers["to"] == "<sip:to@example.com>"

    async def test_parse_returns_none_on_eof(self):
        from sip_bridge.sip_tls import parse_message

        reader = self._make_reader(b"")
        msg = await parse_message(reader)
        assert msg is None

    async def test_parse_multiple_messages(self):
        """Parse two messages from the same stream."""
        from sip_bridge.sip_tls import parse_message

        msg1 = (
            b"INVITE sip:test SIP/2.0\r\n"
            b"Call-ID: call-1\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        msg2 = (
            b"BYE sip:test SIP/2.0\r\n"
            b"Call-ID: call-2\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        reader = self._make_reader(msg1 + msg2)
        m1 = await parse_message(reader)
        m2 = await parse_message(reader)
        assert m1 is not None and m1.headers["call-id"] == "call-1"
        assert m2 is not None and m2.headers["call-id"] == "call-2"

    async def test_parse_repeated_via_headers_preserves_all_hops(self):
        from sip_bridge.sip_tls import parse_message

        raw = (
            b"INVITE sip:test@example.com SIP/2.0\r\n"
            b"Via: SIP/2.0/TLS edge1.example.com:5061;branch=z9hG4bK-1\r\n"
            b"Via: SIP/2.0/TLS edge2.example.com:5061;branch=z9hG4bK-2\r\n"
            b"Call-ID: call-1\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        reader = self._make_reader(raw)
        msg = await parse_message(reader)
        assert msg is not None
        assert msg.headers["via"] == (
            "SIP/2.0/TLS edge1.example.com:5061;branch=z9hG4bK-1\n"
            "SIP/2.0/TLS edge2.example.com:5061;branch=z9hG4bK-2"
        )


# --- Security limits tests ---


class TestSecurityLimits:
    """Enforce size and header count limits."""

    def _make_reader(self, data: bytes) -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return reader

    async def test_reject_oversized_message(self):
        """Messages exceeding 64KB should be rejected."""
        from sip_bridge.sip_tls import SipTransportError, parse_message

        # Create a message with a body claiming >64KB
        body = "x" * 70000
        raw = (
            b"INVITE sip:test SIP/2.0\r\n"
            b"Call-ID: abc\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n"
            + body.encode()
        )
        reader = self._make_reader(raw)
        with pytest.raises(SipTransportError, match="[Ss]ize|64"):
            await parse_message(reader)

    async def test_reject_too_many_headers(self):
        """Messages with >100 headers should be rejected."""
        from sip_bridge.sip_tls import SipTransportError, parse_message

        lines = [b"INVITE sip:test SIP/2.0\r\n"]
        for i in range(105):
            lines.append(f"X-Header-{i}: value{i}\r\n".encode())
        lines.append(b"Content-Length: 0\r\n")
        lines.append(b"\r\n")
        raw = b"".join(lines)
        reader = self._make_reader(raw)
        with pytest.raises(SipTransportError, match="[Hh]eader.*100"):
            await parse_message(reader)

    async def test_reject_missing_content_length_with_body(self):
        """Reject if Content-Length is absent but there's trailing data."""
        from sip_bridge.sip_tls import parse_message

        raw = (
            b"INVITE sip:test SIP/2.0\r\n"
            b"Call-ID: abc\r\n"
            b"\r\n"
        )
        reader = self._make_reader(raw)
        msg = await parse_message(reader)
        # No Content-Length means body is empty (not an error for bodyless messages)
        assert msg is not None
        assert msg.body == ""

    async def test_100_headers_accepted(self):
        """Exactly 100 headers should be fine."""
        from sip_bridge.sip_tls import parse_message

        lines = [b"INVITE sip:test SIP/2.0\r\n"]
        for i in range(99):
            lines.append(f"X-Header-{i}: value{i}\r\n".encode())
        lines.append(b"Content-Length: 0\r\n")
        lines.append(b"\r\n")
        raw = b"".join(lines)
        reader = self._make_reader(raw)
        msg = await parse_message(reader)
        assert msg is not None

    async def test_reject_malformed_header_line(self):
        """Lines without ':' in headers should be rejected, not silently skipped."""
        from sip_bridge.sip_tls import SipTransportError, parse_message

        raw = (
            b"INVITE sip:test SIP/2.0\r\n"
            b"Call-ID: abc\r\n"
            b"THIS LINE HAS NO COLON\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        reader = self._make_reader(raw)
        with pytest.raises(SipTransportError, match="[Mm]alformed"):
            await parse_message(reader)

    async def test_reject_invalid_content_length(self):
        """Non-numeric Content-Length should be rejected, not coerced to 0."""
        from sip_bridge.sip_tls import SipTransportError, parse_message

        raw = (
            b"INVITE sip:test SIP/2.0\r\n"
            b"Call-ID: abc\r\n"
            b"Content-Length: not-a-number\r\n"
            b"\r\n"
        )
        reader = self._make_reader(raw)
        with pytest.raises(SipTransportError, match="[Cc]ontent-[Ll]ength"):
            await parse_message(reader)

    async def test_reject_negative_content_length(self):
        """Negative Content-Length should be rejected."""
        from sip_bridge.sip_tls import SipTransportError, parse_message

        raw = (
            b"INVITE sip:test SIP/2.0\r\n"
            b"Call-ID: abc\r\n"
            b"Content-Length: -1\r\n"
            b"\r\n"
        )
        reader = self._make_reader(raw)
        with pytest.raises(SipTransportError, match="[Cc]ontent-[Ll]ength"):
            await parse_message(reader)


# --- SIP message serialization tests ---


class TestSerializeMessage:
    """Serialize SipMessage back to bytes for sending."""

    def test_serialize_request(self):
        from sip_bridge.sip_tls import SipMessage, serialize_message

        msg = SipMessage(
            first_line="INVITE sip:test@example.com SIP/2.0",
            headers={"call-id": "abc123", "from": "<sip:test>", "content-length": "0"},
            body="",
        )
        raw = serialize_message(msg)
        assert raw.startswith(b"INVITE sip:test@example.com SIP/2.0\r\n")
        assert b"\r\n\r\n" in raw
        assert b"Call-ID: abc123\r\n" in raw

    def test_serialize_multivalue_via_headers(self):
        from sip_bridge.sip_tls import SipMessage, serialize_message

        msg = SipMessage(
            first_line="SIP/2.0 407 Proxy Authentication Required",
            headers={
                "via": (
                    "SIP/2.0/TLS edge1.example.com:5061;branch=z9hG4bK-1\n"
                    "SIP/2.0/TLS edge2.example.com:5061;branch=z9hG4bK-2"
                ),
                "call-id": "abc123",
                "cseq": "1 INVITE",
            },
            body="",
        )
        raw = serialize_message(msg)
        assert b"Via: SIP/2.0/TLS edge1.example.com:5061;branch=z9hG4bK-1\r\n" in raw
        assert b"Via: SIP/2.0/TLS edge2.example.com:5061;branch=z9hG4bK-2\r\n" in raw
        assert b"CSeq: 1 INVITE\r\n" in raw

    def test_serialize_skips_empty_multivalue_headers(self):
        from sip_bridge.sip_tls import SipMessage, serialize_message

        msg = SipMessage(
            first_line="SIP/2.0 200 OK",
            headers={
                "via": (
                    "SIP/2.0/TLS edge1.example.com:5061;branch=z9hG4bK-1\n\n"
                    "SIP/2.0/TLS edge2.example.com:5061;branch=z9hG4bK-2"
                ),
                "call-id": "abc123",
            },
            body="",
        )

        raw = serialize_message(msg)
        assert raw.count(b"Via: ") == 2
        assert b"Via: \r\n" not in raw

    def test_serialize_response_with_body(self):
        from sip_bridge.sip_tls import SipMessage, serialize_message

        body = "v=0\r\nm=audio 3480 RTP/SAVP 111\r\n"
        msg = SipMessage(
            first_line="SIP/2.0 200 OK",
            headers={"call-id": "abc123", "content-length": str(len(body))},
            body=body,
        )
        raw = serialize_message(msg)
        assert raw.endswith(body.encode())
        assert b"Content-Length: " in raw
