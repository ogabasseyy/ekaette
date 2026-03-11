"""Per-call AI lifecycle with structured concurrency.

4-task TaskGroup pattern (mirrors wa_session.py):
1. _media_recv_loop: UDP recvfrom → feed_inbound (RTP frames from network)
2. _media_inbound_loop: RTP parse → G.711 decode → PCM16 16kHz → Gemini
3. _gemini_bidi_loop: Gemini Live bidi (send PCM16, receive PCM16)
4. _media_outbound_loop: PCM16 24kHz → G.711 encode → RTP → send

Uses asyncio.TaskGroup for clean teardown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from google import genai
from google.genai import types as genai_types

from .audio_codec import alaw_to_pcm16, resample_8k_to_16k
from .codec_bridge import resample_24k_to_16k
from .rtp import PCMA_PAYLOAD_TYPE, PCMU_PAYLOAD_TYPE, RTPPacket, RTPTimer

if TYPE_CHECKING:
    from .codec_bridge import CodecBridge
    from .gateway_client import GatewayClient

try:
    from aec_audio_processing import AudioProcessor
except Exception:  # pragma: no cover - optional native dependency at runtime
    AudioProcessor = None

logger = logging.getLogger(__name__)

# Bounded queue sizes (backpressure)
INBOUND_QUEUE_SIZE = 500  # ~10s of 20ms frames (match wa_session.py)
OUTBOUND_QUEUE_SIZE = 10000

# 20ms of silence at 16kHz 16-bit mono (640 bytes)
SILENCE_FRAME = b"\x00" * 640
# 20ms of silence at 24kHz 16-bit mono (960 bytes) for callback-leg media priming.
SILENCE_FRAME_24K = b"\x00" * 960

# Echo suppression holdoff after model stops speaking.
# Keep short (0.5s) to avoid muting start of user's next utterance.
ECHO_HOLDOFF_SEC = 0.5

# Default audio gain for G.711 telephony input.
# 2x compensates for typical PSTN attenuation without triggering VAD false positives.
DEFAULT_AUDIO_GAIN = 2


def _read_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


_CALLBACK_MEDIA_PRIME_MS = max(
    100.0,
    _read_float_env("SIP_OUTBOUND_CALLBACK_MEDIA_PRIME_MS", 600.0),
)
_CALLBACK_MEDIA_PRIME_FRAME_COUNT = max(
    1,
    int(math.ceil(_CALLBACK_MEDIA_PRIME_MS / 20.0)),
)
_CALLBACK_POST_ANSWER_GRACE_MS = max(
    0.0,
    _read_float_env("SIP_OUTBOUND_CALLBACK_POST_ANSWER_GRACE_MS", 1000.0),
)


def build_telephone_vad_config() -> genai_types.RealtimeInputConfig:
    """Build VAD config optimized for telephone audio (2026 best practices).

    Tuned for G.711/Opus telephony: short prefix padding (120ms) to catch
    clipped onsets, moderate silence duration (450ms) for natural pauses,
    LOW sensitivities to reduce false triggers from line noise.
    """
    return genai_types.RealtimeInputConfig(
        automatic_activity_detection=genai_types.AutomaticActivityDetection(
            disabled=False,
            startOfSpeechSensitivity=genai_types.StartSensitivity.START_SENSITIVITY_LOW,
            endOfSpeechSensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
            prefixPaddingMs=int(os.getenv("SIP_AUTO_VAD_PREFIX_PADDING_MS", "120")),
            silenceDurationMs=int(os.getenv("SIP_AUTO_VAD_SILENCE_DURATION_MS", "450")),
        ),
        activity_handling=genai_types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        turn_coverage=genai_types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
    )


@dataclass(slots=True)
class CallSession:
    """Per-call session managing RTP audio ↔ Gemini Live bridge."""

    call_id: str
    tenant_id: str
    company_id: str
    codec_bridge: CodecBridge | None = None

    # RTP media
    remote_rtp_addr: tuple[str, int] | None = None
    local_rtp_port: int = 0
    rtp_bind_host: str = ""
    media_transport: Any = None

    # Caller identity (extracted from SIP From header)
    _caller_phone: str = ""
    request_hangup: Callable[[str], None] | None = None

    # Gemini Live config (direct mode)
    gemini_api_key: str = ""
    gemini_model_id: str = ""
    gemini_system_instruction: str = ""
    gemini_voice: str = "Aoede"
    gemini_session: Any = None

    # Gateway mode (Cloud Run WebSocket)
    gateway_client: GatewayClient | None = None
    connect_greeting_text: str = "[Phone call connected]"

    # Queues
    inbound_queue: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=INBOUND_QUEUE_SIZE)
    )
    outbound_queue: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=OUTBOUND_QUEUE_SIZE)
    )
    _gemini_in_queue: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=INBOUND_QUEUE_SIZE)
    )

    started_at: float = field(default_factory=time.time)
    _shutdown: asyncio.Event = field(default_factory=asyncio.Event)
    _owns_transport: bool = False
    _no_inbound_warned: bool = False
    _outbound_buffer: bytearray = field(default_factory=bytearray)

    # Echo suppression: shared between recv loop (writer) and send loop (reader)
    _model_speaking: bool = False
    _model_speech_end_time: float = 0.0
    # Guard against re-greeting on gateway reconnect
    _gateway_greeting_sent: bool = False
    _greeting_lock_active: bool = False
    _greeting_lock_pending_release: bool = False
    _greeting_lock_safety_deadline: float = 0.0
    _last_outbound_rtp_sent_at: float = 0.0
    _hangup_requested: bool = False
    _end_after_speaking_pending: bool = False
    _end_after_speaking_audio_seen: bool = False
    _end_after_speaking_idle_seen: bool = False
    _end_after_speaking_deadline: float = 0.0
    delay_answer_until_ready: bool = False
    prime_outbound_on_answer: bool = False
    _media_send_enabled: asyncio.Event = field(default_factory=asyncio.Event)
    _first_outbound_audio_ready: asyncio.Event = field(default_factory=asyncio.Event)
    _startup_failed: asyncio.Event = field(default_factory=asyncio.Event)
    _answer_media_primed: bool = False
    callback_post_answer_grace_sec: float = 0.0
    _callback_post_answer_release_at: float = 0.0
    _answered_at_monotonic: float = 0.0
    _preanswer_agent_final_seen: bool = False
    _suppress_postanswer_agent_audio_until_user_speaks: bool = False
    _user_spoke_after_answer: bool = False
    _suppressed_agent_audio_frames: int = 0
    _denoise_enabled: bool = False
    _noise_gate_multiplier: float = 1.6
    _noise_gate_min_rms: float = 120.0
    _noise_gate_attack_rms: float = 320.0
    _noise_gate_attenuation: float = 0.12
    _noise_floor_rms: float = 0.0
    _noise_gate_suppressed_frames: int = 0
    _webrtc_apm_enabled: bool = False
    _webrtc_apm: Any = None
    _webrtc_apm_frame_size_bytes: int = 0
    _webrtc_apm_failures: int = 0

    # Metrics
    frames_received: int = 0
    frames_sent: int = 0
    inbound_drops: int = 0
    outbound_drops: int = 0
    gemini_input_drops: int = 0

    def __post_init__(self) -> None:
        self._denoise_enabled = os.getenv("SIP_DENOISE_ENABLED", "1").strip().lower() in {
            "1", "true", "yes", "on",
        }
        self._noise_gate_multiplier = max(
            1.0, _read_float_env("SIP_DENOISE_GATE_MULTIPLIER", 1.6)
        )
        self._noise_gate_min_rms = max(
            0.0, _read_float_env("SIP_DENOISE_MIN_RMS", 120.0)
        )
        self._noise_gate_attack_rms = max(
            self._noise_gate_min_rms,
            _read_float_env("SIP_DENOISE_ATTACK_RMS", 320.0),
        )
        self._noise_gate_attenuation = min(
            1.0,
            max(0.0, _read_float_env("SIP_DENOISE_ATTENUATION", 0.12)),
        )
        self._webrtc_apm_enabled = os.getenv(
            "SIP_WEBRTC_APM_ENABLED", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._maybe_init_webrtc_apm()
        if not self.delay_answer_until_ready:
            self._media_send_enabled.set()

    def _maybe_init_webrtc_apm(self) -> None:
        """Initialize WebRTC Audio Processing when available."""
        if not self._webrtc_apm_enabled or AudioProcessor is None:
            return
        try:
            processor = AudioProcessor(
                enable_aec=False,
                enable_ns=True,
                ns_level=max(0, min(3, int(os.getenv("SIP_WEBRTC_APM_NS_LEVEL", "2")))),
                enable_agc=os.getenv("SIP_WEBRTC_APM_AGC_ENABLED", "1").strip().lower()
                in {"1", "true", "yes", "on"},
                agc_mode=max(0, min(3, int(os.getenv("SIP_WEBRTC_APM_AGC_MODE", "1")))),
                enable_vad=False,
            )
            processor.set_stream_format(16000, 1, 16000, 1)
            processor.set_reverse_stream_format(16000, 1)
            processor.set_stream_delay(int(os.getenv("SIP_WEBRTC_APM_STREAM_DELAY_MS", "120")))
            self._webrtc_apm = processor
            self._webrtc_apm_frame_size_bytes = int(processor.get_frame_size()) * 2
            logger.info(
                "Enabled WebRTC APM on AT bridge call_id=%s frame_bytes=%d",
                self.call_id,
                self._webrtc_apm_frame_size_bytes,
            )
        except Exception:
            self._webrtc_apm = None
            self._webrtc_apm_frame_size_bytes = 0
            logger.warning(
                "Failed to initialize WebRTC APM; falling back to noise gate call_id=%s",
                self.call_id,
                exc_info=True,
            )

    async def wait_until_answer_ready(self, timeout: float) -> bool:
        """Wait until greeting audio is buffered or startup fails."""
        if not self.delay_answer_until_ready:
            return True
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._first_outbound_audio_ready.is_set():
                return True
            if self._startup_failed.is_set() or self._shutdown.is_set():
                return False
            await asyncio.sleep(0.05)
        return self._first_outbound_audio_ready.is_set()

    @property
    def startup_failed(self) -> bool:
        return self._startup_failed.is_set()

    def mark_answered(self) -> None:
        """Release buffered outbound audio once SIP answers the call."""
        self._answered_at_monotonic = time.monotonic()
        if self.prime_outbound_on_answer and not self._answer_media_primed:
            self._prime_outbound_callback_audio()
        if self._preanswer_agent_final_seen:
            self._suppress_postanswer_agent_audio_until_user_speaks = True
        if self.callback_post_answer_grace_sec > 0:
            self._callback_post_answer_release_at = (
                time.monotonic() + self.callback_post_answer_grace_sec
            )
        self._media_send_enabled.set()

    def _callback_post_answer_grace_active(self) -> bool:
        """Return True while callback speech should still be held briefly after answer."""
        release_at = self._callback_post_answer_release_at
        return bool(release_at) and time.monotonic() < release_at

    def _answered(self) -> bool:
        return self._answered_at_monotonic > 0.0

    def _prime_outbound_callback_audio(self) -> None:
        """Queue short RTP silence so outbound callback legs stay alive during model startup."""
        queued = 0
        for _ in range(_CALLBACK_MEDIA_PRIME_FRAME_COUNT):
            try:
                self.outbound_queue.put_nowait(SILENCE_FRAME_24K)
                queued += 1
            except asyncio.QueueFull:
                self.outbound_drops += 1
                break
        if queued:
            self._answer_media_primed = True
            self._first_outbound_audio_ready.set()
            logger.info(
                "Primed outbound callback media frames=%d ms=%d call_id=%s",
                queued,
                int(_CALLBACK_MEDIA_PRIME_MS),
                self.call_id,
            )

    async def run(self) -> None:
        """Run the four concurrent tasks. Cancels all on first failure."""
        logger.info(
            "Call session started",
            extra={
                "call_id": self.call_id,
                "tenant_id": self.tenant_id,
                "company_id": self.company_id,
                "local_rtp_port": self.local_rtp_port,
                "remote_rtp": self.remote_rtp_addr,
            },
        )

        # Create UDP socket for RTP if not injected
        if self.media_transport is None and self.local_rtp_port:
            env_bind_host = os.getenv("SIP_RTP_BIND_HOST", "").strip()
            bind_host = (
                self.rtp_bind_host.strip()
                or env_bind_host
                or "127.0.0.1"
            )
            self.media_transport = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.media_transport.setblocking(False)
            self.media_transport.bind((bind_host, self.local_rtp_port))
            self._owns_transport = True
            logger.info(
                "RTP socket bound",
                extra={"host": bind_host, "port": self.local_rtp_port},
            )

        # Gateway mode: connect via Cloud Run WebSocket
        use_gateway = self.gateway_client is not None
        gemini_ctx = None

        if use_gateway:
            try:
                await self.gateway_client.connect()
                logger.info("Gateway mode: connected to Cloud Run")
            except Exception:
                logger.exception("Failed to connect to gateway")
                self._startup_failed.set()
                self._cleanup_transport()
                return
        elif self.gemini_session is None and self.gemini_api_key:
            # Direct mode: connect to Gemini Live
            try:
                sys_instruct = self.gemini_system_instruction or (
                    "You are the virtual assistant named Ekaitay. "
                    "Your name is Ekaitay — always say it exactly like that. "
                    "You are answering a phone call. Greet the caller warmly and ask how you can help. "
                    "Be helpful, concise, and professional. Keep responses short for phone conversation."
                )

                speech_config = genai_types.SpeechConfig(
                    voice_config=genai_types.VoiceConfig(
                        prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                            voice_name=self.gemini_voice,
                        )
                    )
                )

                live_config = {
                    "response_modalities": ["AUDIO"],
                    "speech_config": speech_config,
                    "system_instruction": {"parts": [{"text": sys_instruct}]},
                    "input_audio_transcription": genai_types.AudioTranscriptionConfig(),
                    "output_audio_transcription": genai_types.AudioTranscriptionConfig(),
                    "proactivity": genai_types.ProactivityConfig(proactive_audio=True),
                    "realtime_input_config": build_telephone_vad_config(),
                }

                client = genai.Client(
                    api_key=self.gemini_api_key,
                    http_options=genai_types.HttpOptions(api_version="v1alpha"),
                )
                gemini_ctx = client.aio.live.connect(
                    model=self.gemini_model_id,
                    config=live_config,
                )
                self.gemini_session = await gemini_ctx.__aenter__()
                logger.info("Gemini Live connected (auto-VAD, echo-muting)")

                # Trigger proactive greeting (Pipecat pattern):
                # send_client_content ONCE before audio stream starts.
                # This is sequential, not interleaved — VAD works normally after.
                self._model_speaking = True  # pre-mute echo for greeting
                await self.gemini_session.send_client_content(
                    turns=genai_types.Content(
                        role="user",
                        parts=[genai_types.Part(text=self.connect_greeting_text)],
                    ),
                    turn_complete=True,
                )
                logger.info("Greeting trigger sent via send_client_content")

            except Exception:
                logger.exception("Failed to connect to Gemini Live")
                self._startup_failed.set()
                gemini_ctx = None

        bidi_loop = self._gateway_bidi_loop if use_gateway else self._gemini_bidi_loop

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._media_recv_loop())
                tg.create_task(self._media_inbound_loop())
                tg.create_task(bidi_loop())
                tg.create_task(self._media_outbound_loop())
        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error("Call session task failed", exc_info=exc)
        finally:
            # Clean up connections
            if use_gateway and self.gateway_client is not None:
                try:
                    await self.gateway_client.close()
                except Exception:
                    logger.debug("Gateway cleanup failed", exc_info=True)
            if gemini_ctx is not None:
                try:
                    await gemini_ctx.__aexit__(None, None, None)
                except Exception:
                    logger.debug("Gemini context cleanup failed", exc_info=True)

            # Clean up UDP socket
            self._cleanup_transport()

            duration = time.time() - self.started_at
            logger.info(
                "Call session ended",
                extra={
                    "call_id": self.call_id,
                    "duration_seconds": round(duration, 2),
                    "frames_received": self.frames_received,
                    "frames_sent": self.frames_sent,
                    "inbound_drops": self.inbound_drops,
                    "outbound_drops": self.outbound_drops,
                    "gemini_input_drops": self.gemini_input_drops,
                },
            )

    def _cleanup_transport(self) -> None:
        """Close owned UDP transport if still open."""
        if self._owns_transport and self.media_transport is not None:
            try:
                self.media_transport.close()
            except Exception:
                logger.debug("RTP socket close failed", exc_info=True)
            self.media_transport = None

    def shutdown(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown.set()

    def _clear_outbound_audio(self) -> None:
        """Drop buffered playback so interrupted speech stops immediately."""
        self._outbound_buffer.clear()
        while True:
            try:
                self.outbound_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _maybe_finish_end_after_speaking(self) -> None:
        """End the call after the callback acknowledgement has finished playing."""
        if (
            not self._end_after_speaking_pending
            or self._shutdown.is_set()
            or self._hangup_requested
        ):
            return

        now = time.monotonic()
        if (
            not self._end_after_speaking_idle_seen
            and self._end_after_speaking_deadline > 0
            and now >= self._end_after_speaking_deadline
        ):
            logger.info(
                "Ending call after callback acknowledgement (idle deadline) call_id=%s",
                self.call_id,
            )
            self._finalize_end_after_speaking("idle deadline")
            return

        if not self._end_after_speaking_idle_seen:
            return

        outbound_drained = (
            self._end_after_speaking_audio_seen
            and self._last_outbound_rtp_sent_at > 0
            and now - self._last_outbound_rtp_sent_at > 0.5
        )
        safety_timeout = (
            self._end_after_speaking_deadline > 0
            and now >= self._end_after_speaking_deadline
        )
        if outbound_drained or safety_timeout:
            logger.info(
                "Ending call after callback acknowledgement (%s) call_id=%s",
                "outbound audio drained" if outbound_drained else "safety timeout",
                self.call_id,
            )
            self._finalize_end_after_speaking(
                "outbound audio drained" if outbound_drained else "safety timeout"
            )

    def _finalize_end_after_speaking(self, reason: str) -> None:
        """Issue hangup once the callback acknowledgement has completed."""
        self._end_after_speaking_pending = False
        self._hangup_requested = True
        if self.request_hangup is not None:
            try:
                self.request_hangup(reason)
            except Exception:
                logger.warning(
                    "Failed to request SIP hangup after callback acknowledgement call_id=%s",
                    self.call_id,
                    exc_info=True,
                )
        self._shutdown.set()

    @staticmethod
    def _pcm_rms(pcm16: bytes) -> float:
        """Compute frame RMS for simple noise-floor tracking."""
        n = len(pcm16) // 2
        if n <= 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", pcm16)
        return math.sqrt(sum(sample * sample for sample in samples) / n)

    def _apply_input_noise_gate(self, pcm16: bytes) -> bytes:
        """Apply a conservative adaptive noise gate to inbound telephony PCM."""
        if not self._denoise_enabled or not pcm16:
            return pcm16

        rms = self._pcm_rms(pcm16)
        if rms <= 0:
            return pcm16

        if self._noise_floor_rms <= 0:
            self._noise_floor_rms = max(1.0, min(rms, self._noise_gate_min_rms))
        elif rms <= max(self._noise_floor_rms * 2.5, self._noise_gate_attack_rms):
            self._noise_floor_rms = (self._noise_floor_rms * 0.94) + (rms * 0.06)
        else:
            self._noise_floor_rms = (self._noise_floor_rms * 0.995) + (
                min(rms, self._noise_floor_rms * 2.5) * 0.005
            )

        threshold = max(
            self._noise_gate_min_rms,
            self._noise_floor_rms * self._noise_gate_multiplier,
        )
        if rms >= threshold:
            return pcm16

        attenuation = self._noise_gate_attenuation
        if attenuation <= 0.0:
            return b"\x00" * len(pcm16)

        n = len(pcm16) // 2
        samples = struct.unpack(f"<{n}h", pcm16)
        attenuated = struct.pack(
            f"<{n}h",
            *(int(sample * attenuation) for sample in samples),
        )
        self._noise_gate_suppressed_frames += 1
        if (
            self._noise_gate_suppressed_frames <= 3
            or self._noise_gate_suppressed_frames % 200 == 0
        ):
            logger.info(
                "Noise gate suppressed inbound frame call_id=%s rms=%.0f floor=%.0f threshold=%.0f count=%d",
                self.call_id,
                rms,
                self._noise_floor_rms,
                threshold,
                self._noise_gate_suppressed_frames,
            )
        return attenuated

    def _process_webrtc_apm_stream(self, pcm16: bytes, *, reverse: bool = False) -> bytes:
        """Process PCM16 through WebRTC APM in 10ms frames.

        The native processor expects 10ms chunks at the configured sample rate.
        Our AT bridge works in 20ms chunks, so split each inbound/outbound frame
        into 10ms subframes and trim any padded tail on return.
        """
        processor = self._webrtc_apm
        frame_bytes = self._webrtc_apm_frame_size_bytes
        if processor is None or frame_bytes <= 0 or not pcm16:
            return pcm16

        out = bytearray()
        for start in range(0, len(pcm16), frame_bytes):
            chunk = pcm16[start : start + frame_bytes]
            original_len = len(chunk)
            if original_len < frame_bytes:
                chunk = chunk + (b"\x00" * (frame_bytes - original_len))
            processed = (
                processor.process_reverse_stream(chunk)
                if reverse
                else processor.process_stream(chunk)
            )
            out.extend(processed[:original_len])
        return bytes(out)

    def _apply_input_denoise(self, pcm16: bytes) -> bytes:
        """Apply WebRTC APM denoising first, then fall back to the legacy gate."""
        if not pcm16:
            return pcm16
        if self._webrtc_apm is not None:
            try:
                return self._process_webrtc_apm_stream(pcm16)
            except Exception:
                self._webrtc_apm_failures += 1
                if self._webrtc_apm_failures <= 3 or self._webrtc_apm_failures % 100 == 0:
                    logger.warning(
                        "WebRTC APM processing failed; falling back to noise gate call_id=%s failures=%d",
                        self.call_id,
                        self._webrtc_apm_failures,
                        exc_info=True,
                    )
        return self._apply_input_noise_gate(pcm16)

    async def feed_inbound(self, frame: bytes) -> None:
        """Feed an RTP audio frame from the phone side."""
        try:
            self.inbound_queue.put_nowait(frame)
            self.frames_received += 1
            if self.frames_received % 50 == 1:
                logger.info(
                    "RTP frames received count=%d call_id=%s",
                    self.frames_received,
                    self.call_id,
                )
        except asyncio.QueueFull:
            self.inbound_drops += 1

    # ------------------------------------------------------------------
    # Media loops
    # ------------------------------------------------------------------

    async def _media_recv_loop(self) -> None:
        """Read RTP packets from UDP socket and feed into inbound queue."""
        loop = asyncio.get_running_loop()
        use_symmetric_rtp = os.getenv("SIP_SYMMETRIC_RTP", "0").strip().lower() in {
            "1", "true", "yes", "on",
        }
        first_inbound_logged = False
        if self.media_transport is None:
            while not self._shutdown.is_set():
                await asyncio.sleep(0.02)
            return

        while not self._shutdown.is_set():
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(self.media_transport, 2048),
                    timeout=1.0,
                )
                if not first_inbound_logged:
                    first_inbound_logged = True
                    logger.info(
                        "RTP inbound first source=%s configured_remote=%s symmetric=%s",
                        addr, self.remote_rtp_addr, use_symmetric_rtp,
                    )
                    if self.remote_rtp_addr is not None and self.remote_rtp_addr != addr and not use_symmetric_rtp:
                        logger.warning(
                            "RTP source differs from SDP remote (source=%s, sdp=%s). "
                            "If caller hears no audio, enable SIP_SYMMETRIC_RTP=1.",
                            addr, self.remote_rtp_addr,
                        )
                if self.remote_rtp_addr is None:
                    self.remote_rtp_addr = (addr[0], addr[1])
                    logger.info("RTP remote set from first inbound source remote=%s", self.remote_rtp_addr)
                elif use_symmetric_rtp and self.remote_rtp_addr != addr:
                    logger.info("RTP remote updated by symmetric latching old=%s new=%s", self.remote_rtp_addr, addr)
                    self.remote_rtp_addr = (addr[0], addr[1])
                await self.feed_inbound(data)
            except TimeoutError:
                continue
            except OSError:
                break

    async def _media_inbound_loop(self) -> None:
        """Parse RTP, decode G.711 → PCM16 16kHz, forward to Gemini."""
        decode_count = 0
        parse_fail = 0
        while not self._shutdown.is_set():
            try:
                frame = await asyncio.wait_for(
                    self.inbound_queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue

            try:
                rtp = RTPPacket.parse(frame)
                if rtp is None:
                    parse_fail += 1
                    if parse_fail <= 3:
                        logger.warning("RTP parse failed", extra={"frame_len": len(frame)})
                    continue
                payload = rtp.payload

                decode_count += 1
                if decode_count <= 3:
                    logger.info(
                        "RTP packet parsed pt=%d seq=%d payload_len=%d",
                        rtp.payload_type, rtp.sequence, len(payload),
                    )

                if rtp.payload_type == PCMU_PAYLOAD_TYPE:
                    if self.codec_bridge is not None:
                        pcm16 = self.codec_bridge.decode_to_pcm16_16k(payload)
                    else:
                        pcm16 = payload
                elif rtp.payload_type == PCMA_PAYLOAD_TYPE:
                    pcm16 = resample_8k_to_16k(alaw_to_pcm16(payload))
                else:
                    continue

                if pcm16:
                    pcm16 = self._apply_input_denoise(pcm16)
                    gain = int(os.getenv("SIP_AUDIO_GAIN", str(DEFAULT_AUDIO_GAIN)))
                    if gain > 1:
                        n_amp = len(pcm16) // 2
                        samples_amp = struct.unpack(f"<{n_amp}h", pcm16)
                        pcm16 = struct.pack(
                            f"<{n_amp}h",
                            *(max(-32768, min(32767, s * gain)) for s in samples_amp),
                        )
                    try:
                        self._gemini_in_queue.put_nowait(pcm16)
                    except asyncio.QueueFull:
                        self.gemini_input_drops += 1
                        if (
                            self.gemini_input_drops <= 3
                            or self.gemini_input_drops % 100 == 0
                        ):
                            logger.warning(
                                "Dropping decoded PCM frame for Gemini input due to backpressure "
                                "(drops=%d call_id=%s)",
                                self.gemini_input_drops,
                                self.call_id,
                            )
            except Exception:
                logger.debug("Inbound frame processing error", exc_info=True)

    async def _gemini_bidi_loop(self) -> None:
        """Bidirectional Gemini Live session."""
        if self.gemini_session is None:
            while not self._shutdown.is_set():
                await asyncio.sleep(0.02)
            return

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._gemini_send_loop())
            tg.create_task(self._gemini_recv_loop())

    async def _gemini_send_loop(self) -> None:
        """Send audio to Gemini Live with echo muting.

        Auto-VAD handles all turn detection. We prevent echo from
        confusing it by sending SILENCE instead of real audio while
        the model is speaking + a holdoff period after.

        This keeps the audio stream continuous (VAD stays calibrated)
        while preventing phone speaker echo from triggering false turns.

        CRITICAL: Must use audio=Blob(...) not media={...}.
        """
        send_count = 0
        max_rms_seen = 0.0

        while not self._shutdown.is_set():
            try:
                pcm16 = await asyncio.wait_for(
                    self._gemini_in_queue.get(), timeout=1.0
                )

                # Compute RMS for diagnostics
                n = len(pcm16) // 2
                if n > 0:
                    samples = struct.unpack(f"<{n}h", pcm16)
                    rms = math.sqrt(sum(s * s for s in samples) / n)
                    if rms > max_rms_seen:
                        max_rms_seen = rms
                else:
                    rms = 0.0

                # Echo muting: while model speaks (or within holdoff after),
                # replace real audio with silence to prevent echo feedback.
                echo_muted = (
                    self._model_speaking
                    or (time.time() - self._model_speech_end_time) < ECHO_HOLDOFF_SEC
                )

                audio_to_send = SILENCE_FRAME if echo_muted else pcm16

                await self.gemini_session.send_realtime_input(
                    audio=genai_types.Blob(
                        data=audio_to_send,
                        mime_type="audio/pcm;rate=16000",
                    ),
                )
                send_count += 1

                # Log periodically
                if (send_count <= 1250 and send_count % 50 == 1) or send_count % 250 == 1:
                    logger.info(
                        "send count=%d rms=%.0f max=%.0f muted=%s",
                        send_count, rms, max_rms_seen,
                        "YES" if echo_muted else "no",
                    )
            except TimeoutError:
                continue
            except Exception:
                logger.exception("Gemini send error")
                break

    async def _gemini_recv_loop(self) -> None:
        """Receive audio from Gemini Live and queue for outbound.

        CRITICAL: session.receive() is an async generator that BREAKS on
        turn_complete. We must call it in a while loop to get subsequent
        turns. This is the pattern used by both Google's cookbook and Pipecat.

        Manages echo suppression flags:
        - Sets _model_speaking=True when audio data starts arriving
        - Sets _model_speaking=False and records end time on turn_complete
        """
        recv_count = 0
        turn_count = 0
        try:
            while not self._shutdown.is_set():
                turn = self.gemini_session.receive()
                async for response in turn:
                    if self._shutdown.is_set():
                        return

                    sc = getattr(response, "server_content", None)
                    if sc:
                        input_tx = getattr(sc, "input_transcription", None)
                        if input_tx:
                            text = getattr(input_tx, "text", "")
                            if text:
                                logger.info("Input transcription: %s", text)

                        mt = getattr(sc, "model_turn", None)
                        if mt:
                            for part in mt.parts:
                                text = getattr(part, "text", None)
                                if text:
                                    logger.info("Output transcription: %s", text)

                                inline = getattr(part, "inline_data", None)
                                if inline and inline.data:
                                    if not self._model_speaking:
                                        self._model_speaking = True
                                        logger.info("Echo mute ON (model speaking)")

                                    self._first_outbound_audio_ready.set()
                                    self._outbound_buffer.extend(inline.data)
                                    while len(self._outbound_buffer) >= 960:
                                        frame = bytes(self._outbound_buffer[:960])
                                        del self._outbound_buffer[:960]
                                        recv_count += 1
                                        if recv_count % 100 == 1:
                                            logger.info("Gemini audio buffered count=%d", recv_count)
                                        try:
                                            self.outbound_queue.put_nowait(frame)
                                        except asyncio.QueueFull:
                                            self.outbound_drops += 1

                        if getattr(sc, "turn_complete", False):
                            self._model_speaking = False
                            self._model_speech_end_time = time.time()
                            turn_count += 1
                            logger.info("Gemini turn %d complete (echo mute OFF)", turn_count)
                        if getattr(sc, "interrupted", False):
                            self._model_speaking = False
                            self._model_speech_end_time = time.time()
                            logger.info("Gemini interrupted (echo mute OFF)")

                    if getattr(response, "tool_call", None):
                        logger.info("Tool call received")
                    if getattr(response, "setup_complete", None):
                        logger.info("Gemini setup complete received")
                    if getattr(response, "go_away", None):
                        logger.warning("Gemini go_away received — session expiring")

                # turn iterator exhausted (turn_complete), loop back for next turn
                logger.info("Turn iterator done, awaiting next turn")

        except Exception:
            if not self._shutdown.is_set():
                logger.exception("Gemini recv error")

    # ------------------------------------------------------------------
    # Gateway mode loops (Cloud Run WebSocket)
    # ------------------------------------------------------------------

    async def _gateway_bidi_loop(self) -> None:
        """Bridge audio between codec pipeline and Cloud Run WebSocket.

        Reconnects on WebSocket disconnect unless shutdown is signalled
        or `live_session_ended` was received.
        """
        max_retries = 5
        retry_delay = 1.0
        for attempt in range(max_retries + 1):
            if self._shutdown.is_set():
                return
            if attempt > 0:
                # Reconnect after previous disconnect
                delay = min(retry_delay * (2 ** (attempt - 1)), 5.0)
                logger.info("Gateway reconnect attempt %d in %.1fs", attempt, delay)
                await asyncio.sleep(delay)
                if self._shutdown.is_set():
                    return
                try:
                    await self.gateway_client.reconnect()
                except Exception:
                    logger.warning("Gateway reconnect failed", exc_info=True)
                    continue

            send_task = asyncio.create_task(self._gateway_send_loop())
            recv_task = asyncio.create_task(self._gateway_recv_loop())
            task_error: Exception | None = None
            try:
                done, pending = await asyncio.wait(
                    {send_task, recv_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        continue
                    except Exception as exc:
                        task_error = exc
                        logger.warning("Gateway loop task failed", exc_info=True)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            finally:
                for t in (send_task, recv_task):
                    if not t.done():
                        t.cancel()

            # If shutdown was set (e.g. live_session_ended), don't retry
            if self._shutdown.is_set():
                return
            if task_error is not None:
                logger.warning("Gateway WebSocket disconnected after task error: %s", task_error)
            else:
                logger.warning("Gateway WebSocket disconnected, will retry")

        # Retries exhausted — tear down the call
        logger.error("Gateway reconnect retries exhausted, shutting down call")
        self._shutdown.set()

    async def _gateway_send_loop(self) -> None:
        """Read PCM16 from inbound pipeline, send to Cloud Run.

        In gateway mode the first greeting is non-interruptible, so we mute caller
        audio only while that greeting lock is active. After the greeting finishes,
        we keep streaming real caller audio upstream so Cloud Run/Gemini can detect
        interruption and barge-in for later speech.
        """
        while not self._shutdown.is_set():
            try:
                pcm16 = await asyncio.wait_for(
                    self._gemini_in_queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue

            echo_muted = (
                self._model_speaking
                or (time.time() - self._model_speech_end_time) < ECHO_HOLDOFF_SEC
            )
            gateway_client = self.gateway_client
            if gateway_client is None:
                await asyncio.sleep(0.05)
                continue
            # Release greeting lock once the outbound RTP pipeline has
            # drained — i.e. no new audio frame was sent for 500ms after
            # agent_status=idle signalled the model finished speaking.
            # This is data-driven: the lock tracks actual audio delivery,
            # not an arbitrary timer.  A safety deadline (10s) covers the
            # edge case where the outbound pipeline never sends a frame.
            if (
                self._greeting_lock_active
                and self._greeting_lock_pending_release
            ):
                now = time.monotonic()
                outbound_drained = (
                    self._last_outbound_rtp_sent_at > 0
                    and now - self._last_outbound_rtp_sent_at > 0.5
                )
                safety_timeout = now >= self._greeting_lock_safety_deadline
                if outbound_drained or safety_timeout:
                    self._greeting_lock_active = False
                    self._greeting_lock_pending_release = False
                    logger.info(
                        "Greeting lock released (%s)",
                        "outbound audio drained" if outbound_drained else "safety timeout",
                    )

            await gateway_client.send_audio(
                SILENCE_FRAME if self._greeting_lock_active else pcm16
            )

    async def _gateway_recv_loop(self) -> None:
        """Receive from Cloud Run, route audio to outbound, handle JSON protocol."""
        gateway_client = self.gateway_client
        if gateway_client is None:
            return
        async for frame in gateway_client.receive():
            if self._shutdown.is_set():
                break
            if frame.is_audio:
                # PCM16 24kHz from Gemini Live — pass to outbound pipeline
                self._model_speaking = True
                if self._end_after_speaking_pending:
                    self._end_after_speaking_audio_seen = True
                self._first_outbound_audio_ready.set()
                if (
                    self._suppress_postanswer_agent_audio_until_user_speaks
                    and self._answered()
                    and not self._user_spoke_after_answer
                ):
                    self._suppressed_agent_audio_frames += 1
                    if self._suppressed_agent_audio_frames == 1:
                        logger.info(
                            "Suppressing callback agent audio after answer until user speaks call_id=%s",
                            self.call_id,
                        )
                    continue
                try:
                    self.outbound_queue.put_nowait(frame.audio_data)
                except asyncio.QueueFull:
                    self.outbound_drops += 1
            else:
                try:
                    msg = json.loads(frame.text_data)
                except json.JSONDecodeError:
                    session_ref = (
                        gateway_client.canonical_session_id
                        or gateway_client.session_id
                    )
                    logger.warning(
                        "Ignoring malformed gateway JSON call_id=%s session_id=%s payload=%r",
                        self.call_id,
                        session_ref,
                        frame.text_data[:200],
                        exc_info=True,
                    )
                    continue
                msg_type = msg.get("type", "")
                if msg_type == "session_started":
                    canonical_id = msg.get("sessionId", "")
                    if canonical_id:
                        gateway_client.remember_canonical_session_id(canonical_id)
                    logger.info("Gateway session started: %s", canonical_id)
                    # Trigger AI greeting only on first connect, not on reconnect
                    if not self._gateway_greeting_sent:
                        self._gateway_greeting_sent = True
                        self._greeting_lock_active = True
                        try:
                            await gateway_client.send_text(json.dumps({
                                "type": "text",
                                "text": self.connect_greeting_text,
                            }))
                        except Exception:
                            logger.warning("Failed to send gateway greeting", exc_info=True)
                    else:
                        logger.info("Gateway reconnected — skipping duplicate greeting")
                elif msg_type == "transcription":
                    role = msg.get("role")
                    partial = bool(msg.get("partial"))
                    text = msg.get("text", "")[:100]
                    if role == "user":
                        if self._answered():
                            self._user_spoke_after_answer = True
                            self._suppress_postanswer_agent_audio_until_user_speaks = False
                        logger.info(
                            "Gateway user transcription partial=%s call_id=%s text=%s",
                            partial,
                            self.call_id,
                            text,
                        )
                    elif role == "agent" and not partial:
                        if not self._answered():
                            self._preanswer_agent_final_seen = True
                        logger.info(
                            "Gateway agent transcription final call_id=%s text=%s",
                            self.call_id,
                            text,
                        )
                    else:
                        logger.debug("Transcription [%s]: %s", role, text)
                elif msg_type == "session_ending":
                    reason = msg.get("reason", "")
                    logger.info("Gateway session ending: reason=%s", reason)
                    if reason == "live_session_ended":
                        self._shutdown.set()
                    elif reason == "session_resumption":
                        gateway_client.remember_resumption_token(
                            msg.get("resumptionToken", "")
                        )
                        logger.info("Resumption token received")
                    elif reason == "go_away":
                        logger.warning(
                            "GoAway, timeLeft=%s ms", msg.get("timeLeftMs")
                        )
                elif msg_type == "ping":
                    pass  # Server keepalive is one-way — do NOT respond
                elif msg_type == "interrupted":
                    self._model_speaking = False
                    self._greeting_lock_active = False
                    self._greeting_lock_pending_release = False
                    self._model_speech_end_time = time.time()
                    self._clear_outbound_audio()
                elif msg_type == "agent_status":
                    if msg.get("status") == "idle":
                        self._model_speaking = False
                        self._model_speech_end_time = time.time()
                        # Don't release greeting lock immediately — the
                        # outbound queue may still contain greeting audio
                        # that hasn't been played to the caller yet.
                        # Releasing now lets caller audio reach the model,
                        # which self-interrupts and clears the queue,
                        # truncating the greeting.  Use a time-based grace
                        # period so the greeting fully plays before the
                        # caller's audio flows through.
                        if self._greeting_lock_active:
                            self._greeting_lock_pending_release = True
                            self._greeting_lock_safety_deadline = time.monotonic() + 10.0
                        if self._end_after_speaking_pending:
                            self._end_after_speaking_idle_seen = True
                            self._end_after_speaking_deadline = time.monotonic() + (
                                0.75 if self._end_after_speaking_audio_seen else 0.5
                            )
                elif msg_type == "agent_transfer":
                    session_id = msg.get("sessionId", "")
                    if session_id:
                        gateway_client.remember_canonical_session_id(session_id)
                    resumption_token = msg.get("resumptionToken", "")
                    if resumption_token:
                        gateway_client.remember_resumption_token(resumption_token)
                    logger.info(
                        "Gateway agent transfer: type=%s from=%s to=%s reason=%s sessionId=%s resumptionToken=%s",
                        msg.get("transferType", ""),
                        msg.get("from", ""),
                        msg.get("to", ""),
                        msg.get("reason", ""),
                        session_id,
                        bool(resumption_token),
                    )
                elif msg_type == "error":
                    logger.warning("Gateway error: %s", msg.get("message", ""))
                elif msg_type == "call_control":
                    action = str(msg.get("action", "")).strip().lower()
                    if action == "end_after_speaking":
                        self._end_after_speaking_pending = True
                        self._end_after_speaking_audio_seen = False
                        self._end_after_speaking_idle_seen = False
                        self._end_after_speaking_deadline = time.monotonic() + 6.0
                        logger.info(
                            "Received call_control end_after_speaking call_id=%s reason=%s",
                            self.call_id,
                            msg.get("reason", ""),
                        )
                else:
                    logger.debug(
                        "Gateway JSON [%s]: %s",
                        msg_type, frame.text_data[:200],
                    )
                # Track agent transcription end as secondary signal
                if (
                    msg_type == "transcription"
                    and msg.get("role") == "agent"
                    and not msg.get("partial")
                ):
                    self._model_speech_end_time = time.time()
                    self._model_speaking = False

    async def _media_outbound_loop(self) -> None:
        """Encode PCM16 24kHz → G.711, split into 160-byte RTP frames, send."""
        ssrc = int.from_bytes(os.urandom(4), "big")
        seq = 0
        timestamp = 0
        timer: RTPTimer | None = None
        g711_buffer = bytearray()
        target_logged = False

        FRAME_SIZE = 160  # 160 bytes = 20ms at 8kHz
        callback_silence_frame: bytes | None = None

        while not self._shutdown.is_set():
            self._maybe_finish_end_after_speaking()
            if self._shutdown.is_set():
                break
            if not self._media_send_enabled.is_set():
                try:
                    await asyncio.wait_for(self._media_send_enabled.wait(), timeout=0.5)
                except TimeoutError:
                    self._maybe_finish_end_after_speaking()
                    continue
            if self._callback_post_answer_grace_active():
                if callback_silence_frame is None:
                    try:
                        if self.codec_bridge is not None:
                            callback_silence_frame = self.codec_bridge.encode_from_pcm16_24k(
                                SILENCE_FRAME_24K
                            )
                        else:
                            callback_silence_frame = SILENCE_FRAME_24K
                    except Exception:
                        logger.debug("Callback silence encode error", exc_info=True)
                        await asyncio.sleep(0.02)
                        continue
                if len(g711_buffer) < FRAME_SIZE:
                    g711_buffer.extend(callback_silence_frame)
            if len(g711_buffer) < FRAME_SIZE:
                try:
                    pcm_frame = await asyncio.wait_for(
                        self.outbound_queue.get(), timeout=1.0
                    )
                except TimeoutError:
                    self._maybe_finish_end_after_speaking()
                    continue

                try:
                    if self._webrtc_apm is not None and pcm_frame:
                        try:
                            reverse_pcm16 = resample_24k_to_16k(pcm_frame)
                            self._process_webrtc_apm_stream(reverse_pcm16, reverse=True)
                        except Exception:
                            logger.debug("WebRTC APM reverse-stream error", exc_info=True)
                    if self.codec_bridge is not None:
                        encoded = self.codec_bridge.encode_from_pcm16_24k(pcm_frame)
                    else:
                        encoded = pcm_frame
                    g711_buffer.extend(encoded)
                except Exception:
                    logger.debug("Outbound encode error", exc_info=True)
                    continue

            while len(g711_buffer) >= FRAME_SIZE and not self._shutdown.is_set():
                if timer is None:
                    timer = RTPTimer()

                frame_data = bytes(g711_buffer[:FRAME_SIZE])
                del g711_buffer[:FRAME_SIZE]

                marker = (self.frames_sent == 0)
                payload_type = (
                    getattr(self.codec_bridge, "rtp_payload_type", PCMU_PAYLOAD_TYPE)
                    if self.codec_bridge is not None
                    else PCMU_PAYLOAD_TYPE
                )
                pkt = RTPPacket(
                    version=2,
                    payload_type=payload_type,
                    sequence=seq & 0xFFFF,
                    timestamp=timestamp & 0xFFFFFFFF,
                    ssrc=ssrc,
                    marker=marker,
                    payload=frame_data,
                )

                try:
                    if self.media_transport is not None and self.remote_rtp_addr is not None:
                        if not target_logged:
                            logger.info(
                                "RTP outbound target local_port=%d remote=%s",
                                self.local_rtp_port, self.remote_rtp_addr,
                            )
                            target_logged = True
                        self.media_transport.sendto(pkt.serialize(), self.remote_rtp_addr)
                        self._last_outbound_rtp_sent_at = time.monotonic()
                except Exception:
                    logger.debug("Outbound send error", exc_info=True)

                self.frames_sent += 1
                if self.frames_sent % 50 == 1:
                    logger.info(
                        "RTP frames sent count=%d call_id=%s",
                        self.frames_sent, self.call_id,
                    )

                if (
                    not self._no_inbound_warned
                    and self.frames_sent >= 100
                    and self.frames_received == 0
                ):
                    self._no_inbound_warned = True
                    logger.warning(
                        "No inbound RTP received while outbound audio is active. "
                        "Verify SIP_PUBLIC_IP and UDP firewall rules for RTP media ports (10000-20000).",
                    )

                seq += 1
                timestamp += FRAME_SIZE
                await timer.wait_next_frame()
