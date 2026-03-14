"""Shared models for realtime websocket streaming."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket


@dataclass
class SessionInitContext:
    """Canonical context produced during websocket session startup."""

    websocket: WebSocket
    user_id: str
    resolved_session_id: str
    client_ip: str
    model_name: str
    is_native_audio: bool
    industry: str
    session_industry: str
    company_id: str
    tenant_id: str
    requested_template_id: str | None
    session_state: dict[str, object]
    session_voice: str
    manual_vad_active: bool
    run_config: Any
    live_session_resumption_enabled: bool = False
    caller_phone: str = ""


@dataclass
class SilenceState:
    """Mutable silence/backoff state shared across streaming tasks."""

    last_client_activity: float
    silence_nudge_count: int
    agent_busy: bool
    silence_nudge_due_at: float
    silence_nudge_interval: float
    # True only while the assistant is actively outputting audio/text for the
    # current turn. This is intentionally narrower than ``agent_busy``.
    assistant_output_active: bool = False
    # Agent response latency tracking (independent from customer-silence)
    awaiting_agent_response: bool = False
    user_spoke_at: float = 0.0
    response_nudge_count: int = 0
    # True while a new user utterance is in progress, based on
    # transcription/VAD signals rather than raw RTP packet arrival.
    user_turn_active: bool = False
    # True when cross-channel media has just been injected and the caller is
    # waiting for a visual analysis acknowledgement or result.
    pending_media_analysis: bool = False
    # Greeting lock: suppress caller audio until the first agent turn completes,
    # matching the SIP/WA bridge pattern of non-interruptible greetings.
    greeting_lock_active: bool = True
    greeting_lock_deadline: float = 0.0  # monotonic deadline; 0 = not yet set
