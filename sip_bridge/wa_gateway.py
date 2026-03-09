"""Gateway mode loops for WhatsApp SIP bridge.

Extracted from wa_session.py to keep file sizes within architecture caps.
These functions bridge audio between the codec pipeline and Cloud Run WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .wa_session import WaSession

logger = logging.getLogger(__name__)


async def gateway_bidi_loop(session: WaSession) -> None:
    """Bridge audio between codec pipeline and Cloud Run WebSocket.

    Reconnects on WebSocket disconnect unless shutdown is signalled
    or ``live_session_ended`` was received.
    """
    max_retries = 5
    retry_delay = 1.0
    for attempt in range(max_retries + 1):
        if session._shutdown.is_set():
            return
        if attempt > 0:
            delay = min(retry_delay * (2 ** (attempt - 1)), 5.0)
            logger.info("Gateway reconnect attempt %d in %.1fs", attempt, delay)
            await asyncio.sleep(delay)
            if session._shutdown.is_set():
                return
            try:
                await session.gateway_client.reconnect()
            except Exception:
                logger.warning("Gateway reconnect failed", exc_info=True)
                continue

        send_task = asyncio.create_task(_gateway_send_loop(session))
        recv_task = asyncio.create_task(_gateway_recv_loop(session))
        task_errors: list[Exception] = []
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
                    task_errors.append(exc)
                    logger.warning("Gateway loop task failed", exc_info=True)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            for t in (send_task, recv_task):
                if not t.done():
                    t.cancel()

        if session._shutdown.is_set():
            return
        if task_errors:
            logger.warning("Gateway WebSocket disconnected after task errors: %s", task_errors)
        else:
            logger.warning("Gateway WebSocket disconnected, will retry")

    # Retries exhausted -- tear down the call
    logger.error("Gateway reconnect retries exhausted, shutting down call")
    session._shutdown.set()


async def _gateway_send_loop(session: WaSession) -> None:
    """Read PCM16 from inbound pipeline, send to Cloud Run.

    In gateway mode the first greeting is non-interruptible, so we mute caller
    audio only while that greeting lock is active. After the greeting finishes,
    we keep streaming real caller audio upstream so Cloud Run/Gemini can detect
    interruption and barge-in for later speech.
    """
    from .wa_session import SILENCE_FRAME

    while not session._shutdown.is_set():
        try:
            pcm16 = await asyncio.wait_for(
                session._gemini_in_queue.get(), timeout=1.0
            )
        except TimeoutError:
            continue
        gateway_client = session.gateway_client
        if gateway_client is None:
            await asyncio.sleep(0.05)
            continue
        session._gateway_send_frames += 1
        if session._gateway_send_frames <= 3:
            logger.info(
                "Gateway send frame=%d muted=%s bytes=%d call_id=%s",
                session._gateway_send_frames,
                session._greeting_lock_active,
                len(pcm16),
                session.call_id,
            )
        await gateway_client.send_audio(
            SILENCE_FRAME if session._greeting_lock_active else pcm16
        )


async def _gateway_recv_loop(session: WaSession) -> None:
    """Receive from Cloud Run, route audio to outbound, handle JSON protocol."""
    from .wa_session import MODEL_OUTPUT_CHANNELS, downmix_pcm16_to_mono

    if session.gateway_client is None:
        return
    async for frame in session.gateway_client.receive():
        if session._shutdown.is_set():
            break
        if frame.is_audio:
            session._model_speaking = True
            session._last_model_audio_at = time.time()
            session._gateway_audio_frames_received += 1
            input_audio = frame.audio_data
            session._gateway_audio_bytes_received += len(input_audio)
            output_audio = input_audio
            if MODEL_OUTPUT_CHANNELS > 1:
                output_audio = downmix_pcm16_to_mono(input_audio, MODEL_OUTPUT_CHANNELS)
            if session._gateway_audio_frames_received <= 5:
                logger.info(
                    "Gateway audio frame=%d bytes=%d total_bytes=%d call_id=%s",
                    session._gateway_audio_frames_received,
                    len(input_audio),
                    session._gateway_audio_bytes_received,
                    session.call_id,
                )
                if MODEL_OUTPUT_CHANNELS > 1:
                    logger.info(
                        "Gateway audio downmix channels=%d input_bytes=%d mono_bytes=%d call_id=%s",
                        MODEL_OUTPUT_CHANNELS,
                        len(input_audio),
                        len(output_audio),
                        session.call_id,
                    )
            if not output_audio:
                continue
            try:
                session.outbound_queue.put_nowait(output_audio)
            except asyncio.QueueFull:
                session.outbound_drops += 1
        else:
            await _handle_gateway_json(session, frame)


async def _handle_gateway_json(session: WaSession, frame) -> None:
    """Dispatch a JSON protocol frame from the gateway."""
    try:
        msg = json.loads(frame.text_data)
    except json.JSONDecodeError:
        session_ref = ""
        if session.gateway_client is not None:
            session_ref = (
                session.gateway_client.canonical_session_id
                or session.gateway_client.session_id
            )
        logger.warning(
            "Ignoring malformed gateway JSON call_id=%s session_id=%s payload=%r",
            session.call_id,
            session_ref,
            frame.text_data[:200],
            exc_info=True,
        )
        return
    msg_type = msg.get("type", "")
    if msg_type == "session_started":
        await _on_session_started(session, msg)
    elif msg_type == "session_ending":
        reason = msg.get("reason", "")
        logger.info("Gateway session ending: reason=%s", reason)
        if reason == "live_session_ended":
            session._shutdown.set()
        elif reason == "session_resumption":
            if session.gateway_client is not None:
                session.gateway_client.remember_resumption_token(
                    msg.get("resumptionToken", "")
                )
    elif msg_type == "ping":
        pass
    elif msg_type == "interrupted":
        logger.info("Gateway interrupted call_id=%s", session.call_id)
        session._model_speaking = False
        session._greeting_lock_active = False
        session._model_speech_end_time = time.time()
        session._clear_outbound_audio()
    elif msg_type == "agent_status":
        logger.info(
            "Gateway agent_status=%s call_id=%s",
            msg.get("status", ""),
            session.call_id,
        )
        if msg.get("status") == "idle":
            session._model_speaking = False
            session._greeting_lock_active = False
            session._model_speech_end_time = time.time()
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
                session.call_id,
                msg.get("text", "")[:100],
            )
        is_final_agent_transcript = (
            msg.get("role") == "agent" and not msg.get("partial")
        )
        if is_final_agent_transcript:
            logger.info(
                "Gateway agent transcription final call_id=%s text=%s",
                session.call_id,
                msg.get("text", "")[:100],
            )
            session._model_speech_end_time = time.time()
            session._model_speaking = False


async def _on_session_started(session: WaSession, msg: dict) -> None:
    """Handle gateway session_started message."""
    canonical_id = msg.get("sessionId", "")
    if canonical_id and session.gateway_client is not None:
        session.gateway_client.remember_canonical_session_id(canonical_id)
    logger.info("Gateway session started: %s", canonical_id)
    if not session._gateway_greeting_sent:
        session._gateway_greeting_sent = True
        session._greeting_lock_active = True
        try:
            await session.gateway_client.send_text(json.dumps({
                "type": "text",
                "text": "[Call connected]",
            }))
        except Exception:
            session._model_speaking = False
            session._greeting_lock_active = False
            logger.warning(
                "Failed to send virtual assistant greeting",
                exc_info=True,
            )
