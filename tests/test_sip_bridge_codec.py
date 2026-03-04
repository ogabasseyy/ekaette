"""TDD tests for SIP bridge audio codec and RTP handling.

Covers: G.711 μ-law encode/decode roundtrip, resampling,
RTP packet parse/serialize, and frame timing.
"""

from __future__ import annotations

import struct

from sip_bridge.audio_codec import (
    alaw_to_pcm16,
    ulaw_to_pcm16,
    pcm16_to_ulaw,
    resample_8k_to_16k,
    resample_24k_to_8k,
)
from sip_bridge.rtp import (
    PCMA_PAYLOAD_TYPE,
    PCMU_PAYLOAD_TYPE,
    RTPPacket,
    RTPTimer,
)


# ── G.711 μ-law Codec ──


class TestUlawCodec:
    """G.711 μ-law encode/decode correctness."""

    def test_roundtrip_silence(self) -> None:
        """Silence (0x7F in μ-law) roundtrips cleanly."""
        silence = bytes([0x7F] * 160)
        pcm = ulaw_to_pcm16(silence)
        assert len(pcm) == 320  # 160 samples * 2 bytes

    def test_roundtrip_preserves_shape(self) -> None:
        """Encode → decode → encode preserves most μ-law values.

        G.711 μ-law sign-magnitude: 0x7F and 0xFF both decode to 0,
        so perfect roundtrip is not expected for all 256 values.
        """
        original = bytes(range(256))
        pcm = ulaw_to_pcm16(original)
        re_encoded = pcm16_to_ulaw(pcm)
        mismatches = sum(a != b for a, b in zip(original, re_encoded))
        assert mismatches <= 2

    def test_output_length_correct(self) -> None:
        """PCM16 output is exactly 2x the μ-law input length."""
        ulaw = bytes([0x00] * 80)
        pcm = ulaw_to_pcm16(ulaw)
        assert len(pcm) == 160

    def test_encode_clipping(self) -> None:
        """PCM values beyond clip range don't crash."""
        # Max positive PCM16 value
        pcm = struct.pack("<h", 32767)
        result = pcm16_to_ulaw(pcm)
        assert len(result) == 1


class TestAlawCodec:
    """G.711 A-law encode/decode correctness."""

    def test_roundtrip_silence(self) -> None:
        silence = bytes([0x55] * 160)
        pcm = alaw_to_pcm16(silence)
        assert len(pcm) == 320

    def test_output_length_correct(self) -> None:
        alaw = bytes([0x55] * 80)
        pcm = alaw_to_pcm16(alaw)
        assert len(pcm) == 160


# ── Resampling ──


class TestResampling:
    """Sample rate conversion for Gemini Live compatibility."""

    def test_8k_to_16k_doubles_length(self) -> None:
        """Upsampling from 8kHz to 16kHz roughly doubles sample count."""
        pcm_8k = struct.pack("<10h", *range(10))
        pcm_16k = resample_8k_to_16k(pcm_8k)
        assert len(pcm_16k) // 2 == 20  # 10 samples → 20 samples

    def test_24k_to_8k_thirds_length(self) -> None:
        """Downsampling from 24kHz to 8kHz takes every 3rd sample."""
        pcm_24k = struct.pack("<9h", *range(9))
        pcm_8k = resample_24k_to_8k(pcm_24k)
        n_out = len(pcm_8k) // 2
        assert n_out == 3  # 9 / 3 = 3

    def test_empty_input_returns_empty(self) -> None:
        assert resample_8k_to_16k(b"") == b""
        assert resample_24k_to_8k(b"") == b""


# ── RTP Packet ──


class TestRTPPacket:
    """RTP packet parse and serialize."""

    def _make_rtp(
        self, *, pt: int = 0, seq: int = 1, ts: int = 160, ssrc: int = 12345,
        payload: bytes = b"\x00" * 160,
    ) -> bytes:
        byte0 = 0x80  # V=2
        byte1 = pt & 0x7F
        return struct.pack("!BBHII", byte0, byte1, seq, ts, ssrc) + payload

    def test_parse_valid_packet(self) -> None:
        raw = self._make_rtp(seq=42, ts=320)
        pkt = RTPPacket.parse(raw)
        assert pkt is not None
        assert pkt.version == 2
        assert pkt.sequence == 42
        assert pkt.timestamp == 320
        assert pkt.payload_type == PCMU_PAYLOAD_TYPE

    def test_parse_pcma_payload_type(self) -> None:
        raw = self._make_rtp(pt=PCMA_PAYLOAD_TYPE)
        pkt = RTPPacket.parse(raw)
        assert pkt is not None
        assert pkt.payload_type == PCMA_PAYLOAD_TYPE

    def test_parse_too_short_returns_none(self) -> None:
        assert RTPPacket.parse(b"\x00" * 5) is None

    def test_parse_wrong_version_returns_none(self) -> None:
        # V=1 instead of V=2
        raw = b"\x40" + b"\x00" * 11 + b"\x00" * 160
        assert RTPPacket.parse(raw) is None

    def test_serialize_roundtrip(self) -> None:
        raw = self._make_rtp(seq=100, ts=16000, ssrc=99999)
        pkt = RTPPacket.parse(raw)
        assert pkt is not None
        reserialized = pkt.serialize()
        assert reserialized == raw

    def test_payload_extraction(self) -> None:
        payload = b"\xAA\xBB\xCC"
        raw = self._make_rtp(payload=payload)
        pkt = RTPPacket.parse(raw)
        assert pkt is not None
        assert pkt.payload == payload


# ── RTP Timer ──


class TestRTPTimer:
    """20ms frame timing with drift correction."""

    def test_first_deadline_is_20ms_ahead(self) -> None:
        timer = RTPTimer()
        deadline = timer.next_deadline()
        assert deadline > timer._start
        assert abs(deadline - timer._start - 0.02) < 0.001

    def test_deadlines_increment_by_20ms(self) -> None:
        timer = RTPTimer()
        d1 = timer.next_deadline()
        d2 = timer.next_deadline()
        assert abs((d2 - d1) - 0.02) < 0.001

    def test_drift_correction_over_many_frames(self) -> None:
        """Timer uses absolute offsets, not relative sleeps — no drift."""
        timer = RTPTimer()
        for _ in range(100):
            timer.next_deadline()
        last = timer.next_deadline()
        expected = timer._start + (101 * 0.02)
        assert abs(last - expected) < 0.0001
