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
import logging
import math
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from google import genai
from google.genai import types as genai_types

from .audio_codec import alaw_to_pcm16, resample_8k_to_16k
from .rtp import PCMA_PAYLOAD_TYPE, PCMU_PAYLOAD_TYPE, RTPPacket, RTPTimer

if TYPE_CHECKING:
    from .codec_bridge import CodecBridge

logger = logging.getLogger(__name__)

# Bounded queue sizes (backpressure)
INBOUND_QUEUE_SIZE = 500  # ~10s of 20ms frames (match wa_session.py)
OUTBOUND_QUEUE_SIZE = 10000

# 20ms of silence at 16kHz 16-bit mono (640 bytes)
SILENCE_FRAME = b"\x00" * 640

# Echo suppression holdoff after model stops speaking.
# Keep short (0.5s) to avoid muting start of user's next utterance.
ECHO_HOLDOFF_SEC = 0.5


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
    media_transport: Any = None

    # Gemini Live config
    gemini_api_key: str = ""
    gemini_model_id: str = ""
    gemini_system_instruction: str = ""
    gemini_voice: str = "Aoede"
    gemini_session: Any = None

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

    # Metrics
    frames_received: int = 0
    frames_sent: int = 0
    inbound_drops: int = 0
    outbound_drops: int = 0

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
            self.media_transport = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.media_transport.setblocking(False)
            bind_addr = os.getenv("RTP_BIND_ADDRESS", "0.0.0.0")  # All interfaces for RTP media
            self.media_transport.bind((bind_addr, self.local_rtp_port))
            self._owns_transport = True
            logger.info("RTP socket bound", extra={"port": self.local_rtp_port})

        # Connect to Gemini Live
        gemini_ctx = None
        if self.gemini_session is None and self.gemini_api_key:
            try:
                sys_instruct = self.gemini_system_instruction or (
                    "You are an AI customer service assistant named Ekaitay. "
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
                        parts=[genai_types.Part(text="[Phone call connected]")],
                    ),
                    turn_complete=True,
                )
                logger.info("Greeting trigger sent via send_client_content")

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
                logger.error("Call session task failed", exc_info=exc)
        finally:
            # Clean up Gemini session
            if gemini_ctx is not None:
                try:
                    await gemini_ctx.__aexit__(None, None, None)
                except Exception:
                    pass  # Best-effort cleanup — session may already be closed

            # Clean up UDP socket
            if self._owns_transport and self.media_transport is not None:
                try:
                    self.media_transport.close()
                except Exception:
                    pass  # Best-effort socket cleanup

                self.media_transport = None

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
                },
            )

    def shutdown(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown.set()

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
                    gain = int(os.getenv("SIP_AUDIO_GAIN", "4"))
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
                        pass  # Drop frame — Gemini consumer is behind
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

    async def _media_outbound_loop(self) -> None:
        """Encode PCM16 24kHz → G.711, split into 160-byte RTP frames, send."""
        ssrc = int.from_bytes(os.urandom(4), "big")
        seq = 0
        timestamp = 0
        timer: RTPTimer | None = None
        g711_buffer = bytearray()
        target_logged = False

        FRAME_SIZE = 160  # 160 bytes = 20ms at 8kHz

        while not self._shutdown.is_set():
            if len(g711_buffer) < FRAME_SIZE:
                try:
                    pcm_frame = await asyncio.wait_for(
                        self.outbound_queue.get(), timeout=1.0
                    )
                except TimeoutError:
                    continue

                try:
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
                pkt = RTPPacket(
                    version=2,
                    payload_type=PCMU_PAYLOAD_TYPE,
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
