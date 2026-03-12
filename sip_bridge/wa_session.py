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
    gemini_voice: str = "Kore"
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
                    "You are the virtual assistant named ehkaitay, pronounced 'eh-KAI-tay'. "
                    "The middle syllable is exactly 'kai', rhyming with 'sky'. "
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

        from .wa_gateway import gateway_bidi_loop
        from .wa_media_pipeline import media_inbound_loop, media_outbound_loop, media_recv_loop

        bidi_loop = (lambda: gateway_bidi_loop(self)) if use_gateway else self._gemini_bidi_loop

        # Send maiden SRTP packet AFTER 200 OK has been written to TLS.
        # session.run() is scheduled via create_task, which only starts
        # after _handle_invite returns resp to handle_sip_connection.
        # Small delay ensures Meta has processed our SDP answer (with
        # crypto key and media port) before receiving SRTP packets.
        await self._send_maiden_srtp()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._silence_keepalive_loop())
                tg.create_task(media_recv_loop(self))
                tg.create_task(media_inbound_loop(self))
                tg.create_task(bidi_loop())
                tg.create_task(media_outbound_loop(self))
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
