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
    # Agent response latency tracking (independent from customer-silence)
    awaiting_agent_response: bool = False
    user_spoke_at: float = 0.0
    response_nudge_count: int = 0
