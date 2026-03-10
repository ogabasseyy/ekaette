"""G.711 μ-law 8kHz ↔ PCM16 16kHz/24kHz codec conversion.

Adapted from sip-to-ai (Apache 2.0).
"""

from __future__ import annotations

import struct

# G.711 μ-law decode table (256 entries)
_ULAW_DECODE: list[int] = []
_ALAW_DECODE: list[int] = []


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


def _alaw_to_linear(byte_val: int) -> int:
    """Decode one G.711 A-law byte into signed PCM16."""
    val = byte_val ^ 0x55
    t = (val & 0x0F) << 4
    seg = (val & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t += 0x108
        t <<= (seg - 1)
    return t if (val & 0x80) else -t


def _build_alaw_table() -> None:
    """Build A-law to linear PCM16 lookup table."""
    global _ALAW_DECODE
    if _ALAW_DECODE:
        return
    for i in range(256):
        _ALAW_DECODE.append(_alaw_to_linear(i))


_build_alaw_table()


def alaw_to_pcm16(alaw_bytes: bytes) -> bytes:
    """Convert G.711 A-law bytes to PCM16 linear (same sample rate)."""
    samples = [_ALAW_DECODE[b] for b in alaw_bytes]
    return struct.pack(f"<{len(samples)}h", *samples)


def pcm16_to_alaw(pcm16_bytes: bytes) -> bytes:
    """Convert PCM16 linear bytes to G.711 A-law."""
    n_samples = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm16_bytes)
    result = bytearray(n_samples)
    for i, sample in enumerate(samples):
        result[i] = _linear_to_alaw(sample)
    return bytes(result)


def _linear_to_alaw(sample: int) -> int:
    """Encode a single PCM16 sample to A-law."""
    clip = 0x7FFF
    if sample >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        sample = -sample - 1
    if sample > clip:
        sample = clip

    sample >>= 4
    segment_end = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)
    segment = 0
    while segment < len(segment_end) and sample > segment_end[segment]:
        segment += 1

    if segment >= 8:
        return 0x7F ^ mask

    encoded = segment << 4
    if segment < 2:
        encoded |= sample & 0x0F
    else:
        encoded |= (sample >> (segment - 1)) & 0x0F
    return encoded ^ mask


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
