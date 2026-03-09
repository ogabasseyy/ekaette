"""WhatsApp call session — Opus/SRTP/Gemini media pipeline.

4-task TaskGroup pattern:
1. _media_recv_loop: UDP recvfrom → feed_inbound (SRTP frames from network)
2. _media_inbound_loop: SRTP unprotect → Opus decode → PCM16 → Gemini
3. _gemini_bidi_loop: Gemini Live WebSocket (shared client)
4. _media_outbound_loop: PCM16 → Opus encode → SRTP protect → send

State boundaries (from plan):
- Owns: Media pipeline (encode/decode/Gemini loops), Firestore call records
- Must NOT touch: SIP signaling, TLS transport
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from google import genai

if TYPE_CHECKING:
    from .codec_bridge import CodecBridge
    from .gateway_client import GatewayClient
    from .srtp_context import SRTPContext

logger = logging.getLogger(__name__)

# Bounded queue sizes (backpressure)
INBOUND_QUEUE_SIZE = 500
OUTBOUND_QUEUE_SIZE = 10000
UDP_TIMEOUT_SEC = 600
MODEL_OUTPUT_SAMPLE_RATE = max(
    8000,
    int(os.getenv("WA_MODEL_OUTPUT_SAMPLE_RATE", "24000")),
)
MODEL_OUTPUT_CHANNELS = max(
    1,
    int(os.getenv("WA_MODEL_OUTPUT_CHANNELS", "1")),
)
GATEWAY_AUDIO_STALL_UNMUTE_SEC = 1.0

# Echo suppression: send silence while model speaks + holdoff
SILENCE_FRAME = b"\x00" * 640  # 20ms of silence at 16kHz
ECHO_HOLDOFF_SEC = 0.5


def build_wa_vad_config():
    """Build VAD config optimized for WhatsApp telephone audio (2026 best practices).

    Same telephone-optimized values as AT bridge. Env vars use WA_ prefix.
    """
    from google.genai import types

    return types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            startOfSpeechSensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
            endOfSpeechSensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefixPaddingMs=int(os.getenv("WA_AUTO_VAD_PREFIX_PADDING_MS", "120")),
            silenceDurationMs=int(os.getenv("WA_AUTO_VAD_SILENCE_DURATION_MS", "450")),
        ),
        activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        turn_coverage=types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
    )


def compute_rtp_timestamp_increment(clock_rate: int, frame_duration_ms: int) -> int:
    """Compute RTP timestamp increment per frame.

    Opus at 48kHz with 20ms frames = 960.
    G.711 at 8kHz with 20ms frames = 160.
    """
    return clock_rate * frame_duration_ms // 1000


def downmix_pcm16_to_mono(pcm16: bytes, channels: int) -> bytes:
    """Downmix interleaved PCM16 audio to mono."""
    if channels <= 1 or not pcm16:
        return pcm16
    sample_count = len(pcm16) // 2
    if sample_count == 0:
        return b""
    truncated_samples = sample_count - (sample_count % channels)
    if truncated_samples <= 0:
        return b""
    samples = struct.unpack(f"<{truncated_samples}h", pcm16[: truncated_samples * 2])
    mono = []
    for i in range(0, truncated_samples, channels):
        frame = samples[i : i + channels]
        mono.append(sum(frame) // channels)
    return struct.pack(f"<{len(mono)}h", *mono)


@dataclass(slots=True)
class WaSession:
    """Per-call WhatsApp session managing Opus/SRTP ↔ Gemini Live bridge."""

    call_id: str
    tenant_id: str
    company_id: str
    codec_bridge: CodecBridge | None = None
    srtp_sender: SRTPContext | None = None
    srtp_receiver: SRTPContext | None = None
    firestore_db: Any = None
    gemini_api_key: str = ""
    gemini_model_id: str = ""
    gemini_system_instruction: str = ""
    gemini_voice: str = "Aoede"
    gemini_session: Any = None
    # Gateway mode (Cloud Run WebSocket)
    gateway_client: GatewayClient | None = None
    media_transport: Any = None
    remote_media_addr: tuple[str, int] | None = None
    _caller_phone: str = ""
    _bridge_config: Any | None = None
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

    # Ownership tracking
    _owns_transport: bool = False

    # Echo suppression
    _model_speaking: bool = False
    _model_speech_end_time: float = 0.0
    _gateway_greeting_sent: bool = False
    _greeting_lock_active: bool = False
    _last_model_audio_at: float = 0.0
    _gateway_audio_frames_received: int = 0
    _gateway_audio_bytes_received: int = 0
    _gateway_send_frames: int = 0
    rtp_ssrc: int = field(default_factory=lambda: int.from_bytes(os.urandom(4), "big"))
    rtp_sequence: int = 0
    rtp_timestamp: int = 0

    # Metrics
    frames_received: int = 0
    frames_sent: int = 0
    inbound_drops: int = 0
    outbound_drops: int = 0
    _no_inbound_warned: bool = False

    # ACK synchronization: maiden SRTP waits until Meta ACKs our 200 OK
    _ack_event: asyncio.Event = field(default_factory=asyncio.Event)

    def notify_ack(self) -> None:
        """Called by SIP server when ACK is received for this call."""
        self._ack_event.set()

    def _clear_outbound_audio(self) -> None:
        """Drop queued playback so interrupted speech stops immediately."""
        while True:
            try:
                self.outbound_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def run(self) -> None:
        """Run the three concurrent tasks. Cancels all on first failure."""
        logger.info(
            "WA session started",
            extra={
                "call_id": self.call_id,
                "tenant_id": self.tenant_id,
                "company_id": self.company_id,
            },
        )
        self._write_call_start()

        # Create UDP transport for media if not injected externally
        if self.media_transport is None and self.remote_media_addr is not None:
            self.media_transport = socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM,
            )
            self.media_transport.setblocking(False)
            self._owns_transport = True

        # Gateway mode: connect via Cloud Run WebSocket
        use_gateway = self.gateway_client is not None
        gemini_ctx = None

        if use_gateway:
            try:
                await self.gateway_client.connect()
                logger.info("Gateway mode: WA connected to Cloud Run")
            except Exception:
                logger.exception("Failed to connect to gateway")
                self._cleanup_transport()
                self._write_call_end(time.time() - self.started_at)
                return
        elif self.gemini_session is None and self.gemini_api_key:
            # Direct mode: connect to Gemini Live
            try:
                sys_instruct = (
                    "You are the virtual assistant named ehkaitay. "
                    "Your name is ehkaitay — always say it exactly like that. "
                    "You are answering a WhatsApp call. Greet the caller warmly and ask how you can help. "
                    "Always speak in English. "
                    "Be helpful, concise, and professional. Keep responses short for phone conversation."
                )
                if self.gemini_system_instruction and isinstance(self.gemini_system_instruction, dict):
                    parts = self.gemini_system_instruction.get("parts", [{"text": sys_instruct}])
                    sys_instruct = parts[0].get("text", sys_instruct)

                from google.genai import types

                speech_config = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self.gemini_voice,
                        )
                    )
                )

                # During-call messaging tool
                from .wa_tools import SEND_WA_MESSAGE_TOOL
                tools_list = [SEND_WA_MESSAGE_TOOL]

                live_config = {
                    "response_modalities": ["AUDIO"],
                    "speech_config": speech_config,
                    "system_instruction": {"parts": [{"text": sys_instruct}]},
                    "input_audio_transcription": types.AudioTranscriptionConfig(),
                    "output_audio_transcription": types.AudioTranscriptionConfig(),
                    "proactivity": types.ProactivityConfig(proactive_audio=True),
                    "tools": tools_list,
                }

                if getattr(self, "_use_explicit_vad", False):
                    live_config["realtime_input_config"] = types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            disabled=True
                        )
                    )
                else:
                    live_config["realtime_input_config"] = build_wa_vad_config()

                client = genai.Client(
                    api_key=self.gemini_api_key,
                    http_options=types.HttpOptions(api_version="v1alpha"),
                )
                gemini_ctx = client.aio.live.connect(
                    model=self.gemini_model_id,
                    config=live_config,
                )
                self.gemini_session = await gemini_ctx.__aenter__()
                logger.info("Gemini Live connected for WhatsApp")

                # Trigger proactive greeting (Pipecat pattern):
                # send_client_content ONCE before audio stream starts.
                # Sequential use is fine — only interleaving breaks VAD.
                self._model_speaking = True
                await self.gemini_session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text="[Call connected]")],
                    ),
                    turn_complete=True,
                )
                logger.info("WhatsApp greeting trigger sent")

            except Exception:
                logger.exception("Failed to connect to Gemini Live")
                self._model_speaking = False
                self.gemini_session = None
                if gemini_ctx is not None:
                    try:
                        await gemini_ctx.__aexit__(None, None, None)
                    except Exception:
                        logger.debug("Gemini cleanup failed after connect error", exc_info=True)
                self._cleanup_transport()
                self._write_call_end(time.time() - self.started_at)
                return

        bidi_loop = self._gateway_bidi_loop if use_gateway else self._gemini_bidi_loop

        # Send maiden SRTP packet AFTER 200 OK has been written to TLS.
        # session.run() is scheduled via create_task, which only starts
        # after _handle_invite returns resp to handle_sip_connection.
        # Small delay ensures Meta has processed our SDP answer (with
        # crypto key and media port) before receiving SRTP packets.
        await self._send_maiden_srtp()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._silence_keepalive_loop())
                tg.create_task(self._media_recv_loop())
                tg.create_task(self._media_inbound_loop())
                tg.create_task(bidi_loop())
                tg.create_task(self._media_outbound_loop())
        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error("WA session task failed", exc_info=exc)
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
                    # Best-effort cleanup; connection may already be closed.
                    pass
            # Clean up UDP socket only if we created it
            self._cleanup_transport()

            duration = time.time() - self.started_at
            self._write_call_end(duration)
            logger.info(
                "WA session ended",
                extra={
                    "call_id": self.call_id,
                    "duration_seconds": round(duration, 2),
                    "frames_received": self.frames_received,
                    "frames_sent": self.frames_sent,
                    "inbound_drops": self.inbound_drops,
                    "outbound_drops": self.outbound_drops,
                },
            )

    def _cleanup_transport(self) -> None:
        """Close owned UDP transport if still open."""
        if self._owns_transport and self.media_transport is not None:
            try:
                self.media_transport.close()
            except Exception:  # noqa: S110 — best-effort cleanup of UDP transport
                pass
            self.media_transport = None

    def shutdown(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown.set()

    async def _send_maiden_srtp(self) -> None:
        """Send the first SRTP packet to Meta after ACK confirms 200 OK receipt.

        Meta requires the business to send the first media packet before
        it starts flowing RTP back.  We wait for the ACK (which proves
        Meta has processed our 200 OK/SDP) before sending, to avoid a
        race where our SRTP arrives before Meta's relay has our crypto key.
        """
        if self.media_transport is None or self.remote_media_addr is None:
            return
        if self.srtp_sender is None:
            return

        # Wait for ACK from Meta (up to 5 seconds, then proceed anyway).
        # Meta's relay has a ~700ms timeout for receiving the first SRTP
        # packet after ACK, so we must send maiden SRTP immediately after
        # ACK — no additional delay.
        try:
            await asyncio.wait_for(self._ack_event.wait(), timeout=5.0)
            logger.info(
                "ACK received, sending maiden SRTP now",
                extra={"call_id": self.call_id},
            )
        except TimeoutError:
            logger.warning(
                "ACK timeout after 5s, sending maiden SRTP anyway",
                extra={"call_id": self.call_id},
            )

        # Send a single STUN binding request to help Meta's relay create
        # a reverse-path entry.  Minimal 20-byte per RFC 5389 §6.
        try:
            stun_txn_id = os.urandom(12)
            stun_request = (
                b"\x00\x01"   # Type: Binding Request
                b"\x00\x00"   # Length: 0 (no attributes)
                b"\x21\x12\xa4\x42"  # Magic Cookie
                + stun_txn_id
            )
            self.media_transport.sendto(stun_request, self.remote_media_addr)
        except Exception:
            pass
        logger.info(
            "STUN binding request sent to %s",
            self.remote_media_addr,
            extra={"call_id": self.call_id},
        )

        from .rtp import RTPPacket

        opus_silence = b"\xf8\xff\xfe"  # Fallback comfort-noise payload.
        codec_bridge = self.codec_bridge
        frame_duration_ms = getattr(codec_bridge, "frame_duration_ms", 20) if codec_bridge else 20
        if not isinstance(frame_duration_ms, int):
            frame_duration_ms = 20
        clock_rate = getattr(codec_bridge, "rtp_clock_rate", 48000) if codec_bridge else 48000
        if not isinstance(clock_rate, int):
            clock_rate = 48000
        if codec_bridge is not None:
            silence_frame = b"\x00" * (24000 * frame_duration_ms // 1000 * 2)
            try:
                opus_silence = codec_bridge.encode_from_pcm16_24k(silence_frame)
            except Exception:
                logger.warning(
                    "Failed to encode maiden Opus silence, using fallback comfort noise",
                    exc_info=True,
                    extra={"call_id": self.call_id},
                )
        maiden_rtp = RTPPacket(
            version=2,
            payload_type=getattr(self.codec_bridge, "rtp_payload_type", 111),
            sequence=self.rtp_sequence & 0xFFFF,
            timestamp=self.rtp_timestamp & 0xFFFFFFFF,
            ssrc=self.rtp_ssrc,
            marker=True,
            payload=opus_silence,
        ).serialize()
        try:
            maiden_srtp = self.srtp_sender.protect(maiden_rtp)
            self.media_transport.sendto(maiden_srtp, self.remote_media_addr)
            timestamp_increment = compute_rtp_timestamp_increment(clock_rate, frame_duration_ms)
            self.rtp_sequence += 1
            self.rtp_timestamp += timestamp_increment
            logger.info(
                "Maiden SRTP sent to %s (%d bytes, SSRC=%08x seq=%d ts=%d)",
                self.remote_media_addr,
                len(maiden_srtp),
                self.rtp_ssrc,
                self.rtp_sequence - 1,
                self.rtp_timestamp - timestamp_increment,
            )
        except Exception:
            logger.warning(
                "Failed to send maiden SRTP packet",
                exc_info=True,
                extra={"call_id": self.call_id},
            )

    async def _silence_keepalive_loop(self) -> None:
        """Send Opus silence at 20ms intervals until real audio starts flowing.

        Meta tears down the call if it doesn't see continuous RTP within ~1s
        of the 200 OK.  This fills the gap between the maiden SRTP packet
        and the first real audio frame from Gemini/gateway.
        """
        if self.media_transport is None or self.remote_media_addr is None:
            return
        if self.srtp_sender is None:
            return

        from .rtp import RTPPacket

        opus_silence = b"\xf8\xff\xfe"
        codec_bridge = self.codec_bridge
        if codec_bridge is not None:
            frame_duration_ms = getattr(codec_bridge, "frame_duration_ms", 20)
            if not isinstance(frame_duration_ms, int):
                frame_duration_ms = 20
            silence_pcm = b"\x00" * (24000 * frame_duration_ms // 1000 * 2)
            try:
                opus_silence = codec_bridge.encode_from_pcm16_24k(silence_pcm)
            except Exception:
                pass

        payload_type = getattr(self.codec_bridge, "rtp_payload_type", 111)
        if not isinstance(payload_type, int):
            payload_type = 111
        clock_rate = getattr(self.codec_bridge, "rtp_clock_rate", 48000)
        if not isinstance(clock_rate, int):
            clock_rate = 48000
        frame_duration_ms = getattr(self.codec_bridge, "frame_duration_ms", 20)
        if not isinstance(frame_duration_ms, int):
            frame_duration_ms = 20
        ts_step = compute_rtp_timestamp_increment(clock_rate, frame_duration_ms)

        keepalive_sent = 0

        while not self._shutdown.is_set():
            # Stop once the real outbound loop has sent frames
            if self.frames_sent > 0:
                logger.info(
                    "Silence keepalive stopping: real audio started after %d keepalive frames",
                    keepalive_sent,
                )
                return

            rtp = RTPPacket(
                version=2,
                payload_type=payload_type,
                sequence=self.rtp_sequence & 0xFFFF,
                timestamp=self.rtp_timestamp & 0xFFFFFFFF,
                ssrc=self.rtp_ssrc,
                payload=opus_silence,
            ).serialize()
            try:
                protected = self.srtp_sender.protect(rtp)
                self.media_transport.sendto(protected, self.remote_media_addr)
                keepalive_sent += 1
                # Advance shared RTP state so outbound loop continues seamlessly
                self.rtp_sequence += 1
                self.rtp_timestamp += ts_step
            except Exception:
                if keepalive_sent == 0:
                    logger.warning(
                        "Silence keepalive failed on first packet",
                        exc_info=True,
                        extra={"call_id": self.call_id},
                    )
                return

            await asyncio.sleep(0.02)  # 20ms pacing

    async def feed_inbound(self, frame: bytes) -> None:
        """Feed an SRTP audio frame from the network side."""
        try:
            self.inbound_queue.put_nowait(frame)
            self.frames_received += 1
            if self.frames_received % 50 == 1:
                logger.info(
                    "WA RTP frames received count=%d call_id=%s",
                    self.frames_received,
                    self.call_id,
                )
        except asyncio.QueueFull:
            self.inbound_drops += 1

    # ------------------------------------------------------------------
    # Firestore call-state persistence
    # ------------------------------------------------------------------

    def _get_call_doc(self):
        """Get Firestore document ref for this call (call_id = doc ID)."""
        if self.firestore_db is None:
            return None
        return self.firestore_db.collection("wa_calls").document(self.call_id)

    def _write_call_start(self) -> None:
        """Write call start record. Uses set(merge=True) for idempotency."""
        doc = self._get_call_doc()
        if doc is None:
            return
        doc.set(
            {
                "call_id": self.call_id,
                "tenant_id": self.tenant_id,
                "company_id": self.company_id,
                "status": "active",
                "started_at": self.started_at,
            },
            merge=True,
        )

    def _write_call_end(self, duration: float) -> None:
        """Write call end record. Conditional update prevents duplicate termination."""
        doc = self._get_call_doc()
        if doc is None:
            return
        doc.update(
            {
                "status": "terminated",
                "duration_seconds": round(duration, 2),
                "frames_received": self.frames_received,
                "frames_sent": self.frames_sent,
                "ended_at": time.time(),
            }
        )

    # ------------------------------------------------------------------
    # Media loops
    # ------------------------------------------------------------------

    async def _media_recv_loop(self) -> None:
        """Read SRTP packets from UDP socket and feed into inbound queue."""
        from .rtp import is_rtcp_packet

        loop = asyncio.get_running_loop()
        use_symmetric_rtp = (
            os.getenv("WA_SIP_SYMMETRIC_RTP", "")
            or os.getenv("SIP_SYMMETRIC_RTP", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}
        first_inbound_logged = False
        if self.media_transport is None:
            # No transport — idle until shutdown (test/sandbox mode)
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
                        "WA RTP inbound first source=%s configured_remote=%s symmetric=%s",
                        addr,
                        self.remote_media_addr,
                        use_symmetric_rtp,
                    )
                    if (
                        self.remote_media_addr is not None
                        and self.remote_media_addr != addr
                        and not use_symmetric_rtp
                    ):
                        logger.warning(
                            "WA RTP source differs from SDP remote (source=%s, sdp=%s). "
                            "If caller audio is missing, enable WA_SIP_SYMMETRIC_RTP=1.",
                            addr,
                            self.remote_media_addr,
                        )
                if self.remote_media_addr is None:
                    self.remote_media_addr = (addr[0], addr[1])
                    logger.info("WA RTP remote set from first inbound source remote=%s", self.remote_media_addr)
                elif use_symmetric_rtp and self.remote_media_addr != addr:
                    logger.info(
                        "WA RTP remote updated by symmetric latching old=%s new=%s",
                        self.remote_media_addr,
                        addr,
                    )
                    self.remote_media_addr = (addr[0], addr[1])
                elif self.remote_media_addr != addr:
                    continue
                # Filter out non-RTP packets (STUN, DTLS) per RFC 5764 §5.1.2
                if len(data) < 2:
                    continue
                first_byte = data[0]
                if first_byte < 128 or first_byte > 191:
                    # Not RTP (version 2): likely STUN (0-3) or DTLS (20-63)
                    continue
                if is_rtcp_packet(data):
                    # a=rtcp-mux means RTCP shares the RTP port; this audio path
                    # only forwards SRTP voice packets to the decoder.
                    continue
                await self.feed_inbound(data)
            except TimeoutError:
                continue
            except OSError:
                # Socket closed or error — exit loop
                break

    async def _media_inbound_loop(self) -> None:
        """Read inbound SRTP frames, unprotect, decode, forward to Gemini."""
        from .rtp import RTPPacket

        decode_count = 0
        pcm_log_count = 0
        decode_error_count = 0
        while not self._shutdown.is_set():
            try:
                frame = await asyncio.wait_for(
                    self.inbound_queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue

            try:
                # SRTP unprotect → codec decode → PCM16
                if self.srtp_receiver is not None:
                    rtp_packet = self.srtp_receiver.unprotect(frame)
                else:
                    rtp_packet = frame

                rtp = RTPPacket.parse(rtp_packet)
                if rtp is None:
                    continue

                decode_count += 1
                if decode_count <= 3:
                    logger.info(
                        "WA RTP packet parsed pt=%d seq=%d payload_len=%d",
                        rtp.payload_type,
                        rtp.sequence,
                        len(rtp.payload),
                    )

                if self.codec_bridge is not None:
                    expected_payload_type = getattr(self.codec_bridge, "rtp_payload_type", None)
                    if isinstance(expected_payload_type, int) and rtp.payload_type != expected_payload_type:
                        continue
                    payload = rtp.payload
                    pcm16 = self.codec_bridge.decode_to_pcm16_16k(payload)
                else:
                    pcm16 = rtp.payload

                # Forward decoded PCM16 to Gemini via internal queue
                if pcm16:
                    pcm_log_count += 1
                    if pcm_log_count <= 3:
                        logger.info(
                            "WA RTP decoded pcm_bytes=%d nonzero=%s call_id=%s",
                            len(pcm16),
                            any(pcm16),
                            self.call_id,
                        )
                    try:
                        self._gemini_in_queue.put_nowait(pcm16)
                    except asyncio.QueueFull:
                        # Drop frame when backpressure queue is saturated.
                        pass
            except Exception:
                decode_error_count += 1
                if decode_error_count <= 3:
                    logger.warning(
                        "Inbound frame processing error call_id=%s",
                        self.call_id,
                        exc_info=True,
                    )
                else:
                    logger.debug("Inbound frame processing error", exc_info=True)

    async def _gemini_bidi_loop(self) -> None:
        """Bidirectional Gemini Live session."""
        if self.gemini_session is None:
            # No Gemini client — idle loop (test/sandbox mode)
            while not self._shutdown.is_set():
                await asyncio.sleep(0.02)
            return

        # Send/receive concurrently with Gemini Live
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._gemini_send_loop())
            tg.create_task(self._gemini_recv_loop())

    async def _gemini_send_loop(self) -> None:
        """Send decoded PCM16 audio to Gemini Live with echo suppression."""
        from google.genai import types as genai_types

        while not self._shutdown.is_set():
            try:
                pcm16 = await asyncio.wait_for(
                    self._gemini_in_queue.get(), timeout=1.0
                )
                # Echo suppression: send silence while model speaks
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
            except TimeoutError:
                continue
            except Exception:
                logger.exception("Gemini send error")
                break

    async def _gemini_recv_loop(self) -> None:
        """Receive audio from Gemini Live and queue for outbound.

        CRITICAL: session.receive() breaks on turn_complete.
        Must call in a while loop for multi-turn conversations.
        """
        try:
            while not self._shutdown.is_set():
                turn = self.gemini_session.receive()
                got_any = False
                async for response in turn:
                    got_any = True
                    if self._shutdown.is_set():
                        return
                    # Handle tool calls (e.g., send_whatsapp_message)
                    tc = getattr(response, "tool_call", None)
                    if tc is not None:
                        await self._handle_tool_call(tc)
                        continue

                    sc = getattr(response, "server_content", None)
                    if sc is None:
                        continue

                    # Track model speaking for echo suppression
                    mt = getattr(sc, "model_turn", None)
                    if mt is not None:
                        for part in mt.parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and inline.data:
                                if not self._model_speaking:
                                    self._model_speaking = True
                                try:
                                    self.outbound_queue.put_nowait(inline.data)
                                except asyncio.QueueFull:
                                    self.outbound_drops += 1

                    if getattr(sc, "turn_complete", False):
                        self._model_speaking = False
                        self._model_speech_end_time = time.time()
                # Yield control between turns to prevent busy-spinning
                # when the generator is empty or returns immediately.
                if not got_any:
                    await asyncio.sleep(0.02)
        except Exception:
            if not self._shutdown.is_set():
                logger.exception("Gemini recv error")

    async def _handle_tool_call(self, tool_call) -> None:
        """Handle Gemini tool calls (e.g., send_whatsapp_message)."""
        from .wa_tools import handle_send_wa_message
        from google.genai import types

        for fc in getattr(tool_call, "function_calls", []):
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}
            fn_id = fc.id

            logger.info("Tool call: %s(%s)", fn_name, list(fn_args.keys()))

            if fn_name == "send_whatsapp_message":
                if not self._caller_phone or self._bridge_config is None:
                    logger.warning(
                        "Skipping send_whatsapp_message: missing caller/config context",
                        extra={"call_id": self.call_id},
                    )
                    result = {"status": "error", "detail": "WhatsApp tool context unavailable"}
                else:
                    result = await handle_send_wa_message(
                        args=fn_args,
                        caller_phone=self._caller_phone,
                        config=self._bridge_config,
                    )
            else:
                result = {"status": "error", "detail": f"Unknown tool: {fn_name}"}

            # Return tool response to Gemini
            try:
                await self.gemini_session.send_tool_response(
                    function_responses=[
                        types.FunctionResponse(
                            name=fn_name,
                            id=fn_id,
                            response=result,
                        )
                    ]
                )
            except Exception:
                logger.warning("Failed to send tool response for %s", fn_name, exc_info=True)

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
            gateway_client = self.gateway_client
            if gateway_client is None:
                await asyncio.sleep(0.05)
                continue
            self._gateway_send_frames += 1
            if self._gateway_send_frames <= 3:
                logger.info(
                    "Gateway send frame=%d muted=%s bytes=%d call_id=%s",
                    self._gateway_send_frames,
                    self._greeting_lock_active,
                    len(pcm16),
                    self.call_id,
                )
            await gateway_client.send_audio(
                SILENCE_FRAME if self._greeting_lock_active else pcm16
            )

    async def _gateway_recv_loop(self) -> None:
        """Receive from Cloud Run, route audio to outbound, handle JSON protocol."""
        if self.gateway_client is None:
            return
        async for frame in self.gateway_client.receive():
            if self._shutdown.is_set():
                break
            if frame.is_audio:
                self._model_speaking = True
                self._last_model_audio_at = time.time()
                self._gateway_audio_frames_received += 1
                input_audio = frame.audio_data
                self._gateway_audio_bytes_received += len(input_audio)
                output_audio = input_audio
                if MODEL_OUTPUT_CHANNELS > 1:
                    output_audio = downmix_pcm16_to_mono(input_audio, MODEL_OUTPUT_CHANNELS)
                if self._gateway_audio_frames_received <= 5:
                    logger.info(
                        "Gateway audio frame=%d bytes=%d total_bytes=%d call_id=%s",
                        self._gateway_audio_frames_received,
                        len(input_audio),
                        self._gateway_audio_bytes_received,
                        self.call_id,
                    )
                    if MODEL_OUTPUT_CHANNELS > 1:
                        logger.info(
                            "Gateway audio downmix channels=%d input_bytes=%d mono_bytes=%d call_id=%s",
                            MODEL_OUTPUT_CHANNELS,
                            len(input_audio),
                            len(output_audio),
                            self.call_id,
                        )
                if not output_audio:
                    continue
                try:
                    self.outbound_queue.put_nowait(output_audio)
                except asyncio.QueueFull:
                    self.outbound_drops += 1
            else:
                try:
                    msg = json.loads(frame.text_data)
                except json.JSONDecodeError:
                    session_ref = ""
                    if self.gateway_client is not None:
                        session_ref = (
                            self.gateway_client.canonical_session_id
                            or self.gateway_client.session_id
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
                        self.gateway_client.remember_canonical_session_id(canonical_id)
                    logger.info("Gateway session started: %s", canonical_id)
                    if not self._gateway_greeting_sent:
                        # Trigger the virtual assistant greeting once per call.
                        # Do not pre-emptively mute caller audio here. We only
                        # lock interruption for the first greeting turn only.
                        self._gateway_greeting_sent = True
                        self._greeting_lock_active = True
                        try:
                            await self.gateway_client.send_text(json.dumps({
                                "type": "text",
                                "text": "[Call connected]",
                            }))
                        except Exception:
                            self._model_speaking = False
                            logger.warning(
                                "Failed to send virtual assistant greeting",
                                exc_info=True,
                            )
                elif msg_type == "session_ending":
                    reason = msg.get("reason", "")
                    logger.info("Gateway session ending: reason=%s", reason)
                    if reason == "live_session_ended":
                        self._shutdown.set()
                    elif reason == "session_resumption":
                        self.gateway_client.remember_resumption_token(
                            msg.get("resumptionToken", "")
                        )
                elif msg_type == "ping":
                    pass
                elif msg_type == "interrupted":
                    logger.info("Gateway interrupted call_id=%s", self.call_id)
                    self._model_speaking = False
                    self._greeting_lock_active = False
                    self._model_speech_end_time = time.time()
                    self._clear_outbound_audio()
                elif msg_type == "agent_status":
                    logger.info(
                        "Gateway agent_status=%s call_id=%s",
                        msg.get("status", ""),
                        self.call_id,
                    )
                    if msg.get("status") == "idle":
                        self._model_speaking = False
                        self._greeting_lock_active = False
                        self._model_speech_end_time = time.time()
                elif msg_type == "agent_transfer":
                    logger.info(
                        "Gateway agent transfer: from=%s to=%s reason=%s details=%s",
                        msg.get("from", ""),
                        msg.get("to", ""),
                        msg.get("reason", ""),
                        msg.get("details", ""),
                    )
                elif msg_type == "error":
                    logger.warning("Gateway error: %s", msg.get("message", ""))
                elif msg_type == "transcription":
                    if msg.get("role") == "user":
                        logger.info(
                            "Gateway user transcription partial=%s call_id=%s text=%s",
                            bool(msg.get("partial")),
                            self.call_id,
                            msg.get("text", "")[:100],
                        )
                    is_final_agent_transcript = (
                        msg.get("role") == "agent" and not msg.get("partial")
                    )
                    if is_final_agent_transcript:
                        logger.info(
                            "Gateway agent transcription final call_id=%s text=%s",
                            self.call_id,
                            msg.get("text", "")[:100],
                        )
                        self._model_speech_end_time = time.time()
                        self._model_speaking = False

    async def _media_outbound_loop(self) -> None:
        """Read AI response audio, encode, SRTP protect, send."""
        from .rtp import RTPPacket, RTPTimer

        ssrc = self.rtp_ssrc
        # seq/timestamp read lazily on first send so the silence keepalive
        # can advance them in the meantime.
        seq: int | None = None
        timestamp: int | None = None
        target_logged = False
        timer: RTPTimer | None = None
        pcm_buffer = bytearray()
        payload_type = getattr(self.codec_bridge, "rtp_payload_type", 111)
        if not isinstance(payload_type, int):
            payload_type = 111
        clock_rate = getattr(self.codec_bridge, "rtp_clock_rate", 48000)
        if not isinstance(clock_rate, int):
            clock_rate = 48000
        frame_duration_ms = getattr(self.codec_bridge, "frame_duration_ms", 20)
        if not isinstance(frame_duration_ms, int):
            frame_duration_ms = 20
        timestamp_step = compute_rtp_timestamp_increment(clock_rate, frame_duration_ms)
        pcm_frame_bytes = MODEL_OUTPUT_SAMPLE_RATE * frame_duration_ms // 1000 * 2

        while not self._shutdown.is_set():
            if len(pcm_buffer) < pcm_frame_bytes:
                try:
                    pcm_chunk = await asyncio.wait_for(
                        self.outbound_queue.get(), timeout=1.0
                    )
                except TimeoutError:
                    continue
                pcm_buffer.extend(pcm_chunk)

            while len(pcm_buffer) >= pcm_frame_bytes and not self._shutdown.is_set():
                if timer is None:
                    timer = RTPTimer()

                pcm_frame = bytes(pcm_buffer[:pcm_frame_bytes])
                del pcm_buffer[:pcm_frame_bytes]

                try:
                    # Lazy init: pick up RTP state from keepalive
                    if seq is None:
                        seq = self.rtp_sequence
                        timestamp = self.rtp_timestamp

                    self.frames_sent += 1
                    if self.codec_bridge is not None:
                        encoded = self.codec_bridge.encode_from_pcm16_24k(pcm_frame)
                    else:
                        encoded = pcm_frame

                    marker = self.frames_sent == 1
                    rtp = RTPPacket(
                        version=2,
                        payload_type=payload_type,
                        sequence=seq & 0xFFFF,
                        timestamp=timestamp & 0xFFFFFFFF,
                        ssrc=ssrc,
                        marker=marker,
                        payload=encoded,
                    ).serialize()

                    if self.srtp_sender is not None:
                        protected = self.srtp_sender.protect(rtp)
                    else:
                        protected = rtp

                    if self.media_transport is not None and self.remote_media_addr is not None:
                        if not target_logged:
                            local_port = "?"
                            try:
                                local_port = self.media_transport.getsockname()[1]
                            except Exception:
                                pass
                            logger.info(
                                "WA RTP outbound target local_port=%s remote=%s ssrc=%08x seq=%d timestamp=%d",
                                local_port,
                                self.remote_media_addr,
                                ssrc,
                                seq,
                                timestamp,
                            )
                            target_logged = True
                        self.media_transport.sendto(protected, self.remote_media_addr)
                    if self.frames_sent % 50 == 1:
                        logger.info(
                            "WA RTP frames sent count=%d call_id=%s",
                            self.frames_sent,
                            self.call_id,
                        )
                    if (
                        not self._no_inbound_warned
                        and self.frames_sent >= 100
                        and self.frames_received == 0
                    ):
                        self._no_inbound_warned = True
                        logger.warning(
                            "No inbound WA RTP received while outbound audio is active. "
                            "Verify WA_SIP_PUBLIC_IP, UDP firewall rules, and consider WA_SIP_SYMMETRIC_RTP=1.",
                        )
                    seq += 1
                    timestamp += timestamp_step
                    self.rtp_sequence = seq
                    self.rtp_timestamp = timestamp
                    await timer.wait_next_frame()
                except Exception:
                    if self.frames_sent <= 5:
                        logger.warning("Outbound frame processing error", exc_info=True)
                    else:
                        logger.debug("Outbound frame processing error", exc_info=True)


# ---------------------------------------------------------------------------
# Call-state TTL cleanup
# ---------------------------------------------------------------------------


def cleanup_stale_calls(db: Any, *, ttl_seconds: int = 3600) -> int:
    """Delete terminated wa_calls docs older than ttl_seconds.

    Returns the number of documents deleted.
    """
    if db is None:
        return 0
    cutoff = time.time() - ttl_seconds
    deleted = 0
    query = db.collection("wa_calls").where("status", "==", "terminated")
    for doc in query.stream():
        data = doc.to_dict()
        ended_at = data.get("ended_at", 0)
        if ended_at and ended_at < cutoff:
            doc.reference.delete()
            deleted += 1
    return deleted
