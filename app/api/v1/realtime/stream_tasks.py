"""Streaming task implementations for realtime websocket sessions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from typing import Any

from fastapi import WebSocketDisconnect

from app.api.v1.realtime.models import SessionInitContext, SilenceState
from app.api.v1.realtime.runtime_cache import (
    bind_runtime_values,
    configure_runtime as configure_runtime_cache,
)
from app.tools.pii_redaction import redact_pii

logger = logging.getLogger(__name__)


def configure_runtime(**kwargs: Any) -> None:
    """Inject runtime dependencies from main module."""
    globals().update(kwargs)
    configure_runtime_cache(**kwargs)


def _reset_silence_nudge_schedule(now: float) -> tuple[float, float]:
    base_interval = max(1.0, float(SILENCE_NUDGE_SECONDS))
    return now + base_interval, base_interval


def _next_silence_nudge_interval(current_interval: float) -> float:
    multiplier = max(1.0, float(SILENCE_NUDGE_BACKOFF_MULTIPLIER))
    grown = max(current_interval + 1.0, current_interval * multiplier)
    max_interval = max(1.0, float(SILENCE_NUDGE_MAX_INTERVAL_SECONDS))
    return min(grown, max_interval)


def create_initial_silence_state() -> SilenceState:
    """Create initial silence-nudge state for a new websocket stream."""
    now = time.monotonic()
    due_at, interval = _reset_silence_nudge_schedule(now)
    return SilenceState(
        last_client_activity=now,
        silence_nudge_count=0,
        agent_busy=False,
        silence_nudge_due_at=due_at,
        silence_nudge_interval=interval,
    )


async def keepalive_task(websocket, session_alive: asyncio.Event) -> None:
    """Send periodic pings to detect dead connections and prevent proxy timeouts."""
    while session_alive.is_set():
        try:
            await asyncio.sleep(25)
            if not session_alive.is_set():
                break
            await websocket.send_text(json.dumps({
                "type": "ping",
                "ts": int(time.time() * 1000),
            }))
        except Exception:
            break  # WebSocket closed; stop keepalive


async def upstream_task(
    ctx: SessionInitContext,
    live_request_queue,
    session_alive: asyncio.Event,
    silence_state: SilenceState,
) -> None:
    """Receives from WebSocket, sends to LiveRequestQueue."""
    websocket = ctx.websocket
    (
        types_mod,
        check_rate_limit,
        upload_rate_limit,
        validate_upload_bytes,
        max_upload_bytes,
        cache_latest_image_fn,
        normalize_company_id_fn,
        append_canonical_lock_fields_fn,
        voice_for_industry_fn,
        build_session_started_message_fn,
    ) = bind_runtime_values(
        "types",
        "_check_rate_limit",
        "UPLOAD_RATE_LIMIT",
        "_validate_upload_bytes",
        "MAX_UPLOAD_BYTES",
        "cache_latest_image",
        "_normalize_company_id",
        "_append_canonical_lock_fields",
        "_voice_for_industry",
        "_build_session_started_message",
    )
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            raise WebSocketDisconnect(code=message.get("code", 1000))

        audio_data = message.get("bytes")
        text_data = message.get("text")

        # Binary frames: audio data
        if audio_data is not None:
            now = time.monotonic()
            silence_state.last_client_activity = now
            silence_state.silence_nudge_count = 0
            silence_state.agent_busy = True
            # New caller audio supersedes any pending response-latency watchdog.
            silence_state.awaiting_agent_response = False
            silence_state.response_nudge_count = 0
            (
                silence_state.silence_nudge_due_at,
                silence_state.silence_nudge_interval,
            ) = _reset_silence_nudge_schedule(now)
            audio_blob = types_mod.Blob(mime_type="audio/pcm;rate=16000", data=audio_data)
            live_request_queue.send_realtime(audio_blob)

        # Text frames: JSON messages
        elif text_data is not None:
            try:
                json_message = json.loads(text_data)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "code": "INVALID_JSON",
                            "message": "Malformed JSON payload",
                        }
                    )
                )
                continue

            # Any valid client JSON message counts as activity.
            msg_type = json_message.get("type", "")
            if msg_type in ("text", "image", "negotiate", "activity_start"):
                now = time.monotonic()
                silence_state.last_client_activity = now
                silence_state.silence_nudge_count = 0
                silence_state.awaiting_agent_response = False
                silence_state.response_nudge_count = 0
                if msg_type in ("text", "image", "negotiate"):
                    silence_state.agent_busy = True
                (
                    silence_state.silence_nudge_due_at,
                    silence_state.silence_nudge_interval,
                ) = _reset_silence_nudge_schedule(now)

            if msg_type == "text":
                content = types_mod.Content(parts=[types_mod.Part(text=json_message["text"])])
                live_request_queue.send_content(content)

            elif msg_type == "image":
                mime_type = json_message.get("mimeType", "image/jpeg")
                if not check_rate_limit(ctx.client_ip, "upload", upload_rate_limit):
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "Upload rate limit exceeded",
                    }))
                    continue

                image_b64 = json_message.get("data")
                if not isinstance(image_b64, str):
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "code": "INVALID_IMAGE_PAYLOAD",
                        "message": "Image payload must be base64 string",
                    }))
                    continue

                try:
                    image_data = base64.b64decode(image_b64, validate=True)
                except (binascii.Error, ValueError):
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "code": "INVALID_BASE64_IMAGE",
                        "message": "Image payload is not valid base64",
                    }))
                    continue

                try:
                    validate_upload_bytes(mime_type, image_data)
                except ValueError as exc:
                    code = str(exc)
                    if code == "UPLOAD_TOO_LARGE":
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "code": "UPLOAD_TOO_LARGE",
                            "message": f"Image exceeds {max_upload_bytes} bytes",
                        }))
                        continue
                    if code == "MIME_TYPE_NOT_ALLOWED":
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "code": "MIME_TYPE_NOT_ALLOWED",
                            "message": "Unsupported image MIME type",
                        }))
                        continue
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "code": "INVALID_UPLOAD",
                        "message": "Invalid upload payload",
                    }))
                    continue

                cache_latest_image_fn(
                    user_id=ctx.user_id,
                    session_id=ctx.resolved_session_id,
                    image_data=image_data,
                    mime_type=mime_type,
                )
                await websocket.send_text(json.dumps({
                    "type": "image_received",
                    "status": "analyzing",
                }))

                image_blob = types_mod.Blob(mime_type=mime_type, data=image_data)
                live_request_queue.send_realtime(image_blob)
                live_request_queue.send_content(
                    types_mod.Content(
                        parts=[
                            types_mod.Part(
                                text=(
                                    "Customer uploaded a device photo. "
                                    "Transfer to vision_agent and call "
                                    "analyze_device_image_tool now."
                                )
                            )
                        ]
                    )
                )

            elif msg_type == "config":
                requested_industry = json_message.get("industry", ctx.industry)
                if not isinstance(requested_industry, str):
                    requested_industry = ctx.industry
                requested_industry = requested_industry.strip().lower() or ctx.industry

                requested_company = normalize_company_id_fn(
                    json_message.get("companyId", json_message.get("company_id", ctx.company_id))
                )

                if requested_industry != ctx.session_industry:
                    await websocket.send_text(
                        json.dumps(
                            append_canonical_lock_fields_fn(
                                {
                                    "type": "error",
                                    "code": "INDUSTRY_LOCKED",
                                    "message": (
                                        "Industry is set during onboarding and cannot be changed "
                                        "during an active session."
                                    ),
                                    "industry": ctx.session_industry,
                                    "companyId": ctx.company_id,
                                    "requestedIndustry": requested_industry,
                                },
                                ctx.session_state,
                            )
                        )
                    )
                elif requested_company != ctx.company_id:
                    await websocket.send_text(
                        json.dumps(
                            append_canonical_lock_fields_fn(
                                {
                                    "type": "error",
                                    "code": "COMPANY_LOCKED",
                                    "message": (
                                        "Company profile is selected during onboarding and cannot "
                                        "be changed during an active session."
                                    ),
                                    "companyId": ctx.company_id,
                                    "requestedCompanyId": requested_company,
                                    "industry": ctx.session_industry,
                                },
                                ctx.session_state,
                            )
                        )
                    )
                else:
                    current_voice = (
                        ctx.session_state.get("app:voice")
                        if isinstance(ctx.session_state.get("app:voice"), str)
                        else None
                    ) or voice_for_industry_fn(ctx.session_industry)
                    await websocket.send_text(
                        json.dumps(
                            build_session_started_message_fn(
                                session_id=ctx.resolved_session_id,
                                industry=ctx.session_industry,
                                company_id=ctx.company_id,
                                voice=current_voice,
                                manual_vad_active=ctx.manual_vad_active,
                                session_state=ctx.session_state,
                            )
                        )
                    )

            elif msg_type == "negotiate":
                action = json_message.get("action", "counter")
                amount = json_message.get("counterOffer", 0)
                content = types_mod.Content(
                    parts=[
                        types_mod.Part(
                            text=f"Customer negotiation: {action}. Counter-offer amount: {amount}"
                        )
                    ]
                )
                live_request_queue.send_content(content)

            elif msg_type == "activity_start":
                if ctx.manual_vad_active and hasattr(live_request_queue, "send_activity_start"):
                    live_request_queue.send_activity_start()

            elif msg_type == "activity_end":
                if ctx.manual_vad_active and hasattr(live_request_queue, "send_activity_end"):
                    live_request_queue.send_activity_end()

            elif msg_type == "client_ping":
                client_ts = json_message.get("clientTs")
                seq = json_message.get("seq")
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "client_pong",
                            "seq": seq,
                            "clientTs": client_ts,
                            "serverTs": int(time.time() * 1000),
                        }
                    )
                )

            else:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "code": "UNSUPPORTED_MESSAGE_TYPE",
                    "message": "Unsupported client message type",
                }))


async def downstream_task(
    ctx: SessionInitContext,
    live_request_queue,
    session_alive: asyncio.Event,
    silence_state: SilenceState,
) -> None:
    """Receive events from run_live(), transform to server messages."""
    websocket = ctx.websocket
    (
        runner_obj,
        extract_server_message_from_state_delta_fn,
        usage_int_fn,
        token_price_prompt_per_million,
        token_price_completion_per_million,
        debug_telemetry,
        sanitize_log_fn,
    ) = bind_runtime_values(
        "runner",
        "_extract_server_message_from_state_delta",
        "_usage_int",
        "TOKEN_PRICE_PROMPT_PER_MILLION",
        "TOKEN_PRICE_COMPLETION_PER_MILLION",
        "DEBUG_TELEMETRY",
        "_sanitize_log",
    )
    current_agent = "ekaette_router"
    last_input_text = ""
    last_output_text = ""
    receiving_input = False
    input_finalized = False   # late-partial suppression
    output_finalized = False  # late-partial suppression
    last_structured_message_id = 0
    session_prompt_tokens = 0
    session_completion_tokens = 0
    session_total_tokens = 0
    session_cost_usd = 0.0

    async def _finalize_input() -> None:
        """Send a non-partial input transcription to close the user's turn."""
        nonlocal last_input_text, receiving_input, input_finalized
        if receiving_input and last_input_text:
            await websocket.send_text(json.dumps({
                "type": "transcription",
                "role": "user",
                "text": redact_pii(last_input_text),
                "partial": False,
            }))
        last_input_text = ""
        receiving_input = False
        input_finalized = True

    async def _finalize_output() -> None:
        """Send a non-partial output transcription to close the agent's turn."""
        nonlocal last_output_text, output_finalized
        if last_output_text:
            await websocket.send_text(json.dumps({
                "type": "transcription",
                "role": "agent",
                "text": last_output_text,
                "partial": False,
            }))
        last_output_text = ""
        output_finalized = True

    async for event in runner_obj.run_live(
        user_id=ctx.user_id,
        session_id=ctx.resolved_session_id,
        live_request_queue=live_request_queue,
        run_config=ctx.run_config,
    ):
        try:
            # Audio + Text content
            if event.content and event.content.parts:
                for part in event.content.parts:
                    # Audio -> binary WebSocket frame (lowest latency)
                    if (
                        part.inline_data
                        and part.inline_data.data
                        and part.inline_data.mime_type
                        and "audio" in part.inline_data.mime_type
                    ):
                        silence_state.agent_busy = True
                        silence_state.awaiting_agent_response = False
                        audio_bytes = part.inline_data.data
                        if isinstance(audio_bytes, str):
                            audio_bytes = base64.b64decode(audio_bytes)
                        await websocket.send_bytes(audio_bytes)

                    # Text -> transcription (text-mode fallback only)
                    elif part.text and not ctx.is_native_audio:
                        silence_state.agent_busy = True
                        silence_state.awaiting_agent_response = False
                        await websocket.send_text(json.dumps({
                            "type": "transcription",
                            "role": "agent",
                            "text": part.text,
                            "partial": not bool(event.turn_complete),
                        }))

            # Input transcription (user's speech -> text)
            if event.input_transcription:
                text = getattr(event.input_transcription, "text", None)
                finished = getattr(event.input_transcription, "finished", False)
                if text:
                    if input_finalized and not finished:
                        # Suppress late partials after input was already finalized
                        pass
                    else:
                        if input_finalized:
                            # New final after prior finalization -> new utterance
                            input_finalized = False
                        last_input_text = text
                        receiving_input = True
                        is_partial = not finished
                        await websocket.send_text(json.dumps({
                            "type": "transcription",
                            "role": "user",
                            "text": redact_pii(text),
                            "partial": is_partial,
                        }))
                        if finished:
                            last_input_text = ""
                            receiving_input = False
                            input_finalized = True

                        # Arm/reset response latency watchdog
                        silence_state.awaiting_agent_response = True
                        silence_state.user_spoke_at = time.monotonic()
                        silence_state.response_nudge_count = 0

            # Output transcription (agent's speech -> text)
            if event.output_transcription:
                text = getattr(event.output_transcription, "text", None)
                finished = getattr(event.output_transcription, "finished", False)
                if text:
                    silence_state.agent_busy = True
                    if output_finalized and not finished:
                        # Suppress late partials after output was already finalized
                        pass
                    else:
                        silence_state.awaiting_agent_response = False
                        if output_finalized:
                            output_finalized = False
                        # Agent started responding -> finalize user's input
                        if receiving_input:
                            await _finalize_input()
                        last_output_text = text
                        is_partial = not finished
                        await websocket.send_text(json.dumps({
                            "type": "transcription",
                            "role": "agent",
                            "text": text,
                            "partial": is_partial,
                        }))
                        if finished:
                            last_output_text = ""
                            output_finalized = True

            # Interrupted -> finalize + clear playback
            if event.interrupted:
                await _finalize_input()
                await _finalize_output()
                silence_state.agent_busy = False
                silence_state.awaiting_agent_response = False
                await websocket.send_text(json.dumps({
                    "type": "interrupted",
                    "interrupted": True,
                }))

            # Agent transfer
            if event.actions and event.actions.transfer_to_agent:
                new_agent = event.actions.transfer_to_agent
                if not isinstance(new_agent, str) or not new_agent.strip():
                    logger.debug("Ignoring invalid transfer target: %r", new_agent)
                elif new_agent == current_agent:
                    logger.debug("Suppressing no-op agent_transfer (already on %s)", new_agent)
                else:
                    await websocket.send_text(json.dumps({
                        "type": "agent_transfer",
                        "from": current_agent,
                        "to": new_agent,
                    }))
                    current_agent = new_agent
                    await websocket.send_text(json.dumps({
                        "type": "agent_status",
                        "agent": new_agent,
                        "status": "active",
                    }))

            # Structured ServerMessages from callbacks/state delta
            state_delta = event.actions.state_delta if event.actions else None
            structured = extract_server_message_from_state_delta_fn(state_delta)
            if structured:
                raw_id = structured.get("id", 0)
                try:
                    structured_id = int(raw_id)
                except (TypeError, ValueError):
                    structured_id = 0

                if structured_id > last_structured_message_id:
                    payload = {k: v for k, v in structured.items() if k != "id"}
                    await websocket.send_text(json.dumps(payload))
                    last_structured_message_id = structured_id

            # Turn complete -> finalize output + status
            if event.turn_complete:
                await _finalize_input()
                await _finalize_output()
                # Anchor silence nudges to when the agent actually finishes,
                # not when the user last spoke. This avoids check-in nudges
                # racing right after a long agent response.
                now = time.monotonic()
                silence_state.agent_busy = False
                if now >= silence_state.last_client_activity:
                    silence_state.silence_nudge_due_at = now + max(
                        1.0, float(silence_state.silence_nudge_interval)
                    )
                # Reset suppression flags for the next turn
                input_finalized = False
                output_finalized = False
                await websocket.send_text(json.dumps({
                    "type": "agent_status",
                    "agent": event.author or current_agent,
                    "status": "idle",
                }))

            # Usage metadata
            if event.usage_metadata:
                logger.debug("Token usage: %s", event.usage_metadata)
                prompt_tokens = usage_int_fn(
                    event.usage_metadata, "prompt_token_count", "prompt_tokens"
                )
                completion_tokens = usage_int_fn(
                    event.usage_metadata,
                    "candidates_token_count",
                    "completion_token_count",
                    "completion_tokens",
                )
                total_tokens = usage_int_fn(
                    event.usage_metadata, "total_token_count", "total_tokens"
                )
                if total_tokens <= 0:
                    total_tokens = prompt_tokens + completion_tokens

                if total_tokens > 0:
                    session_prompt_tokens += prompt_tokens
                    session_completion_tokens += completion_tokens
                    session_total_tokens += total_tokens
                    session_cost_usd += (
                        (prompt_tokens / 1_000_000) * token_price_prompt_per_million
                        + (completion_tokens / 1_000_000) * token_price_completion_per_million
                    )

                    if debug_telemetry:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "telemetry",
                                    "promptTokens": prompt_tokens,
                                    "completionTokens": completion_tokens,
                                    "totalTokens": total_tokens,
                                    "sessionPromptTokens": session_prompt_tokens,
                                    "sessionCompletionTokens": session_completion_tokens,
                                    "sessionTotalTokens": session_total_tokens,
                                    "sessionCostUsd": round(session_cost_usd, 6),
                                }
                            )
                        )

            # Session resumption token
            if event.live_session_resumption_update:
                logger.debug("Session resumption token received")
                token_val = getattr(event.live_session_resumption_update, "token", None)
                if isinstance(token_val, str) and token_val:
                    await websocket.send_text(json.dumps({
                        "type": "session_ending",
                        "reason": "session_resumption",
                        "resumptionToken": token_val,
                    }))

            # GoAway
            go_away = getattr(event, "go_away", None)
            if go_away is not None:
                time_left = getattr(go_away, "time_left", None)
                logger.warning("GoAway received, timeLeft=%s", time_left)
                await websocket.send_text(json.dumps({
                    "type": "session_ending",
                    "reason": "go_away",
                    "timeLeftMs": int(time_left.total_seconds() * 1000)
                    if time_left is not None
                    else None,
                }))

        except Exception as e:
            logger.error("Error processing downstream event: %s", e, exc_info=True)

    # Live API session ended naturally (timeout / GoAway completion).
    # Notify client so it can decide to reconnect gracefully.
    logger.info(
        "downstream_task: run_live loop ended for session %s",
        sanitize_log_fn(ctx.resolved_session_id),
    )
    try:
        await websocket.send_text(json.dumps({
            "type": "session_ending",
            "reason": "live_session_ended",
        }))
    except Exception:
        pass  # Client already disconnected; safe to ignore


async def silence_nudge_task(live_request_queue, session_alive: asyncio.Event, silence_state: SilenceState) -> None:
    """Nudge the model when the customer has been silent too long."""
    (types_mod,) = bind_runtime_values("types")
    if SILENCE_NUDGE_SECONDS <= 0:
        return  # disabled
    while session_alive.is_set():
        await asyncio.sleep(1)
        if not session_alive.is_set():
            break
        now = time.monotonic()

        # ── Fast-path: agent response latency (3s / 15s) ──
        # NOT gated on agent_busy — upstream sets that on user audio
        # frames and never clears it during router thinking time.
        if silence_state.awaiting_agent_response:
            elapsed = now - silence_state.user_spoke_at
            if elapsed >= 3.0 and silence_state.response_nudge_count == 0:
                silence_state.response_nudge_count = 1
                try:
                    live_request_queue.send_content(types_mod.Content(parts=[
                        types_mod.Part(text=(
                            "[System: The customer said something several seconds ago "
                            "and is waiting for a response on a phone call. Silence on "
                            "a phone call feels like a dropped connection. Say a brief "
                            "filler phrase NOW, like 'Let me check that for you', then "
                            "proceed with your task.]"
                        ))
                    ]))
                except Exception:
                    break
                continue
            if elapsed >= 15.0 and silence_state.response_nudge_count == 1:
                silence_state.response_nudge_count = 2
                try:
                    live_request_queue.send_content(types_mod.Content(parts=[
                        types_mod.Part(text=(
                            "[System: Over 15 seconds have passed since the customer "
                            "spoke. Say 'I'm still with you, just a moment longer' "
                            "to reassure them you haven't disconnected.]"
                        ))
                    ]))
                except Exception:
                    break
                continue

        if now < silence_state.silence_nudge_due_at:
            continue
        if silence_state.agent_busy:
            continue
        # Skip customer-silence nudge when agent is slow to respond
        # (the response-latency fast-path handles this case with the right message)
        if silence_state.awaiting_agent_response:
            continue
        if silence_state.silence_nudge_count >= SILENCE_NUDGE_MAX:
            continue
        silence_state.silence_nudge_count += 1
        ordinal = silence_state.silence_nudge_count
        if ordinal == 1:
            hint = (
                "[System: the customer has been silent for several seconds. "
                "Gently check if they are still there. Keep it brief and natural.]"
            )
        else:
            hint = (
                "[System: the customer is still silent after your last check-in. "
                "Say something like 'I'll be right here whenever you're ready' "
                "and then wait quietly. Do not check in again.]"
            )
        try:
            live_request_queue.send_content(
                types_mod.Content(parts=[types_mod.Part(text=hint)])
            )
        except Exception:
            break  # queue closed
        silence_state.silence_nudge_interval = _next_silence_nudge_interval(
            silence_state.silence_nudge_interval
        )
        silence_state.silence_nudge_due_at = now + silence_state.silence_nudge_interval
