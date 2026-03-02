"""Codec bridge abstraction for channel-agnostic audio processing.

Provides a unified interface for G.711 (AT phone) and Opus (WhatsApp)
codec operations, isolating codec details from session management.

All bridges:
- decode_to_pcm16_16k: encoded frame -> PCM16 16kHz (Gemini input)
- encode_from_pcm16_24k: PCM16 24kHz (Gemini output) -> encoded frame
"""

from __future__ import annotations

import abc
import struct


class CodecBridge(abc.ABC):
    """Abstract codec bridge — decode inbound, encode outbound."""

    rtp_payload_type: int
    rtp_clock_rate: int
    frame_duration_ms: int = 20

    @abc.abstractmethod
    def decode_to_pcm16_16k(self, encoded: bytes) -> bytes:
        """Decode an encoded audio frame to PCM16 mono 16kHz."""

    @abc.abstractmethod
    def encode_from_pcm16_24k(self, pcm16_24k: bytes) -> bytes:
        """Encode PCM16 mono 24kHz to the wire codec format."""


# ---------------------------------------------------------------------------
# Resample helpers (linear interpolation, same approach as audio_codec.py)
# ---------------------------------------------------------------------------


def resample_24k_to_16k(pcm16_24k: bytes) -> bytes:
    """Downsample PCM16 from 24kHz to 16kHz (linear interpolation).

    Ratio: 24000/16000 = 3/2. For every 3 input samples, produce 2 output.
    """
    n = len(pcm16_24k) // 2
    if n == 0:
        return b""
    samples = struct.unpack(f"<{n}h", pcm16_24k)
    n_out = (n * 2) // 3
    out = []
    for i in range(n_out):
        # Map output index to fractional input index
        src = i * 1.5
        idx = int(src)
        frac = src - idx
        if idx + 1 < n:
            val = int(samples[idx] * (1.0 - frac) + samples[idx + 1] * frac)
        else:
            val = samples[min(idx, n - 1)]
        # Clamp to int16 range
        val = max(-32768, min(32767, val))
        out.append(val)
    return struct.pack(f"<{len(out)}h", *out)


def resample_16k_to_24k(pcm16_16k: bytes) -> bytes:
    """Upsample PCM16 from 16kHz to 24kHz (linear interpolation).

    Ratio: 24000/16000 = 3/2. For every 2 input samples, produce 3 output.
    """
    n = len(pcm16_16k) // 2
    if n == 0:
        return b""
    samples = struct.unpack(f"<{n}h", pcm16_16k)
    n_out = (n * 3) // 2
    out = []
    for i in range(n_out):
        src = i * (2.0 / 3.0)
        idx = int(src)
        frac = src - idx
        if idx + 1 < n:
            val = int(samples[idx] * (1.0 - frac) + samples[idx + 1] * frac)
        else:
            val = samples[min(idx, n - 1)]
        val = max(-32768, min(32767, val))
        out.append(val)
    return struct.pack(f"<{len(out)}h", *out)


# ---------------------------------------------------------------------------
# G711CodecBridge — wraps existing audio_codec.py
# ---------------------------------------------------------------------------


class G711CodecBridge(CodecBridge):
    """G.711 μ-law codec bridge for AT phone calls."""

    frame_duration_ms: int = 20

    def __init__(self, rtp_payload_type: int = 0, rtp_clock_rate: int = 8000) -> None:
        self.rtp_payload_type = rtp_payload_type
        self.rtp_clock_rate = rtp_clock_rate

    def decode_to_pcm16_16k(self, encoded: bytes) -> bytes:
        """G.711 μ-law -> PCM16 8kHz -> resample -> PCM16 16kHz."""
        from sip_bridge.audio_codec import resample_8k_to_16k, ulaw_to_pcm16

        pcm16_8k = ulaw_to_pcm16(encoded)
        return resample_8k_to_16k(pcm16_8k)

    def encode_from_pcm16_24k(self, pcm16_24k: bytes) -> bytes:
        """PCM16 24kHz -> resample -> PCM16 8kHz -> G.711 μ-law."""
        from sip_bridge.audio_codec import pcm16_to_ulaw, resample_24k_to_8k

        pcm16_8k = resample_24k_to_8k(pcm16_24k)
        return pcm16_to_ulaw(pcm16_8k)


# ---------------------------------------------------------------------------
# OpusCodecBridge — uses opuslib_next with SDP-derived rates
# ---------------------------------------------------------------------------


class OpusCodecBridge(CodecBridge):
    """Opus codec bridge for WhatsApp calls.

    Encoder rate is SDP-derived (from maxplaybackrate fmtp parameter).
    Decoder always outputs 16kHz (Gemini input rate).
    """

    frame_duration_ms: int = 20

    def __init__(
        self,
        rtp_payload_type: int = 111,
        rtp_clock_rate: int = 48000,
        encode_rate: int = 16000,
    ) -> None:
        self.rtp_payload_type = rtp_payload_type
        self.rtp_clock_rate = rtp_clock_rate
        self.encode_rate = encode_rate

        from opuslib_next import APPLICATION_VOIP, Decoder, Encoder

        # Decoder at 16kHz — Gemini input rate
        self._decoder = Decoder(fs=16000, channels=1)
        # Encoder at SDP-negotiated rate, VOIP application for speech
        self._encoder = Encoder(fs=encode_rate, channels=1, application=APPLICATION_VOIP)

        # Frame size in samples at the encoder rate for 20ms
        self._encode_frame_samples = encode_rate * self.frame_duration_ms // 1000
        # Frame size in samples at decoder rate (16kHz) for 20ms
        self._decode_frame_samples = 16000 * self.frame_duration_ms // 1000  # 320

    def decode_to_pcm16_16k(self, encoded: bytes) -> bytes:
        """Opus -> PCM16 16kHz (libopus handles rate conversion natively)."""
        return self._decoder.decode(encoded, self._decode_frame_samples)

    def encode_from_pcm16_24k(self, pcm16_24k: bytes) -> bytes:
        """PCM16 24kHz -> resample to encode_rate -> Opus encode."""
        if self.encode_rate == 24000:
            pcm_input = pcm16_24k
        elif self.encode_rate == 16000:
            pcm_input = resample_24k_to_16k(pcm16_24k)
        else:
            # Generic resample path (not expected for Meta, but safe)
            pcm_input = self._resample_24k_to_rate(pcm16_24k, self.encode_rate)

        return self._encoder.encode(pcm_input, self._encode_frame_samples)

    def _resample_24k_to_rate(self, pcm16_24k: bytes, target_rate: int) -> bytes:
        """Generic resample from 24kHz to arbitrary rate (linear interpolation)."""
        n = len(pcm16_24k) // 2
        if n == 0:
            return b""
        samples = struct.unpack(f"<{n}h", pcm16_24k)
        ratio = target_rate / 24000
        n_out = int(n * ratio)
        out = []
        for i in range(n_out):
            src = i / ratio
            idx = int(src)
            frac = src - idx
            if idx + 1 < n:
                val = int(samples[idx] * (1.0 - frac) + samples[idx + 1] * frac)
            else:
                val = samples[min(idx, n - 1)]
            val = max(-32768, min(32767, val))
            out.append(val)
        return struct.pack(f"<{len(out)}h", *out)
