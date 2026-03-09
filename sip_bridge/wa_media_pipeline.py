"""Media pipeline loops for WhatsApp SIP bridge.

Extracted from wa_session.py to keep file sizes within architecture caps.
Handles UDP/RTP/SRTP receive, decode, and encode/send loops.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .wa_session import WaSession

logger = logging.getLogger(__name__)


async def media_recv_loop(session: WaSession) -> None:
    """Read SRTP packets from UDP socket and feed into inbound queue."""
    from .rtp import is_rtcp_packet

    loop = asyncio.get_running_loop()
    use_symmetric_rtp = (
        os.getenv("WA_SIP_SYMMETRIC_RTP", "")
        or os.getenv("SIP_SYMMETRIC_RTP", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    first_inbound_logged = False
    if session.media_transport is None:
        # No transport -- idle until shutdown (test/sandbox mode)
        while not session._shutdown.is_set():
            await asyncio.sleep(0.02)
        return

    while not session._shutdown.is_set():
        try:
            data, addr = await asyncio.wait_for(
                loop.sock_recvfrom(session.media_transport, 2048),
                timeout=1.0,
            )
            if not first_inbound_logged:
                first_inbound_logged = True
                logger.info(
                    "WA RTP inbound first source=%s configured_remote=%s symmetric=%s",
                    addr,
                    session.remote_media_addr,
                    use_symmetric_rtp,
                )
                if (
                    session.remote_media_addr is not None
                    and session.remote_media_addr != addr
                    and not use_symmetric_rtp
                ):
                    logger.warning(
                        "WA RTP source differs from SDP remote (source=%s, sdp=%s). "
                        "If caller audio is missing, enable WA_SIP_SYMMETRIC_RTP=1.",
                        addr,
                        session.remote_media_addr,
                    )
            if session.remote_media_addr is None:
                session.remote_media_addr = (addr[0], addr[1])
                logger.info("WA RTP remote set from first inbound source remote=%s", session.remote_media_addr)
            elif use_symmetric_rtp and session.remote_media_addr != addr:
                logger.info(
                    "WA RTP remote updated by symmetric latching old=%s new=%s",
                    session.remote_media_addr,
                    addr,
                )
                session.remote_media_addr = (addr[0], addr[1])
            elif session.remote_media_addr != addr:
                continue
            # Filter out non-RTP packets (STUN, DTLS) per RFC 5764 S5.1.2
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
            await session.feed_inbound(data)
        except TimeoutError:
            continue
        except OSError:
            # Socket closed or error -- exit loop
            break


async def media_inbound_loop(session: WaSession) -> None:
    """Read inbound SRTP frames, unprotect, decode, forward to Gemini."""
    from .rtp import RTPPacket

    decode_count = 0
    pcm_log_count = 0
    decode_error_count = 0
    while not session._shutdown.is_set():
        try:
            frame = await asyncio.wait_for(
                session.inbound_queue.get(), timeout=1.0
            )
        except TimeoutError:
            continue

        try:
            # SRTP unprotect -> codec decode -> PCM16
            if session.srtp_receiver is not None:
                rtp_packet = session.srtp_receiver.unprotect(frame)
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

            if session.codec_bridge is not None:
                expected_payload_type = getattr(session.codec_bridge, "rtp_payload_type", None)
                if isinstance(expected_payload_type, int) and rtp.payload_type != expected_payload_type:
                    continue
                payload = rtp.payload
                pcm16 = session.codec_bridge.decode_to_pcm16_16k(payload)
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
                        session.call_id,
                    )
                try:
                    session._gemini_in_queue.put_nowait(pcm16)
                except asyncio.QueueFull:
                    # Drop frame when backpressure queue is saturated.
                    pass
        except Exception:
            decode_error_count += 1
            if decode_error_count <= 3:
                logger.warning(
                    "Inbound frame processing error call_id=%s",
                    session.call_id,
                    exc_info=True,
                )
            else:
                logger.debug("Inbound frame processing error", exc_info=True)


async def media_outbound_loop(session: WaSession) -> None:
    """Read AI response audio, encode, SRTP protect, send."""
    from .rtp import RTPPacket, RTPTimer
    from .wa_session import MODEL_OUTPUT_SAMPLE_RATE, compute_rtp_timestamp_increment

    ssrc = session.rtp_ssrc
    # seq/timestamp read lazily on first send so the silence keepalive
    # can advance them in the meantime.
    seq: int | None = None
    timestamp: int | None = None
    target_logged = False
    timer: RTPTimer | None = None
    pcm_buffer = bytearray()
    payload_type = getattr(session.codec_bridge, "rtp_payload_type", 111)
    if not isinstance(payload_type, int):
        payload_type = 111
    clock_rate = getattr(session.codec_bridge, "rtp_clock_rate", 48000)
    if not isinstance(clock_rate, int):
        clock_rate = 48000
    frame_duration_ms = getattr(session.codec_bridge, "frame_duration_ms", 20)
    if not isinstance(frame_duration_ms, int):
        frame_duration_ms = 20
    timestamp_step = compute_rtp_timestamp_increment(clock_rate, frame_duration_ms)
    pcm_frame_bytes = MODEL_OUTPUT_SAMPLE_RATE * frame_duration_ms // 1000 * 2

    while not session._shutdown.is_set():
        if len(pcm_buffer) < pcm_frame_bytes:
            try:
                pcm_chunk = await asyncio.wait_for(
                    session.outbound_queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue
            pcm_buffer.extend(pcm_chunk)

        while len(pcm_buffer) >= pcm_frame_bytes and not session._shutdown.is_set():
            if timer is None:
                timer = RTPTimer()

            pcm_frame = bytes(pcm_buffer[:pcm_frame_bytes])
            del pcm_buffer[:pcm_frame_bytes]

            try:
                # Lazy init: pick up RTP state from keepalive
                if seq is None:
                    seq = session.rtp_sequence
                    timestamp = session.rtp_timestamp

                session.frames_sent += 1
                if session.codec_bridge is not None:
                    encoded = session.codec_bridge.encode_from_pcm16_24k(pcm_frame)
                else:
                    encoded = pcm_frame

                marker = session.frames_sent == 1
                rtp = RTPPacket(
                    version=2,
                    payload_type=payload_type,
                    sequence=seq & 0xFFFF,
                    timestamp=timestamp & 0xFFFFFFFF,
                    ssrc=ssrc,
                    marker=marker,
                    payload=encoded,
                ).serialize()

                if session.srtp_sender is not None:
                    protected = session.srtp_sender.protect(rtp)
                else:
                    protected = rtp

                if session.media_transport is not None and session.remote_media_addr is not None:
                    if not target_logged:
                        local_port = "?"
                        try:
                            local_port = session.media_transport.getsockname()[1]
                        except Exception:
                            pass
                        logger.info(
                            "WA RTP outbound target local_port=%s remote=%s ssrc=%08x seq=%d timestamp=%d",
                            local_port,
                            session.remote_media_addr,
                            ssrc,
                            seq,
                            timestamp,
                        )
                        target_logged = True
                    session.media_transport.sendto(protected, session.remote_media_addr)
                if session.frames_sent % 50 == 1:
                    logger.info(
                        "WA RTP frames sent count=%d call_id=%s",
                        session.frames_sent,
                        session.call_id,
                    )
                if (
                    not session._no_inbound_warned
                    and session.frames_sent >= 100
                    and session.frames_received == 0
                ):
                    session._no_inbound_warned = True
                    logger.warning(
                        "No inbound WA RTP received while outbound audio is active. "
                        "Verify WA_SIP_PUBLIC_IP, UDP firewall rules, and consider WA_SIP_SYMMETRIC_RTP=1.",
                    )
                seq += 1
                timestamp += timestamp_step
                session.rtp_sequence = seq
                session.rtp_timestamp = timestamp
                await timer.wait_next_frame()
            except Exception:
                if session.frames_sent <= 5:
                    logger.warning("Outbound frame processing error", exc_info=True)
                else:
                    logger.debug("Outbound frame processing error", exc_info=True)
