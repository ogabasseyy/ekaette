"""In-process voice operations analytics for demo and operator dashboards."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(ts: float | None = None) -> str:
    if ts is None:
        return _utc_now().isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalize_scope(value: str, default: str) -> str:
    normalized = (value or "").strip()
    return normalized or default


@dataclass(slots=True)
class VoiceSessionState:
    session_id: str
    tenant_id: str
    company_id: str
    channel: str
    status: str
    started_at: str
    updated_at: str
    ended_at: str | None = None
    duration_seconds: float = 0.0
    caller_phone: str = ""
    transfer_count: int = 0
    callback_requested: bool = False
    callback_triggered: bool = False
    transcript_messages_total: int = 0
    transcript_preview: str = ""
    agent_path: list[str] = field(default_factory=lambda: ["ekaette_router"])


_lock = threading.Lock()
_sessions: dict[str, VoiceSessionState] = {}


def reset_state() -> None:
    with _lock:
        _sessions.clear()


def start_session(
    *,
    session_id: str,
    tenant_id: str,
    company_id: str,
    channel: str,
    started_at: float | None = None,
    caller_phone: str = "",
) -> None:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        logger.warning("voice_analytics.start_session skipped empty session_id")
        return
    now_iso = _to_iso(started_at)
    with _lock:
        if normalized_session_id in _sessions:
            logger.warning(
                "voice_analytics.start_session overwriting existing session_id=%s",
                normalized_session_id,
            )
        _sessions[normalized_session_id] = VoiceSessionState(
            session_id=normalized_session_id,
            tenant_id=_normalize_scope(tenant_id, "public"),
            company_id=_normalize_scope(company_id, "ekaette-electronics"),
            channel=_normalize_scope(channel, "voice"),
            status="active",
            started_at=now_iso,
            updated_at=now_iso,
            caller_phone=(caller_phone or "").strip(),
        )


def end_session(
    *,
    session_id: str,
    ended_at: float | None = None,
    status: str = "completed",
) -> None:
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        ended_iso = _to_iso(ended_at)
        session.ended_at = ended_iso
        session.updated_at = ended_iso
        session.status = _normalize_scope(status, "completed")
        started_dt = _parse_iso(session.started_at)
        ended_dt = _parse_iso(ended_iso)
        if started_dt is not None and ended_dt is not None:
            duration = max(0.0, (ended_dt - started_dt).total_seconds())
            session.duration_seconds = round(duration, 2)


def record_transcript(
    *,
    session_id: str,
    role: str,
    text: str,
    partial: bool,
) -> None:
    normalized_text = (text or "").strip()
    if partial or not normalized_text:
        return
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        session.transcript_messages_total += 1
        session.updated_at = _to_iso()
        if not session.transcript_preview:
            speaker = "Customer" if role == "user" else "Ekaette"
            session.transcript_preview = f"{speaker}: {normalized_text}"


def record_transfer(
    *,
    session_id: str,
    target_agent: str,
) -> None:
    normalized_target = (target_agent or "").strip()
    if not normalized_target:
        return
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        session.transfer_count += 1
        if normalized_target not in session.agent_path:
            session.agent_path.append(normalized_target)
        session.updated_at = _to_iso()


def mark_callback_requested(
    *,
    session_id: str,
    phone: str = "",
) -> None:
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        session.callback_requested = True
        if phone.strip():
            session.caller_phone = phone.strip()
        session.updated_at = _to_iso()


def mark_callback_triggered(
    *,
    tenant_id: str,
    company_id: str,
    phone: str,
) -> None:
    normalized_phone = (phone or "").strip()
    if not normalized_phone:
        return
    normalized_tenant = _normalize_scope(tenant_id, "public")
    normalized_company = _normalize_scope(company_id, "ekaette-electronics")
    with _lock:
        candidates = [
            session
            for session in _sessions.values()
            if session.tenant_id == normalized_tenant
            and session.company_id == normalized_company
            and session.caller_phone == normalized_phone
        ]
        if not candidates:
            return
        candidates.sort(key=lambda item: item.updated_at, reverse=True)
        candidates[0].callback_triggered = True
        candidates[0].updated_at = _to_iso()


def get_session_snapshot(session_id: str) -> dict[str, Any] | None:
    normalized_session_id = (session_id or "").strip()
    if not normalized_session_id:
        return None
    with _lock:
        session = _sessions.get(normalized_session_id)
        if session is None:
            return None
        return {
            "session_id": session.session_id,
            "tenant_id": session.tenant_id,
            "company_id": session.company_id,
            "channel": session.channel,
            "status": session.status,
            "started_at": session.started_at,
            "updated_at": session.updated_at,
            "ended_at": session.ended_at,
            "duration_seconds": int(round(session.duration_seconds)),
            "caller_phone": session.caller_phone,
            "transfer_count": session.transfer_count,
            "callback_requested": session.callback_requested,
            "callback_triggered": session.callback_triggered,
            "transcript_messages_total": session.transcript_messages_total,
            "transcript_preview": session.transcript_preview,
            "agent_path": list(session.agent_path),
        }


def _filtered_sessions(
    *,
    tenant_id: str,
    company_id: str,
    days: int,
) -> list[VoiceSessionState]:
    cutoff = _utc_now() - timedelta(days=max(1, days))
    sort_fallback_now = _utc_now()
    normalized_tenant = _normalize_scope(tenant_id, "public")
    normalized_company = _normalize_scope(company_id, "ekaette-electronics")
    ranked_sessions: list[tuple[datetime, VoiceSessionState]] = []
    with _lock:
        for session in _sessions.values():
            if session.tenant_id != normalized_tenant:
                continue
            if session.company_id != normalized_company:
                continue
            parsed_updated = _parse_iso(session.updated_at)
            if parsed_updated is None:
                logger.debug(
                    "voice_analytics._filtered_sessions: unparseable updated_at for session_id=%s",
                    session.session_id,
                )
                parsed_updated = sort_fallback_now
            if parsed_updated >= cutoff:
                ranked_sessions.append((parsed_updated, session))
    ranked_sessions.sort(key=lambda item: item[0], reverse=True)
    return [session for _, session in ranked_sessions]


def list_recent_calls(
    *,
    tenant_id: str,
    company_id: str,
    days: int = 30,
    limit: int = 10,
) -> list[dict[str, Any]]:
    sessions = _filtered_sessions(tenant_id=tenant_id, company_id=company_id, days=days)
    snapshots: list[dict[str, Any]] = []
    for session in sessions[: max(1, min(limit, 100))]:
        snapshots.append({
            "session_id": session.session_id,
            "tenant_id": session.tenant_id,
            "company_id": session.company_id,
            "channel": session.channel,
            "status": session.status,
            "started_at": session.started_at,
            "updated_at": session.updated_at,
            "ended_at": session.ended_at,
            "duration_seconds": int(round(session.duration_seconds)),
            "caller_phone": session.caller_phone,
            "transfer_count": session.transfer_count,
            "callback_requested": session.callback_requested,
            "callback_triggered": session.callback_triggered,
            "transcript_messages_total": session.transcript_messages_total,
            "transcript_preview": session.transcript_preview,
            "agent_path": list(session.agent_path),
        })
    return snapshots


def overview_snapshot(
    *,
    tenant_id: str,
    company_id: str,
    days: int = 30,
) -> dict[str, Any]:
    sessions = _filtered_sessions(tenant_id=tenant_id, company_id=company_id, days=days)
    calls_total = len(sessions)
    calls_completed = sum(1 for session in sessions if session.status == "completed")
    total_duration = sum(session.duration_seconds for session in sessions)
    transfers_total = sum(session.transfer_count for session in sessions)
    callback_requests_total = sum(1 for session in sessions if session.callback_requested)
    callback_triggered_total = sum(1 for session in sessions if session.callback_triggered)
    transcript_sessions = sum(1 for session in sessions if session.transcript_messages_total > 0)

    avg_duration = (total_duration / calls_total) if calls_total else 0.0
    transfer_rate = (transfers_total / calls_total) if calls_total else 0.0
    transcript_coverage_rate = (transcript_sessions / calls_total) if calls_total else 0.0

    return {
        "window_days": max(1, days),
        "calls_total": calls_total,
        "calls_completed": calls_completed,
        "avg_duration_seconds": round(avg_duration, 1),
        "transfers_total": transfers_total,
        "transfer_rate": round(transfer_rate, 4),
        "callback_requests_total": callback_requests_total,
        "callback_triggered_total": callback_triggered_total,
        "transcript_coverage_rate": round(transcript_coverage_rate, 4),
    }
