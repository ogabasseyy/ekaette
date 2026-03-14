"""Streaming task implementations for realtime websocket sessions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import errno
import json
import logging
import os
import time
from collections import deque
from types import SimpleNamespace
from typing import Any

from fastapi import WebSocketDisconnect

from app.api.v1.realtime.models import SessionInitContext, SilenceState
from app.api.v1.realtime.runtime_cache import (
    bind_runtime_values,
    configure_runtime as configure_runtime_cache,
)
from app.api.v1.realtime.voice_state_registry import (
    VOICE_STATE_BOOL_KEYS,
    VOICE_STATE_INT_KEYS,
    VOICE_STATE_KEYS,
    VOICE_STATE_STR_KEYS,
    get_registered_voice_state,
    update_voice_state,
)
from app.tools.pii_redaction import redact_pii

logger = logging.getLogger(__name__)

# 20ms of silence at 16kHz mono PCM16 (640 bytes) — matches SIP/WA bridge pattern
_SILENCE_FRAME = b"\x00" * 640


def _text_overlap(a: str, b: str) -> float:
    """Return 0.0–1.0 word-level overlap ratio between two strings.

    Used for output-level dedup (ADK #3395) — detects near-duplicate
    finished transcriptions arriving within seconds of each other.
    """
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    intersection = len(wa & wb)
    return intersection / max(len(wa), len(wb))

def _parse_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _parse_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


RESPONSE_LATENCY_FILLER_SECONDS = max(
    0.5,
    _parse_float_env("RESPONSE_LATENCY_FILLER_SECONDS", 2.0),
)
RESPONSE_LATENCY_REASSURE_SECONDS = max(
    RESPONSE_LATENCY_FILLER_SECONDS + 1.0,
    _parse_float_env("RESPONSE_LATENCY_REASSURE_SECONDS", 5.0),
)
LIVE_STREAM_MAX_RETRIES = max(0, _parse_int_env("LIVE_STREAM_MAX_RETRIES", 2))
LIVE_STREAM_RETRY_BASE_SECONDS = max(
    0.1,
    _parse_float_env("LIVE_STREAM_RETRY_BASE_SECONDS", 0.5),
)
OPENING_DUPLICATE_SUPPRESSION_SECONDS = max(
    1.0,
    _parse_float_env("OPENING_DUPLICATE_SUPPRESSION_SECONDS", 3.0),
)
VOICE_SERVER_OWNED_OPENING_ENABLED = _env_flag(
    "VOICE_SERVER_OWNED_OPENING_ENABLED",
    False,
)
_VOICE_OPENING_CONNECT_MARKERS = frozenset({
    "[phone call connected]",
    "[call connected]",
    "phone call connected",
    "call connected",
})
_CONNECTION_ERRNOS = frozenset({
    errno.EPIPE,
    errno.ECONNABORTED,
    errno.ECONNRESET,
    errno.ENOTCONN,
    errno.ETIMEDOUT,
    errno.ESHUTDOWN,
})
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


def _is_retryable_live_error(exc: Exception) -> bool:
    """Classify transient provider/live-stream failures that merit a retry."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = f"{current.__class__.__name__}: {current}".lower()
        if (
            "service is currently unavailable" in message
            or "received 1011" in message
            or "then sent 1011" in message
        ):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


def _is_connection_error(exc: Exception) -> bool:
    if isinstance(exc, (WebSocketDisconnect, ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, "errno", None) in _CONNECTION_ERRNOS
    return False


def _looks_like_tradein_request(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return False
    try:
        from app.agents.callbacks import (
            _looks_like_explicit_device_swap_request,
            _looks_like_tradein_or_upgrade_request,
        )

        return (
            _looks_like_explicit_device_swap_request(normalized)
            or _looks_like_tradein_or_upgrade_request(normalized)
        )
    except Exception:
        lowered = normalized.lower()
        return any(
            token in lowered
            for token in ("swap", "trade in", "trade-in", "upgrade")
        )


def _normalize_opening_transport_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _session_flag_true(state: Any, key: str) -> bool:
    getter = getattr(state, "get", None)
    if not callable(getter):
        return False
    try:
        return bool(getter(key, False))
    except TypeError:
        return bool(getter(key))


def _is_live_voice_session(ctx: SessionInitContext) -> bool:
    session_state = getattr(ctx, "session_state", None)
    channel_getter = getattr(session_state, "get", None)
    channel = ""
    if callable(channel_getter):
        try:
            channel = str(channel_getter("app:channel", "") or "").strip().lower()
        except TypeError:
            channel = str(channel_getter("app:channel") or "").strip().lower()
    return channel == "voice"


def _should_swallow_opening_transport_metadata(
    ctx: SessionInitContext,
    *,
    msg_type: str,
    raw_text: str,
) -> bool:
    if not VOICE_SERVER_OWNED_OPENING_ENABLED:
        return False
    if msg_type not in {"text", "system_text"}:
        return False
    if not _is_live_voice_session(ctx):
        return False
    session_state = getattr(ctx, "session_state", None)
    if _session_flag_true(session_state, "temp:first_user_turn_started"):
        return False
    if _session_flag_true(session_state, "temp:first_user_turn_complete"):
        return False
    return _normalize_opening_transport_text(raw_text) in _VOICE_OPENING_CONNECT_MARKERS


def _response_latency_prompt(
    *,
    ctx: SessionInitContext | None,
    silence_state: SilenceState,
) -> str:
    session_state = getattr(ctx, "session_state", None)
    current_agent = ""
    latest_user = ""
    recent_customer = ""
    if isinstance(session_state, dict):
        current_agent = str(session_state.get("temp:active_agent", "") or "").strip()
        latest_user = str(session_state.get("temp:last_user_turn", "") or "").strip()
        recent_customer = str(session_state.get("temp:recent_customer_context", "") or "").strip()

    combined = " ".join(part for part in (latest_user, recent_customer) if part).strip()
    if (current_agent or "ekaette_router") == "ekaette_router" and _looks_like_tradein_request(combined):
        explicit_swap_pair = False
        try:
            from app.agents.callbacks import _looks_like_explicit_device_swap_request

            explicit_swap_pair = _looks_like_explicit_device_swap_request(combined)
        except Exception:
            explicit_swap_pair = False
        if explicit_swap_pair:
            return (
                "[System: The customer already stated both the phone they have and the phone "
                "they want in a live swap request and is waiting in silence. Respond right now. "
                "If you are still ekaette_router, transfer immediately to valuation_agent. "
                "Do not greet again. Do not ask brand-new, pre-owned, storage, colour, price, "
                "availability, delivery, or payment questions before the valuation handoff. "
                "Do not stay silent.]"
            )
        return (
            "[System: The customer just made an explicit swap or trade-in request on a live "
            "phone call and is waiting in silence. Respond right now. If you are still "
            "ekaette_router, transfer immediately to valuation_agent. Do not greet again. "
            "Do not stay silent.]"
        )

    return (
        "[System: The customer said something several seconds ago "
        "and is waiting for a response on a phone call. Silence on "
        "a phone call feels like a dropped connection. Say a brief "
        "filler phrase NOW, like 'Let me check that for you', then "
        "proceed with your task.]"
    )


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
        assistant_output_active=False,
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
            (
                silence_state.silence_nudge_due_at,
                silence_state.silence_nudge_interval,
            ) = _reset_silence_nudge_schedule(now)
            # During the greeting lock, substitute silence so the model's
            # server-side VAD doesn't detect caller speech and interrupt
            # the initial greeting — same pattern as SIP/WA bridges.
            # Safety: release after 10s to prevent permanent muting.
            if silence_state.greeting_lock_active:
                if silence_state.greeting_lock_deadline == 0.0:
                    silence_state.greeting_lock_deadline = now + 10.0
                elif now >= silence_state.greeting_lock_deadline:
                    silence_state.greeting_lock_active = False
                    logger.warning("Greeting lock released (safety timeout)")
            if silence_state.greeting_lock_active:
                audio_blob = types_mod.Blob(
                    mime_type="audio/pcm;rate=16000",
                    data=_SILENCE_FRAME,
                )
            else:
                silence_state.agent_busy = True
                audio_blob = types_mod.Blob(
                    mime_type="audio/pcm;rate=16000", data=audio_data,
                )
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
            if msg_type in ("text", "system_text"):
                raw_text_payload = str(json_message.get("text", "") or "").strip()
                if _should_swallow_opening_transport_metadata(
                    ctx,
                    msg_type=msg_type,
                    raw_text=raw_text_payload,
                ):
                    logger.info(
                        "Suppressing opening transport metadata from model session=%s type=%s text=%s",
                        ctx.resolved_session_id,
                        msg_type,
                        raw_text_payload,
                    )
                    continue
            if msg_type in ("text", "system_text", "image", "negotiate", "activity_start"):
                now = time.monotonic()
                silence_state.last_client_activity = now
                silence_state.silence_nudge_count = 0
                silence_state.awaiting_agent_response = False
                silence_state.response_nudge_count = 0
                silence_state.pending_media_analysis = False
                silence_state.user_turn_active = msg_type == "activity_start"
                if msg_type in ("text", "system_text", "image", "negotiate"):
                    silence_state.agent_busy = True
                (
                    silence_state.silence_nudge_due_at,
                    silence_state.silence_nudge_interval,
                ) = _reset_silence_nudge_schedule(now)

            if msg_type == "text":
                raw_text = json_message.get("text", "")
                # System-context markers (e.g. "[Phone call connected]") are
                # transport metadata, NOT customer speech.  Wrapping them as
                # a system hint prevents the model from interpreting them as
                # user intent and immediately trying to act on them.
                if raw_text.startswith("[") and raw_text.endswith("]"):
                    raw_text = (
                        "[System: " + raw_text[1:-1] + ". "
                        "Greet the customer now.]"
                    )
                content = types_mod.Content(parts=[types_mod.Part(text=raw_text)])
                live_request_queue.send_content(content)

            elif msg_type == "system_text":
                raw_text = str(json_message.get("text", "") or "").strip()
                if not raw_text:
                    continue
                system_hint = (
                    "[System: "
                    + raw_text
                    + ". Transport metadata only. The customer has not spoken yet. "
                    "Do not infer any product, support, booking, or callback request. "
                    "Greet the caller warmly now and ask how you can help.]"
                )
                content = types_mod.Content(parts=[types_mod.Part(text=system_hint)])
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
                silence_state.user_turn_active = False
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
    session_state_store = ctx.session_state
    try:
        from app.api.v1.at import voice_analytics
    except Exception:  # pragma: no cover - best effort analytics only
        voice_analytics = None

    def _session_get(key: str, default: Any = None) -> Any:
        getter = getattr(session_state_store, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except TypeError:
                value = getter(key)
                return default if value is None else value
        return default

    def _session_set(key: str, value: Any) -> None:
        try:
            session_state_store[key] = value
        except Exception:
            logger.debug("Failed to persist session key %s", key, exc_info=True)
            return
        if key not in VOICE_STATE_KEYS:
            return
        payload: dict[str, Any] = {}
        if key in VOICE_STATE_BOOL_KEYS:
            if bool(value):
                payload[key] = True
        elif key in VOICE_STATE_STR_KEYS:
            if isinstance(value, str):
                payload[key] = value.strip()
        elif key in VOICE_STATE_INT_KEYS:
            try:
                parsed = int(value or 0)
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                payload[key] = parsed
        if payload:
            update_voice_state(
                user_id=ctx.user_id,
                session_id=ctx.resolved_session_id,
                **payload,
            )

    def _latest_server_message_from_session() -> dict[str, Any] | None:
        raw_message = _session_get("temp:last_server_message", None)
        if not isinstance(raw_message, dict):
            return None
        if not isinstance(raw_message.get("type"), str):
            return None
        return raw_message

    def _queue_session_server_message(payload: dict[str, Any]) -> None:
        raw = _session_get("temp:server_message_seq", 0)
        try:
            current = int(raw)
        except (TypeError, ValueError):
            current = 0
        message = dict(payload)
        message["id"] = current + 1
        _session_set("temp:server_message_seq", current + 1)
        _session_set("temp:last_server_message", message)
        return message

    def _queue_end_after_speaking(reason: str) -> dict[str, Any] | None:
        if bool(_session_get("temp:call_end_after_speaking_requested", False)):
            return None
        _session_set("temp:call_end_after_speaking_requested", True)
        return _queue_session_server_message(
            {
                "type": "call_control",
                "action": "end_after_speaking",
                "reason": reason,
            }
        )

    def _queue_immediate_callback_ack_prompt() -> None:
        prompt = (
            "[System: The callback has already been queued. "
            "Respond immediately with one short acknowledgement, "
            "ask no follow-up question, and then end the call.]"
        )
        try:
            (types_mod,) = bind_runtime_values("types")
            content = types_mod.Content(parts=[types_mod.Part(text=prompt)])
        except Exception:
            content = SimpleNamespace(parts=[SimpleNamespace(text=prompt)])
        live_request_queue.send_content(content)

    def _pending_media_request_voice_ack() -> str:
        raw = _session_get("temp:pending_media_request_voice_ack", "")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
        registry_state = get_registered_voice_state(
            user_id=ctx.user_id,
            session_id=ctx.resolved_session_id,
        )
        registry_raw = registry_state.get("temp:pending_media_request_voice_ack", "")
        if isinstance(registry_raw, str) and registry_raw.strip():
            return registry_raw.strip().lower()
        return ""

    def _queue_media_request_sent_ack_prompt() -> None:
        prompt = (
            "[System: The WhatsApp media request has already been sent successfully. "
            "Respond immediately with one short sentence telling the caller to check "
            "WhatsApp now and send the photo or short video there. Do not say you are "
            "still sending it. Do not ask another question before this acknowledgement. "
            "Do not stay silent.]"
        )
        try:
            (types_mod,) = bind_runtime_values("types")
            content = types_mod.Content(parts=[types_mod.Part(text=prompt)])
        except Exception:
            content = SimpleNamespace(parts=[SimpleNamespace(text=prompt)])
        live_request_queue.send_content(content)

    def _maybe_queue_media_request_sent_ack_prompt() -> bool:
        if _pending_media_request_voice_ack() != "ready":
            return False
        if str(_session_get("app:channel", "") or "").strip().lower() != "voice":
            _session_set("temp:pending_media_request_voice_ack", "")
            return False
        _session_set("temp:pending_media_request_voice_ack", "")
        _queue_media_request_sent_ack_prompt()
        silence_state.awaiting_agent_response = True
        silence_state.user_turn_active = False
        silence_state.user_spoke_at = time.monotonic()
        silence_state.response_nudge_count = 0
        _sync_pending_media_analysis()
        logger.info(
            "Queued immediate media-request acknowledgement prompt session=%s agent=%s",
            sanitize_log_fn(ctx.resolved_session_id),
            sanitize_log_fn(_session_get("temp:active_agent", "") or "unknown"),
        )
        return True

    def _resolve_opening_sentence(log_context: str) -> str:
        opening_sentence = "Hello, this is ehkaitay from our service desk."
        try:
            from app.agents.callbacks import (
                _first_turn_opening,
                _resolve_company_names,
                _resolve_first_turn_customer_name,
            )

            company_profile = _session_get("app:company_profile", {})
            if not isinstance(company_profile, dict):
                company_profile = {}
            _display_name, spoken_name = _resolve_company_names(company_profile)
            opening_sentence = _first_turn_opening(
                spoken_name,
                _resolve_first_turn_customer_name(ctx.session_state),
            )
        except Exception:
            logger.debug("%s fell back to generic greeting", log_context, exc_info=True)
        return opening_sentence

    def _opening_bootstrap_prompt_text() -> str:
        opening_sentence = _resolve_opening_sentence("Opening bootstrap prompt")
        return (
            "[System: Opening phase recovery. No greeting has been spoken yet. "
            "Do not call any tools or transfer. "
            f"Speak the required opening greeting immediately: '{opening_sentence} "
            "How can I help you today?' "
            "Do not stay silent.]"
        )

    def _queue_opening_bootstrap_prompt() -> bool:
        retry_raw = _session_get("temp:opening_bootstrap_retry_count", 0)
        try:
            retry_count = int(retry_raw or 0)
        except (TypeError, ValueError):
            retry_count = 0
        if retry_count >= 1:
            logger.warning(
                "Opening bootstrap retry exhausted session=%s",
                sanitize_log_fn(ctx.resolved_session_id),
            )
            return False
        _session_set("temp:opening_bootstrap_retry_count", retry_count + 1)
        prompt = _opening_bootstrap_prompt_text()
        try:
            (types_mod,) = bind_runtime_values("types")
            content = types_mod.Content(parts=[types_mod.Part(text=prompt)])
        except Exception:
            content = SimpleNamespace(parts=[SimpleNamespace(text=prompt)])
        live_request_queue.send_content(content)
        logger.info(
            "Queued opening bootstrap prompt session=%s attempt=%d",
            sanitize_log_fn(ctx.resolved_session_id),
            retry_count + 1,
        )
        return True

    def _background_media_analysis_running() -> bool:
        raw = _session_get("temp:background_vision_status", "")
        if isinstance(raw, str) and raw.strip().lower() == "running":
            return True
        registry_state = get_registered_voice_state(
            user_id=ctx.user_id,
            session_id=ctx.resolved_session_id,
        )
        registry_raw = registry_state.get("temp:background_vision_status", "")
        return isinstance(registry_raw, str) and registry_raw.strip().lower() == "running"

    def _sync_pending_media_analysis() -> None:
        silence_state.pending_media_analysis = _background_media_analysis_running()

    def _queue_transfer_bootstrap_prompt(target_agent: str, reason: str) -> None:
        latest_user = _session_get("temp:pending_handoff_latest_user", "")
        recent_customer = _session_get("temp:pending_handoff_recent_customer_context", "")
        if (
            reason in {"voice_tradein_recovery", "voice_tradein_handoff"}
            and target_agent == "valuation_agent"
        ):
            routing_phrase = (
                "Routing recovery just transferred"
                if reason == "voice_tradein_recovery"
                else "Routing just transferred"
            )
            prompt = (
                f"[System: {routing_phrase} this live voice trade-in call to "
                "valuation_agent. Respond immediately with one short continuity phrase. Do not "
                "greet or stay silent. If request_media_via_whatsapp has not succeeded yet on "
                "this call, call it now before you say the message was sent or ask the caller "
                "to check WhatsApp. After the tool succeeds, tell the caller briefly to check "
                "WhatsApp. Do not ask them to describe visible condition before the media is "
                "received.]"
            )
        else:
            prompt = (
                "[System: Routing recovery just transferred this live voice call. Respond "
                "immediately with one short continuity phrase, do not greet again, and continue "
                "the task without staying silent.]"
            )
        if isinstance(latest_user, str) and latest_user.strip():
            prompt += f" Latest customer request: '{latest_user.strip()}'."
        if isinstance(recent_customer, str) and recent_customer.strip():
            prompt += f" Recent customer context: '{recent_customer.strip()}'."
        try:
            (types_mod,) = bind_runtime_values("types")
            content = types_mod.Content(parts=[types_mod.Part(text=prompt)])
        except Exception:
            content = SimpleNamespace(parts=[SimpleNamespace(text=prompt)])
        logger.info(
            "Queued transfer bootstrap prompt session=%s target=%s reason=%s",
            sanitize_log_fn(ctx.resolved_session_id),
            target_agent,
            reason,
        )
        live_request_queue.send_content(content)

    async def _emit_end_after_speaking(reason: str) -> None:
        message = _queue_end_after_speaking(reason)
        if not message:
            return
        payload = {k: v for k, v in message.items() if k != "id"}
        await websocket.send_text(json.dumps(payload))
        nonlocal last_structured_message_id
        try:
            last_structured_message_id = max(last_structured_message_id, int(message.get("id", 0)))
        except (TypeError, ValueError):
            pass

    def _looks_like_callback_closing(text: str) -> bool:
        normalized = " ".join((text or "").lower().split())
        if not normalized or "?" in normalized:
            return False
        callback_markers = (
            "call you back",
            "call back shortly",
            "same number",
            "callback",
            "we will call",
            "we'll call",
        )
        farewell_markers = (
            "have a great day",
            "goodbye",
            "bye.",
            " bye",
            "take care",
            "speak soon",
            "talk soon",
        )
        return any(marker in normalized for marker in callback_markers) or any(
            marker in normalized for marker in farewell_markers
        )

    current_agent_raw = _session_get("temp:active_agent", "ekaette_router")
    current_agent = current_agent_raw if isinstance(current_agent_raw, str) else "ekaette_router"
    opening_audio_buffer: list[bytes] = []
    opening_candidate_text = ""

    def _record_voice_transcript(role: str, text: str, partial: bool) -> None:
        if voice_analytics is None:
            return
        try:
            voice_analytics.record_transcript(
                session_id=ctx.resolved_session_id,
                role=role,
                text=text,
                partial=partial,
            )
        except Exception:
            logger.debug("Voice analytics transcript capture skipped", exc_info=True)

    def _record_voice_transfer(target_agent: str) -> None:
        if voice_analytics is None:
            return
        try:
            voice_analytics.record_transfer(
                session_id=ctx.resolved_session_id,
                target_agent=target_agent,
            )
        except Exception:
            logger.debug("Voice analytics transfer capture skipped", exc_info=True)

    def _is_voice_channel() -> bool:
        channel = _session_get("app:channel", "")
        return isinstance(channel, str) and channel.strip().lower() == "voice"

    def _is_live_voice_session_local() -> bool:
        if _is_voice_channel():
            return True
        return bool(getattr(ctx, "is_native_audio", False))

    def _is_opening_phase_complete() -> bool:
        return bool(_session_get("temp:opening_phase_complete", False))

    def _server_owned_opening_enabled() -> bool:
        return VOICE_SERVER_OWNED_OPENING_ENABLED and _is_live_voice_session_local()

    def _server_owned_preuser_guard_active() -> bool:
        if not _server_owned_opening_enabled():
            return False
        if bool(_session_get("temp:first_user_turn_started", False)):
            return False
        if bool(_session_get("temp:first_user_turn_complete", False)):
            return False
        if bool(_session_get("temp:opening_greeting_complete", False)):
            return False
        return server_owned_opening_pending

    def _server_owned_opening_text() -> str:
        opening_sentence = _resolve_opening_sentence("Server-owned opening")
        return f"{opening_sentence} How can I help you today?"

    def _normalize_opening_contract_text(text: str) -> str:
        lowered = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
        return " ".join(lowered.split()).strip()

    def _opening_output_matches_expected(text: str) -> bool:
        candidate = _normalize_opening_contract_text(text)
        if not candidate:
            return False
        if "this is ehkaitay" not in candidate and "welcome back" not in candidate:
            return False
        for forbidden in (
            "catalog",
            "callback",
            "call you back",
            "transfer",
            "booking agent",
            "valuation agent",
            "support agent",
        ):
            if forbidden in candidate:
                return False
        expected = _normalize_opening_contract_text(_server_owned_opening_text())
        return expected in candidate or _text_overlap(expected, candidate) >= 0.75

    def _clear_buffered_opening_output() -> None:
        nonlocal opening_candidate_text
        opening_audio_buffer.clear()
        opening_candidate_text = ""

    async def _flush_buffered_opening_greeting(text: str) -> None:
        nonlocal opening_turn_completed_at
        nonlocal opening_output_observed
        nonlocal server_owned_opening_pending
        server_owned_opening_pending = False
        silence_state.agent_busy = True
        silence_state.assistant_output_active = True
        silence_state.awaiting_agent_response = False
        silence_state.user_turn_active = False
        _sync_pending_media_analysis()

        await websocket.send_text(json.dumps({
            "type": "agent_status",
            "agent": current_agent,
            "status": "active",
        }))
        for audio_bytes in opening_audio_buffer:
            await websocket.send_bytes(audio_bytes)
        await websocket.send_text(json.dumps({
            "type": "transcription",
            "role": "agent",
            "text": text,
            "partial": False,
        }))

        _record_voice_transcript("agent", text, False)
        recent_turns.append(("agent", text))
        _session_set("temp:last_agent_turn", text)
        _session_set("temp:opening_greeting_server_owned", True)
        opening_output_observed = True
        opening_turn_completed_at = time.monotonic()
        _complete_opening_greeting("validated server-owned live opening")
        _clear_buffered_opening_output()
        logger.info(
            "Server-owned opening greeting validated session=%s voice=%s",
            sanitize_log_fn(ctx.resolved_session_id),
            sanitize_log_fn(ctx.session_voice),
        )

    def _looks_like_callback_request(text: str) -> bool:
        from app.agents.callbacks import looks_like_callback_request

        return looks_like_callback_request(text)

    def _maybe_register_callback_from_user_turn(text: str) -> None:
        if not _looks_like_callback_request(text):
            return
        if bool(_session_get("temp:callback_requested", False)):
            return

        # Never register callbacks on callback legs (prevents infinite loop).
        session_id = _session_get("app:session_id", "")
        if isinstance(session_id, str) and session_id.strip().startswith("sip-callback-"):
            return

        channel = _session_get("app:channel", "voice")
        if isinstance(channel, str) and channel.strip().lower() != "voice":
            return

        from app.api.v1.at import service_voice
        from app.api.v1.realtime.caller_phone_registry import get_registered_caller_phone
        from app.tools.sms_messaging import resolve_caller_phone_from_state

        caller_phone = resolve_caller_phone_from_state(session_state_store)
        if not caller_phone:
            caller_phone = get_registered_caller_phone(
                user_id=ctx.user_id,
                session_id=ctx.resolved_session_id,
            )
        if not caller_phone:
            caller_phone = getattr(ctx, "caller_phone", "") or ""
        if not caller_phone:
            logger.warning(
                "Voice callback intent detected but caller phone was unavailable "
                "user_id=%s session_id=%s",
                sanitize_log_fn(ctx.user_id),
                sanitize_log_fn(ctx.resolved_session_id),
            )
            return

        result = service_voice.register_callback_request(
            phone=caller_phone,
            tenant_id=ctx.tenant_id,
            company_id=ctx.company_id,
            source="voice_user_callback_intent",
            reason=text,
            trigger_after_hangup=True,
        )
        status = str(result.get("status", "")).strip().lower() if isinstance(result, dict) else ""
        if status in {"pending", "queued", "cooldown"}:
            _session_set("temp:callback_requested", True)
            _queue_immediate_callback_ack_prompt()
            logger.info(
                "Queued callback from live voice transcript phone=%s tenant_id=%s "
                "company_id=%s status=%s",
                sanitize_log_fn(caller_phone),
                sanitize_log_fn(ctx.tenant_id),
                sanitize_log_fn(ctx.company_id),
                status,
            )
        else:
            logger.warning(
                "Failed to queue callback from live voice transcript phone=%s "
                "tenant_id=%s company_id=%s result=%r",
                sanitize_log_fn(caller_phone),
                sanitize_log_fn(ctx.tenant_id),
                sanitize_log_fn(ctx.company_id),
                result,
            )

    def _maybe_register_callback_from_agent_promise(text: str) -> None:
        """Register a callback when the agent's output promises one.

        Catches cases where the model says "I'll call you back" but the
        request_callback tool fails (e.g. caller phone not in tool context).
        This runs in the stream layer which has direct access to caller
        identity via ctx and the ephemeral registry.
        """
        from app.agents.callbacks import looks_like_callback_promise

        if not looks_like_callback_promise(text):
            return
        if bool(_session_get("temp:callback_requested", False)):
            return

        # Never register callbacks on callback legs (prevents infinite loop).
        session_id = _session_get("app:session_id", "")
        if isinstance(session_id, str) and session_id.strip().startswith("sip-callback-"):
            return

        channel = _session_get("app:channel", "voice")
        if isinstance(channel, str) and channel.strip().lower() != "voice":
            return

        from app.api.v1.at import service_voice
        from app.api.v1.realtime.caller_phone_registry import get_registered_caller_phone
        from app.tools.sms_messaging import resolve_caller_phone_from_state

        caller_phone = resolve_caller_phone_from_state(session_state_store)
        if not caller_phone:
            caller_phone = get_registered_caller_phone(
                user_id=ctx.user_id,
                session_id=ctx.resolved_session_id,
            )
        if not caller_phone:
            caller_phone = getattr(ctx, "caller_phone", "") or ""
        if not caller_phone:
            logger.warning(
                "Agent callback promise detected but caller phone unavailable "
                "user_id=%s session_id=%s text=%s",
                sanitize_log_fn(ctx.user_id),
                sanitize_log_fn(ctx.resolved_session_id),
                sanitize_log_fn(text[:120]),
            )
            return

        result = service_voice.register_callback_request(
            phone=caller_phone,
            tenant_id=ctx.tenant_id,
            company_id=ctx.company_id,
            source="voice_agent_callback_promise",
            reason=text[:240],
            trigger_after_hangup=True,
        )
        status = str(result.get("status", "")).strip().lower() if isinstance(result, dict) else ""
        if status in {"pending", "queued", "cooldown"}:
            _session_set("temp:callback_requested", True)
            _queue_end_after_speaking("callback_registered")
            logger.info(
                "Queued callback from agent promise phone=%s tenant_id=%s "
                "company_id=%s status=%s",
                sanitize_log_fn(caller_phone),
                sanitize_log_fn(ctx.tenant_id),
                sanitize_log_fn(ctx.company_id),
                status,
            )
        else:
            logger.warning(
                "Failed to queue callback from agent promise phone=%s "
                "tenant_id=%s company_id=%s result=%r",
                sanitize_log_fn(caller_phone),
                sanitize_log_fn(ctx.tenant_id),
                sanitize_log_fn(ctx.company_id),
                result,
            )

    last_input_text = ""
    last_output_text = ""
    receiving_input = False
    input_finalized = False   # late-partial suppression
    output_finalized = False  # late-partial suppression
    # Output-level dedup: suppress near-duplicate finished transcriptions
    # (ADK Bug #3395 — model sometimes emits two finals within ~2s)
    _last_final_text = ""
    _last_final_ts = 0.0
    last_structured_message_id = 0
    session_prompt_tokens = 0
    session_completion_tokens = 0
    session_total_tokens = 0
    session_cost_usd = 0.0
    retry_attempt = 0
    # Track recent conversation turns for context recovery after 1011 crashes
    # and for explicit agent handoffs.
    # Each entry: (role, text) — keeps the last 6 turns.
    recent_turns: deque[tuple[str, str]] = deque(maxlen=6)
    model_has_spoken = False
    opening_turn_completed_at = 0.0
    suppress_preuser_opening_output = False
    opening_output_observed = False
    server_owned_opening_pending = False
    server_owned_opening_logged = False
    handoff_sig_raw = _session_get("temp:last_transfer_handoff_signature", "")
    last_handoff_signature = handoff_sig_raw if isinstance(handoff_sig_raw, str) else ""

    def _recent_turn_lines(*, include_agent: bool = True) -> list[str]:
        lines: list[str] = []
        for role, text in recent_turns:
            if role == "agent" and not include_agent:
                continue
            label = "Customer" if role == "user" else "You"
            lines.append(f"  {label}: {text}")
        return lines

    def _latest_user_turn() -> str:
        for role, text in reversed(recent_turns):
            if role == "user" and isinstance(text, str) and text.strip():
                return text.strip()
        return ""

    def _latest_agent_turn() -> str:
        for role, text in reversed(recent_turns):
            if role == "agent" and isinstance(text, str) and text.strip():
                return text.strip()
        return ""

    def _persist_recent_customer_context() -> None:
        customer_lines = _recent_turn_lines(include_agent=False)
        recent_customer = "\n".join(customer_lines[-3:])
        _session_set("temp:recent_customer_context", recent_customer)

    def _mark_greeted_from_agent_output() -> None:
        """Persist that the agent has already greeted once output begins."""
        if not bool(_session_get("temp:greeted", False)):
            _session_set("temp:greeted", True)

    def _complete_opening_greeting(reason: str) -> None:
        nonlocal opening_turn_completed_at
        if not silence_state.greeting_lock_active:
            return
        silence_state.greeting_lock_active = False
        _session_set("temp:greeted", True)
        _session_set("temp:opening_bootstrap_retry_count", 0)
        if _is_voice_channel():
            _session_set("temp:opening_greeting_complete", True)
            _session_set("temp:greeting_block_count", 0)
            opening_turn_completed_at = time.monotonic()
        logger.info("Greeting lock released (%s)", reason)

    def _preuser_opening_duplicate_active() -> bool:
        if not _is_voice_channel():
            return False
        if not bool(_session_get("temp:opening_greeting_complete", False)):
            return False
        if bool(_session_get("temp:first_user_turn_started", False)):
            return False
        if opening_turn_completed_at <= 0.0:
            return False
        return (time.monotonic() - opening_turn_completed_at) < OPENING_DUPLICATE_SUPPRESSION_SECONDS

    def _clear_pending_handoff() -> None:
        for key in (
            "temp:pending_handoff_target_agent",
            "temp:pending_handoff_latest_user",
            "temp:pending_handoff_latest_agent",
            "temp:pending_handoff_recent_customer_context",
            "temp:pending_transfer_bootstrap_target_agent",
            "temp:pending_transfer_bootstrap_reason",
        ):
            _session_set(key, "")

    def _persist_transfer_handoff_state(target_agent: str) -> None:
        nonlocal last_handoff_signature
        latest_user = _latest_user_turn()
        latest_agent = _latest_agent_turn()
        customer_context_lines = _recent_turn_lines(include_agent=False)
        customer_context = "\n".join(customer_context_lines[-3:])
        signature = f"{target_agent}|{latest_user}|{latest_agent}|{customer_context}"
        if signature == last_handoff_signature:
            logger.debug(
                "Skipping duplicate persisted handoff for agent=%s session=%s",
                target_agent,
                sanitize_log_fn(ctx.resolved_session_id),
            )
            return
        last_handoff_signature = signature
        _session_set("temp:last_transfer_handoff_signature", signature)
        _session_set("temp:pending_handoff_target_agent", target_agent)
        _session_set("temp:pending_handoff_latest_user", latest_user)
        _session_set("temp:pending_handoff_latest_agent", latest_agent)
        _session_set("temp:pending_handoff_recent_customer_context", customer_context)
        logger.info(
            "Persisted transfer handoff for agent=%s session=%s",
            target_agent,
            sanitize_log_fn(ctx.resolved_session_id),
        )

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
        silence_state.user_turn_active = False

    async def _finalize_output(*, interrupted: bool = False) -> None:
        """Send a non-partial output transcription to close the agent's turn."""
        nonlocal last_output_text, output_finalized
        if last_output_text:
            await websocket.send_text(json.dumps({
                "type": "transcription",
                "role": "agent",
                "text": last_output_text,
                "partial": False,
            }))
            # Preserve partial agent text on interruption so recovery
            # instructions have context about what the model was saying.
            if interrupted:
                _session_set("temp:last_agent_turn", last_output_text)
        last_output_text = ""
        output_finalized = True

    if (
        _server_owned_opening_enabled()
        and not bool(_session_get("temp:opening_greeting_complete", False))
        and not bool(_session_get("temp:first_user_turn_started", False))
        and not bool(_session_get("temp:first_user_turn_complete", False))
    ):
        server_owned_opening_pending = _queue_opening_bootstrap_prompt()

    try:
        while session_alive.is_set():
            try:
                async for event in runner_obj.run_live(
                    user_id=ctx.user_id,
                    session_id=ctx.resolved_session_id,
                    live_request_queue=live_request_queue,
                    run_config=ctx.run_config,
                ):
                    if retry_attempt > 0:
                        retry_attempt = 0
                    event_had_agent_output = False
                    try:
                        # Audio + Text content
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if _server_owned_preuser_guard_active():
                                    if (
                                        part.inline_data
                                        and part.inline_data.data
                                        and part.inline_data.mime_type
                                        and "audio" in part.inline_data.mime_type
                                    ):
                                        audio_bytes = part.inline_data.data
                                        if isinstance(audio_bytes, str):
                                            audio_bytes = base64.b64decode(audio_bytes)
                                        opening_audio_buffer.append(audio_bytes)
                                        opening_output_observed = True
                                        event_had_agent_output = True
                                        if not server_owned_opening_logged:
                                            logger.info(
                                                "Buffering pre-user opening audio until transcript validation "
                                                "session=%s author=%s",
                                                sanitize_log_fn(ctx.resolved_session_id),
                                                sanitize_log_fn(event.author or current_agent),
                                            )
                                            server_owned_opening_logged = True
                                    elif part.text and not ctx.is_native_audio:
                                        opening_candidate_text = str(part.text or "").strip()
                                        opening_output_observed = True
                                        event_had_agent_output = True
                                    continue
                                server_owned_opening_logged = False
                                # Audio -> binary WebSocket frame (lowest latency)
                                if (
                                    part.inline_data
                                    and part.inline_data.data
                                    and part.inline_data.mime_type
                                    and "audio" in part.inline_data.mime_type
                                ):
                                    if _preuser_opening_duplicate_active():
                                        if not suppress_preuser_opening_output:
                                            logger.warning(
                                                "Suppressing duplicate pre-user opening audio "
                                                "session=%s author=%s",
                                                sanitize_log_fn(ctx.resolved_session_id),
                                                sanitize_log_fn(event.author or current_agent),
                                            )
                                        suppress_preuser_opening_output = True
                                        continue
                                    silence_state.agent_busy = True
                                    silence_state.assistant_output_active = True
                                    silence_state.awaiting_agent_response = False
                                    silence_state.user_turn_active = False
                                    _sync_pending_media_analysis()
                                    opening_output_observed = True
                                    event_had_agent_output = True
                                    _mark_greeted_from_agent_output()
                                    audio_bytes = part.inline_data.data
                                    if isinstance(audio_bytes, str):
                                        audio_bytes = base64.b64decode(audio_bytes)
                                    await websocket.send_bytes(audio_bytes)
    
                                # Text -> transcription (text-mode fallback only)
                                elif part.text and not ctx.is_native_audio:
                                    if _preuser_opening_duplicate_active():
                                        if not suppress_preuser_opening_output:
                                            logger.warning(
                                                "Suppressing duplicate pre-user opening text "
                                                "session=%s author=%s",
                                                sanitize_log_fn(ctx.resolved_session_id),
                                                sanitize_log_fn(event.author or current_agent),
                                            )
                                        suppress_preuser_opening_output = True
                                        continue
                                    silence_state.agent_busy = True
                                    silence_state.assistant_output_active = True
                                    silence_state.awaiting_agent_response = False
                                    silence_state.user_turn_active = False
                                    _sync_pending_media_analysis()
                                    opening_output_observed = True
                                    event_had_agent_output = True
                                    _mark_greeted_from_agent_output()
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
                                if _server_owned_preuser_guard_active():
                                    logger.warning(
                                        "Caller spoke before opening greeting was validated session=%s; "
                                        "aborting buffered opening output",
                                        sanitize_log_fn(ctx.resolved_session_id),
                                    )
                                    server_owned_opening_pending = False
                                    _clear_buffered_opening_output()
                                if input_finalized and not finished:
                                    # Suppress late partials after input was already finalized
                                    pass
                                else:
                                    if input_finalized:
                                        # New final after prior finalization -> new utterance
                                        input_finalized = False
                                    last_input_text = text
                                    receiving_input = True
                                    silence_state.user_turn_active = not finished
                                    if _is_voice_channel() and bool(
                                        _session_get("temp:opening_greeting_complete", False)
                                    ):
                                        _session_set("temp:first_user_turn_started", True)
                                        _session_set("temp:opening_phase_complete", True)
                                    is_partial = not finished
                                    await websocket.send_text(json.dumps({
                                        "type": "transcription",
                                        "role": "user",
                                        "text": redact_pii(text),
                                        "partial": is_partial,
                                    }))
                                    _record_voice_transcript("user", text, is_partial)
                                    if finished:
                                        recent_turns.append(("user", text))
                                        _session_set("temp:last_user_turn", text)
                                        if _is_voice_channel() and bool(
                                            _session_get("temp:opening_greeting_complete", False)
                                        ):
                                            _session_set("temp:first_user_turn_started", True)
                                            _session_set("temp:first_user_turn_complete", True)
                                            _session_set("temp:opening_phase_complete", True)
                                            _session_set("temp:greeting_block_count", 0)
                                        _maybe_register_callback_from_user_turn(text)
                                        _persist_recent_customer_context()
                                        last_input_text = ""
                                        receiving_input = False
                                        input_finalized = True
    
                                    # Arm/reset response latency watchdog
                                    silence_state.awaiting_agent_response = True
                                    silence_state.user_spoke_at = time.monotonic()
                                    silence_state.response_nudge_count = 0
                                    _sync_pending_media_analysis()
                                    if finished:
                                        logger.info(
                                            "Armed response-latency watchdog session=%s agent=%s media_pending=%s user=%s",
                                            sanitize_log_fn(ctx.resolved_session_id),
                                            sanitize_log_fn(current_agent),
                                            silence_state.pending_media_analysis,
                                            redact_pii(text)[:120],
                                        )
    
                        # Output transcription (agent's speech -> text)
                        if event.output_transcription:
                            text = getattr(event.output_transcription, "text", None)
                            finished = getattr(event.output_transcription, "finished", False)
                            if text:
                                if _server_owned_preuser_guard_active():
                                    if finished:
                                        opening_candidate_text = str(text or "").strip()
                                        opening_output_observed = True
                                    elif not server_owned_opening_logged:
                                        logger.info(
                                            "Buffering pre-user opening transcription until turn completion "
                                            "session=%s text=%s",
                                            sanitize_log_fn(ctx.resolved_session_id),
                                            text[:80],
                                        )
                                        server_owned_opening_logged = True
                                    if finished:
                                        _last_final_text = text
                                        _last_final_ts = time.monotonic()
                                        last_output_text = ""
                                        output_finalized = True
                                    continue
                                server_owned_opening_logged = False
                                if suppress_preuser_opening_output or _preuser_opening_duplicate_active():
                                    if not suppress_preuser_opening_output:
                                        logger.warning(
                                            "Suppressing duplicate pre-user opening transcription "
                                            "session=%s text=%s",
                                            sanitize_log_fn(ctx.resolved_session_id),
                                            text[:80],
                                        )
                                    suppress_preuser_opening_output = True
                                    if finished:
                                        _last_final_text = text
                                        _last_final_ts = time.monotonic()
                                        last_output_text = ""
                                        output_finalized = True
                                    continue
                                silence_state.agent_busy = True
                                silence_state.assistant_output_active = True
                                if output_finalized and not finished:
                                    # Suppress late partials after output was already finalized
                                    pass
                                else:
                                    silence_state.awaiting_agent_response = False
                                    silence_state.user_turn_active = False
                                    _sync_pending_media_analysis()
                                    if output_finalized:
                                        output_finalized = False
                                    # Agent started responding -> finalize user's input
                                    if receiving_input:
                                        await _finalize_input()
                                    opening_output_observed = True
                                    event_had_agent_output = True
                                    _mark_greeted_from_agent_output()
                                    last_output_text = text
                                    is_partial = not finished
    
                                    # Output-level dedup (ADK #3395): suppress
                                    # near-duplicate finished transcriptions
                                    # arriving within 3s of each other.
                                    _is_dup_final = False
                                    if finished:
                                        _now_f = time.monotonic()
                                        if (
                                            _last_final_text
                                            and (_now_f - _last_final_ts) < 3.0
                                            and _text_overlap(
                                                _last_final_text, text
                                            ) > 0.6
                                        ):
                                            _is_dup_final = True
                                            logger.info(
                                                "Dedup output: suppressing duplicate "
                                                "final (%.1fs gap) text=%s",
                                                _now_f - _last_final_ts,
                                                text[:80],
                                            )
    
                                    if not _is_dup_final:
                                        await websocket.send_text(json.dumps({
                                            "type": "transcription",
                                            "role": "agent",
                                            "text": text,
                                            "partial": is_partial,
                                        }))
                                        _record_voice_transcript("agent", text, is_partial)
                                    # Check partial transcriptions too — if
                                    # Gemini crashes mid-turn the finished event
                                    # never arrives and the promise is lost.
                                    _maybe_register_callback_from_agent_promise(text)
                                    if finished:
                                        if not _is_dup_final:
                                            recent_turns.append(("agent", text))
                                            _session_set(
                                                "temp:last_agent_turn", text
                                            )
                                        _last_final_text = text
                                        _last_final_ts = time.monotonic()
                                        model_has_spoken = True
                                        last_output_text = ""
                                        output_finalized = True
                                        pending_target = _session_get(
                                            "temp:pending_handoff_target_agent", ""
                                        )
                                        if (
                                            isinstance(pending_target, str)
                                            and pending_target.strip()
                                            and pending_target.strip() == current_agent
                                        ):
                                            _clear_pending_handoff()
                                        session_id_for_close = _session_get("app:session_id", "")
                                        if (
                                            bool(_session_get("temp:callback_requested", False))
                                            and not (
                                                isinstance(session_id_for_close, str)
                                                and session_id_for_close.strip().startswith("sip-callback-")
                                            )
                                            and _looks_like_callback_closing(text)
                                        ):
                                            await _emit_end_after_speaking(
                                                "callback_acknowledged"
                                            )
                                        if silence_state.greeting_lock_active:
                                            _complete_opening_greeting("first output transcription complete")
    
                        # Interrupted -> finalize + clear playback
                        if event.interrupted:
                            if _is_voice_channel() and not _is_opening_phase_complete():
                                logger.info(
                                    "Ignoring interrupted event during protected voice opening "
                                    "session=%s",
                                    sanitize_log_fn(ctx.resolved_session_id),
                                )
                                continue
                            await _finalize_input()
                            await _finalize_output(interrupted=True)
                            silence_state.agent_busy = False
                            silence_state.assistant_output_active = False
                            silence_state.awaiting_agent_response = False
                            _sync_pending_media_analysis()
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
                                existing_pending = _session_get(
                                    "temp:pending_handoff_target_agent", ""
                                )
                                if (
                                    not isinstance(existing_pending, str)
                                    or existing_pending.strip() != new_agent
                                ):
                                    _persist_transfer_handoff_state(new_agent)
                                current_agent = new_agent
                                _session_set("temp:active_agent", current_agent)
                                _record_voice_transfer(new_agent)
                                bootstrap_target = _session_get(
                                    "temp:pending_transfer_bootstrap_target_agent", ""
                                )
                                bootstrap_reason = _session_get(
                                    "temp:pending_transfer_bootstrap_reason", ""
                                )
                                if (
                                    (not isinstance(bootstrap_target, str) or not bootstrap_target.strip())
                                    and ctx.user_id
                                    and ctx.resolved_session_id
                                ):
                                    registry_state = get_registered_voice_state(
                                        user_id=ctx.user_id,
                                        session_id=ctx.resolved_session_id,
                                    )
                                    registry_target = registry_state.get(
                                        "temp:pending_transfer_bootstrap_target_agent", ""
                                    )
                                    registry_reason = registry_state.get(
                                        "temp:pending_transfer_bootstrap_reason", ""
                                    )
                                    if isinstance(registry_target, str) and registry_target.strip():
                                        bootstrap_target = registry_target.strip()
                                    if isinstance(registry_reason, str) and registry_reason.strip():
                                        bootstrap_reason = registry_reason.strip()
                                if (
                                    isinstance(bootstrap_target, str)
                                    and bootstrap_target.strip() == current_agent
                                ):
                                    _session_set("temp:pending_transfer_bootstrap_target_agent", "")
                                    _session_set("temp:pending_transfer_bootstrap_reason", "")
                                    if isinstance(bootstrap_reason, str) and bootstrap_reason.strip():
                                        _queue_transfer_bootstrap_prompt(
                                            current_agent,
                                            bootstrap_reason.strip(),
                                        )
                                output_finished = bool(
                                    getattr(event.output_transcription, "finished", False)
                                ) if event.output_transcription else False
                                if output_finished:
                                    pending_target = _session_get(
                                        "temp:pending_handoff_target_agent", ""
                                    )
                                    if (
                                        isinstance(pending_target, str)
                                        and pending_target.strip()
                                        and pending_target.strip() == current_agent
                                    ):
                                        _clear_pending_handoff()
                                await websocket.send_text(json.dumps({
                                    "type": "agent_status",
                                    "agent": new_agent,
                                    "status": "active",
                                }))
    
                        # Structured ServerMessages from callbacks/state delta
                        state_delta = event.actions.state_delta if event.actions else None
                        structured = extract_server_message_from_state_delta_fn(state_delta)
                        if not structured:
                            structured = _latest_server_message_from_session()
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
    
                        if event_had_agent_output and _pending_media_request_voice_ack() == "ready":
                            _session_set("temp:pending_media_request_voice_ack", "")
                        elif not event_had_agent_output:
                            _maybe_queue_media_request_sent_ack_prompt()
    
                        # Turn complete -> finalize output + status
                        if event.turn_complete:
                            await _finalize_input()
                            await _finalize_output()
                            if silence_state.greeting_lock_active:
                                if _server_owned_preuser_guard_active():
                                    if _opening_output_matches_expected(opening_candidate_text):
                                        await _flush_buffered_opening_greeting(opening_candidate_text)
                                    elif opening_candidate_text or opening_audio_buffer:
                                        logger.warning(
                                            "Buffered opening output failed validation session=%s text=%s",
                                            sanitize_log_fn(ctx.resolved_session_id),
                                            opening_candidate_text[:120],
                                        )
                                        _clear_buffered_opening_output()
                                        server_owned_opening_pending = _queue_opening_bootstrap_prompt()
                                    else:
                                        logger.warning(
                                            "Opening turn completed before any validated assistant output session=%s",
                                            sanitize_log_fn(ctx.resolved_session_id),
                                        )
                                        server_owned_opening_pending = _queue_opening_bootstrap_prompt()
                                elif opening_output_observed:
                                    _complete_opening_greeting("first turn complete after output")
                                else:
                                    logger.warning(
                                        "Greeting turn completed before any assistant output session=%s",
                                        sanitize_log_fn(ctx.resolved_session_id),
                                    )
                                    server_owned_opening_pending = _queue_opening_bootstrap_prompt()
                            # Anchor silence nudges to when the agent actually finishes,
                            # not when the user last spoke. This avoids check-in nudges
                            # racing right after a long agent response.
                            now = time.monotonic()
                            silence_state.agent_busy = False
                            silence_state.assistant_output_active = False
                            _sync_pending_media_analysis()
                            if now >= silence_state.last_client_activity:
                                silence_state.silence_nudge_due_at = now + max(
                                    1.0, float(silence_state.silence_nudge_interval)
                                )
                            # Reset suppression flags for the next turn
                            input_finalized = False
                            output_finalized = False
                            suppress_preuser_opening_output = False
                            server_owned_opening_logged = False
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
                            if not ctx.live_session_resumption_enabled:
                                logger.debug("Ignoring live session resumption update on Gemini API backend")
                                continue
                            logger.debug("Session resumption token received")
                            token_val = getattr(event.live_session_resumption_update, "token", None)
                            if isinstance(token_val, str) and token_val:
                                session_resumption = getattr(ctx.run_config, "session_resumption", None)
                                if session_resumption is not None:
                                    try:
                                        setattr(session_resumption, "handle", token_val)
                                    except Exception:
                                        logger.debug("Failed to update in-process resumption handle", exc_info=True)
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
                        if _is_connection_error(e):
                            raise
                        logger.error("Error processing downstream event: %s", e, exc_info=True)
                        if not session_alive.is_set():
                            break

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
                return
            except Exception as exc:
                if (
                    session_alive.is_set()
                    and retry_attempt < LIVE_STREAM_MAX_RETRIES
                    and _is_retryable_live_error(exc)
                ):
                    retry_attempt += 1
                    delay = min(LIVE_STREAM_RETRY_BASE_SECONDS * (2 ** (retry_attempt - 1)), 5.0)
                    logger.warning(
                        "Retrying transient live stream failure attempt=%d/%d delay=%.2fs session=%s",
                        retry_attempt,
                        LIVE_STREAM_MAX_RETRIES,
                        delay,
                        sanitize_log_fn(ctx.resolved_session_id),
                        exc_info=True,
                    )
                    await asyncio.sleep(delay)
                    # Inject context recovery so the model doesn't re-greet
                    # or lose track of the conversation after a 1011 crash.
                    if model_has_spoken and recent_turns:
                        (types_mod,) = bind_runtime_values("types")
                        lines = _recent_turn_lines()
                        recovery = (
                            "[System: The connection was briefly interrupted. "
                            "You are resuming a conversation already in progress. "
                            "Do NOT greet the customer again — no hello, no introduction. "
                            "Continue naturally from where you left off.\n"
                            "Recent conversation:\n"
                            + "\n".join(lines)
                            + "]"
                        )
                        live_request_queue.send_content(
                            types_mod.Content(
                                parts=[types_mod.Part(text=recovery)],
                            )
                        )
                        logger.info(
                            "Injected context recovery (%d turns) for session %s",
                            len(recent_turns),
                            sanitize_log_fn(ctx.resolved_session_id),
                        )
                    continue
                raise
    finally:
        if opening_task is not None and not opening_task.done():
            opening_task.cancel()
            await asyncio.gather(opening_task, return_exceptions=True)


async def silence_nudge_task(
    live_request_queue,
    session_alive: asyncio.Event,
    silence_state: SilenceState,
    ctx: SessionInitContext | None = None,
) -> None:
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
            if silence_state.user_turn_active:
                continue
            elapsed = now - silence_state.user_spoke_at
            if (
                elapsed >= RESPONSE_LATENCY_FILLER_SECONDS
                and silence_state.response_nudge_count == 0
            ):
                silence_state.response_nudge_count = 1
                try:
                    logger.info(
                        "Sending first response-latency nudge session_waiting=%s media_pending=%s",
                        silence_state.awaiting_agent_response,
                        silence_state.pending_media_analysis,
                    )
                    live_request_queue.send_content(types_mod.Content(parts=[
                        types_mod.Part(text=_response_latency_prompt(
                            ctx=ctx,
                            silence_state=silence_state,
                        ))
                    ]))
                except Exception:
                    logger.error(
                        "silence_nudge_task: failed to send first response-latency nudge session=%s",
                        sanitize_log_fn(ctx.resolved_session_id) if ctx is not None else "unknown",
                        exc_info=True,
                    )
                    break
                continue
            if (
                elapsed >= RESPONSE_LATENCY_REASSURE_SECONDS
                and silence_state.response_nudge_count == 1
            ):
                silence_state.response_nudge_count = 2
                try:
                    logger.info(
                        "Sending second response-latency nudge session_waiting=%s media_pending=%s",
                        silence_state.awaiting_agent_response,
                        silence_state.pending_media_analysis,
                    )
                    reassure_text = (
                        "[System: Over 5 seconds have passed while you are still "
                        "checking the customer's photo or video. Reassure them "
                        "briefly right now, for example 'I'm still with you, "
                        "still checking the video now.' Then continue.]"
                        if silence_state.pending_media_analysis
                        else
                        "[System: Over 5 seconds have passed since the customer "
                        "spoke. Say 'I'm still with you, just a moment longer' "
                        "to reassure them you haven't disconnected.]"
                    )
                    live_request_queue.send_content(types_mod.Content(parts=[
                        types_mod.Part(text=reassure_text)
                    ]))
                except Exception:
                    logger.error(
                        "silence_nudge_task: failed to send second response-latency nudge session=%s",
                        sanitize_log_fn(ctx.resolved_session_id) if ctx is not None else "unknown",
                        exc_info=True,
                    )
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
