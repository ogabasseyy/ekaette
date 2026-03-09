"""Tests for CodecBridge ABC, G711CodecBridge, and OpusCodecBridge.

TDD Red phase — these tests should FAIL until codec_bridge.py is implemented.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import types

import pytest

_HAS_OPUSLIB = importlib.util.find_spec("opuslib_next") is not None


# --- ABC contract tests ---


class TestCodecBridgeABC:
    """CodecBridge is an ABC that cannot be instantiated directly."""

    def test_abc_cannot_instantiate(self):
        from sip_bridge.codec_bridge import CodecBridge

        with pytest.raises(TypeError):
            CodecBridge()  # type: ignore[abstract]

    def test_abc_has_decode_method(self):
        from sip_bridge.codec_bridge import CodecBridge

        assert hasattr(CodecBridge, "decode_to_pcm16_16k")

    def test_abc_has_encode_method(self):
        from sip_bridge.codec_bridge import CodecBridge

        assert hasattr(CodecBridge, "encode_from_pcm16_24k")

    def test_abc_has_rtp_payload_type(self):
        from sip_bridge.codec_bridge import CodecBridge

        assert "rtp_payload_type" in CodecBridge.__annotations__

    def test_abc_has_rtp_clock_rate(self):
        from sip_bridge.codec_bridge import CodecBridge

        assert "rtp_clock_rate" in CodecBridge.__annotations__

    def test_abc_has_frame_duration_ms(self):
        from sip_bridge.codec_bridge import CodecBridge

        assert "frame_duration_ms" in CodecBridge.__annotations__


# --- G711CodecBridge tests ---


class TestG711CodecBridge:
    """G711CodecBridge wraps audio_codec.py functions."""

    def _make_bridge(self):
        from sip_bridge.codec_bridge import G711CodecBridge

        return G711CodecBridge(rtp_payload_type=0, rtp_clock_rate=8000)

    def test_g711_default_attributes(self):
        bridge = self._make_bridge()
        assert bridge.rtp_payload_type == 0
        assert bridge.rtp_clock_rate == 8000
        assert bridge.frame_duration_ms == 20

    def test_g711_decode_returns_bytes(self):
        bridge = self._make_bridge()
        # 160 bytes of ulaw = 20ms at 8kHz
        ulaw_silence = bytes(160)
        result = bridge.decode_to_pcm16_16k(ulaw_silence)
        assert isinstance(result, bytes)

    def test_g711_decode_output_size(self):
        """8kHz ulaw (160 samples) -> 16kHz PCM16 (320 samples = 640 bytes)."""
        bridge = self._make_bridge()
        ulaw_frame = bytes(160)  # 20ms @ 8kHz
        pcm16_16k = bridge.decode_to_pcm16_16k(ulaw_frame)
        # 160 -> decode to 160 PCM16 samples -> resample 8k->16k -> 320 samples -> 640 bytes
        assert len(pcm16_16k) == 640

    def test_g711_encode_returns_bytes(self):
        bridge = self._make_bridge()
        # 480 samples at 24kHz = 20ms -> 960 bytes PCM16
        pcm16_24k = bytes(960)
        result = bridge.encode_from_pcm16_24k(pcm16_24k)
        assert isinstance(result, bytes)

    def test_g711_encode_output_size(self):
        """24kHz PCM16 (480 samples) -> 8kHz ulaw (160 bytes)."""
        bridge = self._make_bridge()
        pcm16_24k = bytes(960)  # 480 samples * 2 bytes
        ulaw = bridge.encode_from_pcm16_24k(pcm16_24k)
        # 480 samples @ 24k -> resample to 160 samples @ 8k -> 160 bytes ulaw
        assert len(ulaw) == 160

    def test_g711_roundtrip_preserves_length(self):
        """Encode then decode should preserve frame duration."""
        bridge = self._make_bridge()
        # Create 20ms of 24kHz PCM16 silence
        pcm16_24k = struct.pack("<480h", *([0] * 480))
        ulaw = bridge.encode_from_pcm16_24k(pcm16_24k)
        pcm16_16k = bridge.decode_to_pcm16_16k(ulaw)
        # Output is 16kHz = 320 samples = 640 bytes
        assert len(pcm16_16k) == 640

    def test_g711_decode_nonsilence(self):
        """Decoding non-zero ulaw should produce non-zero PCM16."""
        bridge = self._make_bridge()
        # 0xFF in ulaw is approximately 0 (very small), 0x00 is large negative
        # Use 0x01 which is a large positive value
        ulaw_frame = bytes([0x01] * 160)
        pcm16_16k = bridge.decode_to_pcm16_16k(ulaw_frame)
        samples = struct.unpack(f"<{len(pcm16_16k) // 2}h", pcm16_16k)
        # At least some samples should be non-zero
        assert any(s != 0 for s in samples)

    def test_g711_encode_nonsilence(self):
        """Encoding non-zero PCM16 should produce non-zero ulaw."""
        bridge = self._make_bridge()
        # Create a 1kHz tone at 24kHz
        import math

        samples = [int(16000 * math.sin(2 * math.pi * 1000 * i / 24000)) for i in range(480)]
        pcm16_24k = struct.pack(f"<{len(samples)}h", *samples)
        ulaw = bridge.encode_from_pcm16_24k(pcm16_24k)
        # Not all bytes should be the silence value (0xFF)
        assert ulaw != bytes([0xFF] * 160)


# --- OpusCodecBridge tests ---


@pytest.mark.skipif(not _HAS_OPUSLIB, reason="opuslib_next is not installed")
class TestOpusCodecBridge:
    """OpusCodecBridge uses opuslib_next for Opus encode/decode."""

    def _make_bridge(self, encode_rate: int = 16000, channels: int = 2):
        from sip_bridge.codec_bridge import OpusCodecBridge

        return OpusCodecBridge(
            rtp_payload_type=111,
            rtp_clock_rate=48000,
            encode_rate=encode_rate,
            channels=channels,
        )

    def test_opus_default_attributes(self):
        bridge = self._make_bridge()
        assert bridge.rtp_payload_type == 111
        assert bridge.rtp_clock_rate == 48000
        assert bridge.frame_duration_ms == 20
        assert bridge.encode_rate == 16000
        assert bridge.channels == 2

    def test_opus_decode_returns_bytes(self):
        bridge = self._make_bridge()
        # First encode something to get valid Opus data
        pcm16_24k = bytes(960)  # 480 samples silence
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        result = bridge.decode_to_pcm16_16k(encoded)
        assert isinstance(result, bytes)

    def test_opus_decode_output_size_16k(self):
        """20ms at 16kHz = 320 samples = 640 bytes PCM16."""
        bridge = self._make_bridge()
        # Encode silence to get valid Opus data
        pcm16_24k = bytes(960)
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        pcm16_16k = bridge.decode_to_pcm16_16k(encoded)
        assert len(pcm16_16k) == 640

    def test_opus_encode_returns_bytes(self):
        bridge = self._make_bridge()
        pcm16_24k = bytes(960)  # 480 samples @ 24kHz = 20ms
        result = bridge.encode_from_pcm16_24k(pcm16_24k)
        assert isinstance(result, bytes)

    def test_opus_encode_output_smaller_than_input(self):
        """Opus compression should produce smaller output."""
        bridge = self._make_bridge()
        pcm16_24k = bytes(960)
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        assert len(encoded) < len(pcm16_24k)

    def test_opus_encode_rate_from_sdp(self):
        """Encoder rate should be configurable (SDP-derived)."""
        bridge_16k = self._make_bridge(encode_rate=16000)
        assert bridge_16k.encode_rate == 16000

        bridge_24k = self._make_bridge(encode_rate=24000)
        assert bridge_24k.encode_rate == 24000

    def test_opus_channel_count_is_configurable(self):
        bridge_mono = self._make_bridge(channels=1)
        assert bridge_mono.channels == 1

        bridge_stereo = self._make_bridge(channels=2)
        assert bridge_stereo.channels == 2

    def test_opus_roundtrip_preserves_frame_duration(self):
        """Encode then decode preserves 20ms frame duration."""
        bridge = self._make_bridge()
        pcm16_24k = bytes(960)  # 20ms silence
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        pcm16_16k = bridge.decode_to_pcm16_16k(encoded)
        # 20ms @ 16kHz = 320 samples = 640 bytes
        assert len(pcm16_16k) == 640

    def test_opus_encode_with_tone(self):
        """Encoding a sine wave should produce valid Opus data."""
        import math

        bridge = self._make_bridge()
        samples = [int(16000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(480)]
        pcm16_24k = struct.pack(f"<{len(samples)}h", *samples)
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        assert len(encoded) > 0

    def test_opus_decode_with_tone_roundtrip(self):
        """Encode a tone, decode it, verify non-silence."""
        import math

        bridge = self._make_bridge()
        samples = [int(16000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(480)]
        pcm16_24k = struct.pack(f"<{len(samples)}h", *samples)
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        pcm16_16k = bridge.decode_to_pcm16_16k(encoded)
        decoded_samples = struct.unpack(f"<{len(pcm16_16k) // 2}h", pcm16_16k)
        # Decoded tone should have non-zero samples
        assert any(s != 0 for s in decoded_samples)

    def test_opus_multiple_frames_sequential(self):
        """Encoding multiple frames sequentially should work."""
        bridge = self._make_bridge()
        for _ in range(10):
            pcm16_24k = bytes(960)
            encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
            assert len(encoded) > 0
            pcm16_16k = bridge.decode_to_pcm16_16k(encoded)
            assert len(pcm16_16k) == 640

    def test_opus_resample_24k_to_16k_for_encode(self):
        """When encode_rate=16000, encoder should handle 24k->16k resample."""
        bridge = self._make_bridge(encode_rate=16000)
        pcm16_24k = bytes(960)  # 480 samples @ 24kHz
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        # Should succeed — the bridge handles resampling internally
        assert len(encoded) > 0

    def test_opus_encode_24k_no_resample(self):
        """When encode_rate=24000, no resampling needed."""
        bridge = self._make_bridge(encode_rate=24000)
        pcm16_24k = bytes(960)
        encoded = bridge.encode_from_pcm16_24k(pcm16_24k)
        assert len(encoded) > 0


class TestOpusCodecBridgeChannelBehavior:
    """Channel count should affect wire-side PCM width sent to the encoder."""

    def test_channel_count_changes_encoder_input_width(self, monkeypatch):
        encoded_inputs: list[tuple[int, int, int]] = []

        class FakeEncoder:
            def __init__(self, fs: int, channels: int, application: int):
                self.fs = fs
                self.channels = channels
                self.application = application

            def encode(self, pcm_input: bytes, frame_samples: int) -> bytes:
                encoded_inputs.append((self.channels, len(pcm_input), frame_samples))
                return b"opus"

        class FakeDecoder:
            def __init__(self, fs: int, channels: int):
                self.fs = fs
                self.channels = channels

            def decode(self, encoded: bytes, frame_samples: int) -> bytes:
                return bytes(frame_samples * self.channels * 2)

        monkeypatch.setitem(
            sys.modules,
            "opuslib_next",
            types.SimpleNamespace(
                APPLICATION_VOIP=2048,
                Encoder=FakeEncoder,
                Decoder=FakeDecoder,
            ),
        )
        import importlib

        codec_bridge = sys.modules.get("sip_bridge.codec_bridge")
        if codec_bridge is None:
            codec_bridge = importlib.import_module("sip_bridge.codec_bridge")
        else:
            codec_bridge = importlib.reload(codec_bridge)
        OpusCodecBridge = codec_bridge.OpusCodecBridge

        mono_bridge = OpusCodecBridge(channels=1)
        stereo_bridge = OpusCodecBridge(channels=2)

        mono_bridge.encode_from_pcm16_24k(bytes(960))
        stereo_bridge.encode_from_pcm16_24k(bytes(960))

        assert encoded_inputs == [
            (1, 640, 320),
            (2, 1280, 320),
        ]


# --- Resample helper tests ---


class TestResample24kTo16k:
    """Test the 24k->16k linear interpolation resampler."""

    def test_resample_returns_bytes(self):
        from sip_bridge.codec_bridge import resample_24k_to_16k

        pcm16_24k = bytes(960)  # 480 samples
        result = resample_24k_to_16k(pcm16_24k)
        assert isinstance(result, bytes)

    def test_resample_output_size(self):
        """480 samples @ 24kHz -> 320 samples @ 16kHz = 640 bytes."""
        from sip_bridge.codec_bridge import resample_24k_to_16k

        pcm16_24k = bytes(960)
        result = resample_24k_to_16k(pcm16_24k)
        assert len(result) == 640

    def test_resample_empty_input(self):
        from sip_bridge.codec_bridge import resample_24k_to_16k

        assert resample_24k_to_16k(b"") == b""

    def test_resample_preserves_silence(self):
        from sip_bridge.codec_bridge import resample_24k_to_16k

        pcm16_24k = struct.pack("<480h", *([0] * 480))
        result = resample_24k_to_16k(pcm16_24k)
        samples = struct.unpack(f"<{len(result) // 2}h", result)
        assert all(s == 0 for s in samples)

    def test_resample_snr_sine_wave(self):
        """Roundtrip 24k->16k->24k must preserve SNR >20dB on sine wave."""
        import math

        from sip_bridge.codec_bridge import resample_16k_to_24k, resample_24k_to_16k

        freq = 440
        n_24k = 480
        original = [int(16000 * math.sin(2 * math.pi * freq * i / 24000)) for i in range(n_24k)]
        pcm_24k = struct.pack(f"<{n_24k}h", *original)

        pcm_16k = resample_24k_to_16k(pcm_24k)
        pcm_24k_rt = resample_16k_to_24k(pcm_16k)

        rt_samples = struct.unpack(f"<{len(pcm_24k_rt) // 2}h", pcm_24k_rt)
        # Compare only overlapping samples
        n = min(len(original), len(rt_samples))

        signal_power = sum(s * s for s in original[:n]) / n
        noise_power = sum((a - b) ** 2 for a, b in zip(original[:n], rt_samples[:n])) / n

        if noise_power == 0:
            snr_db = 100.0
        else:
            snr_db = 10 * math.log10(signal_power / noise_power)

        assert snr_db > 20, f"SNR {snr_db:.1f}dB < 20dB threshold"
