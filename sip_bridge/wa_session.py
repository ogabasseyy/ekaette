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
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from google import genai

if TYPE_CHECKING:
    from .codec_bridge import CodecBridge
    from .srtp_context import SRTPContext

logger = logging.getLogger(__name__)

# Bounded queue sizes (backpressure)
INBOUND_QUEUE_SIZE = 500
OUTBOUND_QUEUE_SIZE = 10000
UDP_TIMEOUT_SEC = 600

# Echo suppression: send silence while model speaks + holdoff
SILENCE_FRAME = b"\x00" * 640  # 20ms of silence at 16kHz
ECHO_HOLDOFF_SEC = 0.5


def compute_rtp_timestamp_increment(clock_rate: int, frame_duration_ms: int) -> int:
    """Compute RTP timestamp increment per frame.

    Opus at 48kHz with 20ms frames = 960.
    G.711 at 8kHz with 20ms frames = 160.
    """
    return clock_rate * frame_duration_ms // 1000


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
    media_transport: Any = None
    remote_media_addr: tuple[str, int] | None = None
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

    # Metrics
    frames_received: int = 0
    frames_sent: int = 0
    inbound_drops: int = 0
    outbound_drops: int = 0

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

        # Connect to Gemini Live if config is provided and no session injected
        gemini_ctx = None
        if self.gemini_session is None and self.gemini_api_key:
            try:
                sys_instruct = (
                    "You are an AI customer service assistant named ehkaitay. "
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

                live_config = {
                    "response_modalities": ["AUDIO"],
                    "speech_config": speech_config,
                    "system_instruction": {"parts": [{"text": sys_instruct}]},
                    "input_audio_transcription": types.AudioTranscriptionConfig(),
                    "output_audio_transcription": types.AudioTranscriptionConfig(),
                    "proactivity": types.ProactivityConfig(proactive_audio=True),
                }

                if getattr(self, "_use_explicit_vad", False):
                    live_config["realtime_input_config"] = types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            disabled=True
                        )
                    )
                else:
                    live_config["realtime_input_config"] = types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            disabled=False,
                            startOfSpeechSensitivity=getattr(
                                types.StartSensitivity, "START_SENSITIVITY_LOW", None
                            ),
                            endOfSpeechSensitivity=getattr(
                                types.EndSensitivity, "END_SENSITIVITY_LOW", None
                            ),
                            prefixPaddingMs=int(os.getenv("SIP_AUTO_VAD_PREFIX_PADDING_MS", "300")),
                            silenceDurationMs=int(os.getenv("SIP_AUTO_VAD_SILENCE_DURATION_MS", "1500")),
                        )
                    )

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
                gemini_ctx = None

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._media_recv_loop())
                tg.create_task(self._media_inbound_loop())
                tg.create_task(self._gemini_bidi_loop())
                tg.create_task(self._media_outbound_loop())
        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error("WA session task failed", exc_info=exc)
        finally:
            # Clean up Gemini session
            if gemini_ctx is not None:
                try:
                    await gemini_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            # Clean up UDP socket only if we created it
            if self._owns_transport and self.media_transport is not None:
                try:
                    self.media_transport.close()
                except Exception:
                    pass
                self.media_transport = None

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

    def shutdown(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown.set()

    async def feed_inbound(self, frame: bytes) -> None:
        """Feed an SRTP audio frame from the network side."""
        try:
            self.inbound_queue.put_nowait(frame)
            self.frames_received += 1
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
        loop = asyncio.get_running_loop()
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
                # Validate source IP+port matches negotiated remote endpoint
                if self.remote_media_addr is not None and addr[:2] != self.remote_media_addr:
                    continue
                await self.feed_inbound(data)
            except TimeoutError:
                continue
            except OSError:
                # Socket closed or error — exit loop
                break

    async def _media_inbound_loop(self) -> None:
        """Read inbound SRTP frames, unprotect, decode, forward to Gemini."""
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

                if self.codec_bridge is not None:
                    payload = rtp_packet[12:] if len(rtp_packet) > 12 else rtp_packet
                    pcm16 = self.codec_bridge.decode_to_pcm16_16k(payload)
                else:
                    pcm16 = rtp_packet

                # Forward decoded PCM16 to Gemini via internal queue
                if pcm16:
                    try:
                        self._gemini_in_queue.put_nowait(pcm16)
                    except asyncio.QueueFull:
                        pass
            except Exception:
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

    async def _media_outbound_loop(self) -> None:
        """Read AI response audio, encode, SRTP protect, send."""
        while not self._shutdown.is_set():
            try:
                pcm_frame = await asyncio.wait_for(
                    self.outbound_queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue

            try:
                self.frames_sent += 1

                # Codec encode → SRTP protect
                if self.codec_bridge is not None:
                    encoded = self.codec_bridge.encode_from_pcm16_24k(pcm_frame)
                else:
                    encoded = pcm_frame

                if self.srtp_sender is not None:
                    protected = self.srtp_sender.protect(encoded)
                else:
                    protected = encoded

                # Send via UDP transport
                if self.media_transport is not None and self.remote_media_addr is not None:
                    self.media_transport.sendto(protected, self.remote_media_addr)
            except Exception:
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
