"""G.711 μ-law 8kHz ↔ PCM16 16kHz/24kHz codec conversion.

Adapted from sip-to-ai (Apache 2.0).
"""

from __future__ import annotations

import struct

# G.711 μ-law decode table (256 entries)
_ULAW_DECODE: list[int] = []


def _build_ulaw_table() -> None:
    """Build μ-law to linear PCM16 lookup table."""
    global _ULAW_DECODE
    if _ULAW_DECODE:
        return
    for i in range(256):
        val = ~i
        sign = val & 0x80
        exponent = (val >> 4) & 0x07
        mantissa = val & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        _ULAW_DECODE.append(-sample if sign else sample)


_build_ulaw_table()


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """Convert G.711 μ-law bytes to PCM16 linear (same sample rate)."""
    samples = [_ULAW_DECODE[b] for b in ulaw_bytes]
    return struct.pack(f"<{len(samples)}h", *samples)


def pcm16_to_ulaw(pcm16_bytes: bytes) -> bytes:
    """Convert PCM16 linear bytes to G.711 μ-law."""
    n_samples = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm16_bytes)
    result = bytearray(n_samples)
    for i, sample in enumerate(samples):
        result[i] = _linear_to_ulaw(sample)
    return bytes(result)


def _linear_to_ulaw(sample: int) -> int:
    """Encode a single PCM16 sample to μ-law."""
    BIAS = 0x84
    CLIP = 32635

    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    if sample > CLIP:
        sample = CLIP
    sample += BIAS

    exponent = 7
    exp_mask = 0x4000
    while exponent > 0 and not (sample & exp_mask):
        exponent -= 1
        exp_mask >>= 1

    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def resample_8k_to_16k(pcm16_8k: bytes) -> bytes:
    """Upsample PCM16 from 8kHz to 16kHz (linear interpolation)."""
    n = len(pcm16_8k) // 2
    if n == 0:
        return b""
    samples = struct.unpack(f"<{n}h", pcm16_8k)
    out = []
    for i in range(n - 1):
        out.append(samples[i])
        out.append((samples[i] + samples[i + 1]) // 2)
    out.append(samples[-1])
    out.append(samples[-1])
    return struct.pack(f"<{len(out)}h", *out)


def resample_24k_to_8k(pcm16_24k: bytes) -> bytes:
    """Downsample PCM16 from 24kHz to 8kHz (every 3rd sample)."""
    n = len(pcm16_24k) // 2
    if n == 0:
        return b""
    samples = struct.unpack(f"<{n}h", pcm16_24k)
    out = samples[::3]
    return struct.pack(f"<{len(out)}h", *out)
