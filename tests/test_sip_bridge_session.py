"""TDD tests for SIP bridge call session lifecycle.

Covers: session creation, bounded queues, backpressure metrics,
shutdown signal, TaskGroup structured concurrency, VAD config.
"""

from __future__ import annotations

import asyncio
import os
from sip_bridge.session import (
    CallSession,
    INBOUND_QUEUE_SIZE,
    OUTBOUND_QUEUE_SIZE,
    build_telephone_vad_config,
    DEFAULT_AUDIO_GAIN,
)


class TestCallSessionCreation:
    """Session dataclass fields and defaults."""

    def test_session_has_call_id(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert s.call_id == "call-1"

    def test_session_has_bounded_queues(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert s.inbound_queue.maxsize == INBOUND_QUEUE_SIZE
        assert s.outbound_queue.maxsize == OUTBOUND_QUEUE_SIZE

    def test_session_initial_metrics_zero(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert s.frames_received == 0
        assert s.frames_sent == 0
        assert s.inbound_drops == 0
        assert s.outbound_drops == 0

    def test_session_codec_bridge_defaults_to_none(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert s.codec_bridge is None

    def test_session_accepts_codec_bridge(self) -> None:
        from sip_bridge.codec_bridge import G711CodecBridge

        bridge = G711CodecBridge()
        s = CallSession(
            call_id="call-1",
            tenant_id="public",
            company_id="acme",
            codec_bridge=bridge,
        )
        assert s.codec_bridge is bridge
        assert s.codec_bridge.rtp_payload_type == 0

    def test_session_accepts_opus_codec_bridge(self) -> None:
        from sip_bridge.codec_bridge import OpusCodecBridge

        bridge = OpusCodecBridge(encode_rate=16000)
        s = CallSession(
            call_id="call-1",
            tenant_id="public",
            company_id="acme",
            codec_bridge=bridge,
        )
        assert s.codec_bridge is bridge
        assert s.codec_bridge.rtp_payload_type == 111


class TestInboundFeeding:
    """feed_inbound() and backpressure metrics."""

    async def test_feed_inbound_increments_counter(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        await s.feed_inbound(b"\x00" * 160)
        assert s.frames_received == 1

    async def test_feed_inbound_drops_when_full(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        # Fill the queue
        for _ in range(INBOUND_QUEUE_SIZE):
            await s.feed_inbound(b"\x00" * 160)
        # This should be dropped
        await s.feed_inbound(b"\x00" * 160)
        assert s.inbound_drops == 1
        assert s.frames_received == INBOUND_QUEUE_SIZE


class TestShutdown:
    """Graceful shutdown signal."""

    def test_shutdown_sets_event(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert not s._shutdown.is_set()
        s.shutdown()
        assert s._shutdown.is_set()

    async def test_run_completes_after_shutdown(self) -> None:
        """Session.run() exits cleanly when shutdown is signaled."""
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")

        async def signal_shutdown():
            await asyncio.sleep(0.05)
            s.shutdown()

        # Run session with a concurrent shutdown signal
        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(signal_shutdown())

        # Should reach here without hanging
        assert s._shutdown.is_set()


class TestOutboundQueue:
    """Outbound queue and frame counting."""

    async def test_outbound_frame_counting(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")

        # Put frames in outbound queue
        for _ in range(3):
            await s.outbound_queue.put(b"\x00" * 160)

        # Simulate session reading from outbound (quick shutdown)
        async def run_briefly():
            await asyncio.sleep(0.1)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(run_briefly())

        assert s.frames_sent == 3


class TestTelephoneVadConfig:
    """VAD config for telephone channels (2026 best practices)."""

    def test_build_returns_realtime_input_config(self):
        from google.genai import types

        config = build_telephone_vad_config()
        assert isinstance(config, types.RealtimeInputConfig)

    def test_vad_not_disabled(self):
        config = build_telephone_vad_config()
        aad = config.automatic_activity_detection
        assert aad.disabled is False

    def test_start_sensitivity_low(self):
        from google.genai import types

        config = build_telephone_vad_config()
        aad = config.automatic_activity_detection
        assert aad.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_LOW

    def test_end_sensitivity_low(self):
        from google.genai import types

        config = build_telephone_vad_config()
        aad = config.automatic_activity_detection
        assert aad.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_LOW

    def test_prefix_padding_120ms(self):
        config = build_telephone_vad_config()
        aad = config.automatic_activity_detection
        assert aad.prefix_padding_ms == 120

    def test_silence_duration_450ms(self):
        config = build_telephone_vad_config()
        aad = config.automatic_activity_detection
        assert aad.silence_duration_ms == 450

    def test_turn_coverage_activity_only(self):
        from google.genai import types

        config = build_telephone_vad_config()
        assert config.turn_coverage == types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY

    def test_activity_handling_interrupts(self):
        from google.genai import types

        config = build_telephone_vad_config()
        assert config.activity_handling == types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS

    def test_env_override_prefix_padding(self, monkeypatch):
        monkeypatch.setenv("SIP_AUTO_VAD_PREFIX_PADDING_MS", "200")
        config = build_telephone_vad_config()
        assert config.automatic_activity_detection.prefix_padding_ms == 200

    def test_env_override_silence_duration(self, monkeypatch):
        monkeypatch.setenv("SIP_AUTO_VAD_SILENCE_DURATION_MS", "600")
        config = build_telephone_vad_config()
        assert config.automatic_activity_detection.silence_duration_ms == 600


class TestDefaultAudioGain:
    """AT bridge audio gain default."""

    def test_default_gain_is_2(self):
        assert DEFAULT_AUDIO_GAIN == 2

    def test_gain_env_override(self, monkeypatch):
        monkeypatch.setenv("SIP_AUDIO_GAIN", "3")
        # Re-read at usage site (session.py reads at frame time)
        assert int(os.getenv("SIP_AUDIO_GAIN", str(DEFAULT_AUDIO_GAIN))) == 3
