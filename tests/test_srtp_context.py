"""Tests for SDES/SRTP context (parsing + protect/unprotect).

TDD Red phase — these tests should FAIL until srtp_context.py is implemented.
"""

from __future__ import annotations

import os
import struct

import pytest

try:
    __import__("pylibsrtp")
    _has_pylibsrtp = True
except ImportError:
    _has_pylibsrtp = False


class TestSDESParsing:
    """Parse a=crypto lines from SDP."""

    def test_parse_single_crypto_line(self):
        import base64

        from sip_bridge.srtp_context import parse_sdes_crypto

        raw_key = os.urandom(30)
        b64_key = base64.b64encode(raw_key).decode()
        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111\r\n"
            f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{b64_key}\r\n"
        )
        result = parse_sdes_crypto(sdp)
        assert result is not None
        assert result["tag"] == 1
        assert result["suite"] == "AES_CM_128_HMAC_SHA1_80"
        assert "key" in result
        assert result["key"] == raw_key

    def test_parse_no_crypto_line(self):
        from sip_bridge.srtp_context import parse_sdes_crypto

        sdp = "v=0\r\nm=audio 3480 RTP/AVP 0\r\n"
        result = parse_sdes_crypto(sdp)
        assert result is None

    def test_parse_multiple_crypto_lines_returns_first(self):
        import base64

        from sip_bridge.srtp_context import parse_sdes_crypto

        key1 = base64.b64encode(os.urandom(30)).decode()
        key2 = base64.b64encode(os.urandom(30)).decode()
        sdp = (
            "v=0\r\n"
            "m=audio 3480 RTP/SAVP 111\r\n"
            f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{key1}\r\n"
            f"a=crypto:2 AES_CM_128_HMAC_SHA1_32 inline:{key2}\r\n"
        )
        result = parse_sdes_crypto(sdp)
        assert result is not None
        assert result["tag"] == 1

    def test_parse_extracts_base64_key(self):
        """Key material after 'inline:' should be base64-decoded."""
        from sip_bridge.srtp_context import parse_sdes_crypto

        import base64

        raw_key = os.urandom(30)  # SRTP master key (16) + salt (14)
        b64_key = base64.b64encode(raw_key).decode()
        sdp = f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{b64_key}\r\n"
        result = parse_sdes_crypto(sdp)
        assert result is not None
        assert result["key"] == raw_key


@pytest.mark.skipif(not _has_pylibsrtp, reason="pylibsrtp not installed")
class TestSRTPContext:
    """SRTP protect/unprotect roundtrip using pylibsrtp."""

    def _make_context_pair(self):
        """Create a matched sender/receiver SRTP context pair."""
        from sip_bridge.srtp_context import SRTPContext

        # Generate random SRTP master key (16 bytes) + salt (14 bytes)
        key_material = os.urandom(30)
        sender = SRTPContext(key_material=key_material, is_sender=True)
        receiver = SRTPContext(key_material=key_material, is_sender=False)
        return sender, receiver

    def _make_rtp_packet(self, seq: int = 1, ts: int = 960, ssrc: int = 12345, payload_size: int = 160) -> bytes:
        """Create a minimal RTP packet."""
        byte0 = 0x80  # V=2
        byte1 = 111  # PT=111 (Opus)
        header = struct.pack("!BBHII", byte0, byte1, seq, ts, ssrc)
        return header + os.urandom(payload_size)

    def test_protect_returns_bytes(self):
        sender, _ = self._make_context_pair()
        rtp = self._make_rtp_packet()
        srtp = sender.protect(rtp)
        assert isinstance(srtp, bytes)

    def test_protect_adds_auth_tag(self):
        """SRTP adds a 10-byte auth tag (HMAC-SHA1-80)."""
        sender, _ = self._make_context_pair()
        rtp = self._make_rtp_packet()
        srtp = sender.protect(rtp)
        # SRTP = RTP + auth_tag (10 bytes for HMAC-SHA1-80)
        assert len(srtp) == len(rtp) + 10

    def test_unprotect_returns_original_rtp(self):
        sender, receiver = self._make_context_pair()
        rtp = self._make_rtp_packet()
        srtp = sender.protect(rtp)
        recovered = receiver.unprotect(srtp)
        assert recovered == rtp

    def test_roundtrip_multiple_packets(self):
        sender, receiver = self._make_context_pair()
        for seq in range(1, 20):
            rtp = self._make_rtp_packet(seq=seq, ts=seq * 960)
            srtp = sender.protect(rtp)
            recovered = receiver.unprotect(srtp)
            assert recovered == rtp

    def test_unprotect_tampered_fails(self):
        from sip_bridge.srtp_context import SRTPError

        sender, receiver = self._make_context_pair()
        rtp = self._make_rtp_packet()
        srtp = sender.protect(rtp)
        # Tamper with the payload
        tampered = bytearray(srtp)
        tampered[20] ^= 0xFF
        with pytest.raises(SRTPError):
            receiver.unprotect(bytes(tampered))

    def test_different_keys_fail(self):
        from sip_bridge.srtp_context import SRTPContext, SRTPError

        key1 = os.urandom(30)
        key2 = os.urandom(30)
        sender = SRTPContext(key_material=key1, is_sender=True)
        receiver = SRTPContext(key_material=key2, is_sender=False)
        rtp = self._make_rtp_packet()
        srtp = sender.protect(rtp)
        with pytest.raises(SRTPError):
            receiver.unprotect(srtp)

    def test_protect_preserves_rtp_header(self):
        """SRTP should preserve the RTP header bytes."""
        sender, _ = self._make_context_pair()
        rtp = self._make_rtp_packet()
        srtp = sender.protect(rtp)
        # First 12 bytes (RTP header) should be identical
        # (payload is encrypted, but header is not in SRTP)
        assert srtp[:12] == rtp[:12]

    def test_context_from_sdp(self):
        """Create SRTP context from parsed SDP crypto line."""
        from sip_bridge.srtp_context import SRTPContext, parse_sdes_crypto

        import base64

        key_material = os.urandom(30)
        b64_key = base64.b64encode(key_material).decode()
        sdp = f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{b64_key}\r\n"
        crypto = parse_sdes_crypto(sdp)
        assert crypto is not None
        ctx = SRTPContext(key_material=crypto["key"], is_sender=True)
        rtp = self._make_rtp_packet()
        srtp = ctx.protect(rtp)
        assert len(srtp) > len(rtp)

    def test_generate_key_material(self):
        """Generate random key material for our SDP answer."""
        from sip_bridge.srtp_context import generate_key_material

        key = generate_key_material()
        assert isinstance(key, bytes)
        assert len(key) == 30  # 16 master key + 14 salt

    def test_format_crypto_line(self):
        """Format a=crypto line for SDP answer."""
        from sip_bridge.srtp_context import format_crypto_line, generate_key_material

        key = generate_key_material()
        line = format_crypto_line(tag=1, key_material=key)
        assert line.startswith("a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:")
        assert "\r\n" not in line  # No line ending in the value itself


class TestSDESValidation:
    """Fail-fast validation for unsupported suites and bad key lengths."""

    def test_unsupported_suite_raises(self):
        """parse_sdes_crypto rejects unsupported cipher suites."""
        from sip_bridge.srtp_context import SRTPError, parse_sdes_crypto

        import base64

        key = base64.b64encode(os.urandom(30)).decode()
        sdp = f"a=crypto:1 AES_256_CM_HMAC_SHA1_80 inline:{key}\r\n"
        with pytest.raises(SRTPError, match="[Uu]nsupported.*suite"):
            parse_sdes_crypto(sdp)

    def test_wrong_key_length_raises(self):
        """parse_sdes_crypto rejects key material that isn't 30 bytes."""
        from sip_bridge.srtp_context import SRTPError, parse_sdes_crypto

        import base64

        bad_key = base64.b64encode(os.urandom(16)).decode()  # 16 bytes, not 30
        sdp = f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{bad_key}\r\n"
        with pytest.raises(SRTPError, match="[Kk]ey.*length"):
            parse_sdes_crypto(sdp)

    def test_valid_80_suite_accepted(self):
        """AES_CM_128_HMAC_SHA1_80 with 30-byte key is accepted."""
        from sip_bridge.srtp_context import parse_sdes_crypto

        import base64

        key = os.urandom(30)
        b64 = base64.b64encode(key).decode()
        sdp = f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{b64}\r\n"
        result = parse_sdes_crypto(sdp)
        assert result is not None
        assert result["key"] == key

    def test_valid_32_suite_accepted(self):
        """AES_CM_128_HMAC_SHA1_32 with 30-byte key is accepted."""
        from sip_bridge.srtp_context import parse_sdes_crypto

        import base64

        key = os.urandom(30)
        b64 = base64.b64encode(key).decode()
        sdp = f"a=crypto:1 AES_CM_128_HMAC_SHA1_32 inline:{b64}\r\n"
        result = parse_sdes_crypto(sdp)
        assert result is not None

    def test_srtp_context_rejects_bad_key_length(self):
        """SRTPContext constructor rejects non-30-byte key material."""
        from sip_bridge.srtp_context import SRTPContext, SRTPError

        with pytest.raises(SRTPError, match="[Kk]ey.*length"):
            SRTPContext(key_material=os.urandom(16), is_sender=True)
