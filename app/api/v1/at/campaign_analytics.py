"""In-process campaign analytics for AT SMS/voice and payment conversion events.

This module is intentionally storage-agnostic for hackathon speed.
Production deployments can replace this in-memory store with Firestore/Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import heapq
import re
import threading
import uuid
from typing import Any, Literal

CampaignChannel = Literal["sms", "voice", "omni"]

_SUCCESS_STATUS_HINTS = ("success", "queued", "sent", "accepted", "processing", "active")
_FAILURE_STATUS_HINTS = ("failed", "error", "invalid", "rejected", "blocked")

_PHONE_CLEAN_RE = re.compile(r"[^0-9+]", re.ASCII)


@dataclass(slots=True)
class CampaignState:
    campaign_id: str
    channel: CampaignChannel
    tenant_id: str
    company_id: str
    campaign_name: str
    message: str
    created_at: str
    updated_at: str
    recipients: set[str] = field(default_factory=set)
    sent_total: int = 0
    delivered_total: int = 0
    failed_total: int = 0
    replies_total: int = 0
    conversions_total: int = 0
    revenue_kobo: int = 0
    payments_initialized_total: int = 0
    payments_success_total: int = 0


_lock = threading.Lock()
_campaigns: dict[str, CampaignState] = {}
_events: list[dict[str, Any]] = []
_seen_event_ids: dict[str, float] = {}
_recipient_last_campaign: dict[tuple[str, str, str], str] = {}
_message_id_campaign: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_channel(channel: str) -> CampaignChannel:
    normalized = (channel or "").strip().lower()
    if normalized in {"sms", "voice", "omni"}:
        return normalized  # type: ignore[return-value]
    return "omni"


def _normalize_recipient(recipient: str) -> str:
    cleaned = _PHONE_CLEAN_RE.sub("", (recipient or "").strip())
    return cleaned.lower()


def _normalize_message_id(message_id: str) -> str:
    return (message_id or "").strip()


def _build_campaign_id(channel: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"cmp-{channel}-{ts}-{suffix}"


def _event_deduped(event_id: str | None) -> bool:
    if not event_id:
        return False
    now_ts = datetime.now(timezone.utc).timestamp()
    with _lock:
        if event_id in _seen_event_ids:
            return True
        _seen_event_ids[event_id] = now_ts
        if len(_seen_event_ids) > 10_000:
            # Deterministically evict the oldest entries first.
            overflow = len(_seen_event_ids) - 5_000
            oldest_ids = heapq.nsmallest(
                overflow,
                _seen_event_ids.items(),
                key=lambda item: item[1],
            )
            for stale_event_id, _ in oldest_ids:
                _seen_event_ids.pop(stale_event_id, None)
        return False


def _ensure_campaign(
    *,
    channel: str,
    tenant_id: str,
    company_id: str,
    message: str,
    campaign_id: str | None = None,
    campaign_name: str | None = None,
) -> CampaignState:
    normalized_channel = _normalize_channel(channel)
    normalized_tenant = (tenant_id or "public").strip() or "public"
    normalized_company = (company_id or "ekaette-electronics").strip() or "ekaette-electronics"
    resolved_campaign_id = (campaign_id or "").strip() or _build_campaign_id(normalized_channel)
    now = _now_iso()
    with _lock:
        existing = _campaigns.get(resolved_campaign_id)
        if existing is not None and (
            existing.tenant_id != normalized_tenant or existing.company_id != normalized_company
        ):
            # Prevent campaign ID collisions from polluting another tenant/company scope.
            resolved_campaign_id = _build_campaign_id(normalized_channel)
            existing = None
        if existing is not None:
            existing.updated_at = now
            if message and not existing.message:
                existing.message = message
            if campaign_name and campaign_name.strip():
                existing.campaign_name = campaign_name.strip()
            return existing

        state = CampaignState(
            campaign_id=resolved_campaign_id,
            channel=normalized_channel,
            tenant_id=normalized_tenant,
            company_id=normalized_company,
            campaign_name=(campaign_name or "").strip() or f"{normalized_channel.upper()} Campaign",
            message=message,
            created_at=now,
            updated_at=now,
        )
        _campaigns[resolved_campaign_id] = state
        return state


def _status_success(status: str) -> bool:
    lowered = (status or "").strip().lower()
    return any(hint in lowered for hint in _SUCCESS_STATUS_HINTS)


def _status_failed(status: str) -> bool:
    lowered = (status or "").strip().lower()
    return any(hint in lowered for hint in _FAILURE_STATUS_HINTS)


def _extract_recipient_statuses(provider_result: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}

    sms_data = provider_result.get("SMSMessageData")
    sms_recipients = sms_data.get("Recipients") if isinstance(sms_data, dict) else None
    if isinstance(sms_recipients, list):
        for item in sms_recipients:
            if not isinstance(item, dict):
                continue
            recipient = _normalize_recipient(str(item.get("number") or item.get("phoneNumber") or ""))
            if not recipient:
                continue
            statuses[recipient] = str(item.get("status") or "")

    entries = provider_result.get("entries")
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            recipient = _normalize_recipient(str(item.get("phoneNumber") or item.get("number") or ""))
            if not recipient:
                continue
            statuses[recipient] = str(item.get("status") or item.get("state") or "")

    return statuses


def _index_provider_message_ids(provider_result: dict[str, Any], campaign_id: str) -> None:
    sms_data = provider_result.get("SMSMessageData")
    sms_recipients = sms_data.get("Recipients") if isinstance(sms_data, dict) else None
    if not isinstance(sms_recipients, list):
        return

    with _lock:
        for item in sms_recipients:
            if not isinstance(item, dict):
                continue
            message_id = _normalize_message_id(str(item.get("messageId") or ""))
            if message_id:
                _message_id_campaign[message_id] = campaign_id


def _append_event(event: dict[str, Any]) -> None:
    with _lock:
        _events.append(event)
        if len(_events) > 10_000:
            del _events[:5_000]


def _update_recipient_index(tenant_id: str, company_id: str, recipients: list[str], campaign_id: str) -> None:
    with _lock:
        for recipient in recipients:
            normalized_recipient = _normalize_recipient(recipient)
            if normalized_recipient:
                _recipient_last_campaign[(tenant_id, company_id, normalized_recipient)] = campaign_id


def _campaign_scope_for_id(campaign_id: str) -> tuple[str, str] | None:
    normalized_campaign_id = (campaign_id or "").strip()
    if not normalized_campaign_id:
        return None
    with _lock:
        state = _campaigns.get(normalized_campaign_id)
        if state is None:
            return None
        return state.tenant_id, state.company_id


def _compute_kpis(state: CampaignState) -> dict[str, float]:
    sent = max(state.sent_total, 0)
    delivered = max(state.delivered_total, 0)
    replies = max(state.replies_total, 0)
    conversions = max(state.conversions_total, 0)

    delivery_rate = (delivered / sent) if sent else 0.0
    engagement_rate = (replies / delivered) if delivered else 0.0
    conversion_rate = (conversions / delivered) if delivered else 0.0
    avg_order_value_kobo = (state.revenue_kobo / conversions) if conversions else 0.0

    return {
        "delivery_rate": round(delivery_rate, 4),
        "engagement_rate": round(engagement_rate, 4),
        "conversion_rate": round(conversion_rate, 4),
        "avg_order_value_kobo": round(avg_order_value_kobo, 2),
    }


def campaign_snapshot(campaign_id: str) -> dict[str, Any] | None:
    with _lock:
        state = _campaigns.get(campaign_id)
        if state is None:
            return None
        snapshot = {
            "campaign_id": state.campaign_id,
            "channel": state.channel,
            "tenant_id": state.tenant_id,
            "company_id": state.company_id,
            "campaign_name": state.campaign_name,
            "message": state.message,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "recipients_total": len(state.recipients),
            "sent_total": state.sent_total,
            "delivered_total": state.delivered_total,
            "failed_total": state.failed_total,
            "replies_total": state.replies_total,
            "conversions_total": state.conversions_total,
            "revenue_kobo": state.revenue_kobo,
            "payments_initialized_total": state.payments_initialized_total,
            "payments_success_total": state.payments_success_total,
        }
        snapshot.update(_compute_kpis(state))
        return snapshot


def list_campaign_snapshots(
    *,
    tenant_id: str,
    company_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with _lock:
        items = [
            state
            for state in _campaigns.values()
            if state.tenant_id == tenant_id and state.company_id == company_id
        ]
    items.sort(key=lambda state: state.updated_at, reverse=True)
    snapshots: list[dict[str, Any]] = []
    for state in items[: max(1, min(limit, 200))]:
        snapshot = campaign_snapshot(state.campaign_id)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def overview_snapshot(
    *,
    tenant_id: str,
    company_id: str,
    days: int = 30,
) -> dict[str, Any]:
    window_start = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    with _lock:
        scoped = [
            state
            for state in _campaigns.values()
            if state.tenant_id == tenant_id and state.company_id == company_id
        ]

    filtered: list[CampaignState] = []
    for state in scoped:
        try:
            created = datetime.fromisoformat(state.created_at)
        except ValueError:
            created = datetime.now(timezone.utc)
        if created >= window_start:
            filtered.append(state)

    total_sent = sum(item.sent_total for item in filtered)
    total_delivered = sum(item.delivered_total for item in filtered)
    total_failed = sum(item.failed_total for item in filtered)
    total_replies = sum(item.replies_total for item in filtered)
    total_conversions = sum(item.conversions_total for item in filtered)
    total_revenue_kobo = sum(item.revenue_kobo for item in filtered)

    delivery_rate = (total_delivered / total_sent) if total_sent else 0.0
    engagement_rate = (total_replies / total_delivered) if total_delivered else 0.0
    conversion_rate = (total_conversions / total_delivered) if total_delivered else 0.0

    return {
        "window_days": days,
        "campaigns_total": len(filtered),
        "total_sent": total_sent,
        "total_delivered": total_delivered,
        "total_failed": total_failed,
        "total_replies": total_replies,
        "total_conversions": total_conversions,
        "total_revenue_kobo": total_revenue_kobo,
        "total_revenue_naira": round(total_revenue_kobo / 100, 2),
        "delivery_rate": round(delivery_rate, 4),
        "engagement_rate": round(engagement_rate, 4),
        "conversion_rate": round(conversion_rate, 4),
    }


def record_outbound_campaign(
    *,
    channel: str,
    tenant_id: str,
    company_id: str,
    recipients: list[str],
    message: str,
    provider_result: dict[str, Any],
    campaign_id: str | None = None,
    campaign_name: str | None = None,
) -> str:
    state = _ensure_campaign(
        channel=channel,
        tenant_id=tenant_id,
        company_id=company_id,
        message=message,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
    )

    normalized_recipients = [recipient for recipient in recipients if _normalize_recipient(recipient)]
    status_map = _extract_recipient_statuses(provider_result)

    failed = 0
    for recipient in normalized_recipients:
        normalized_recipient = _normalize_recipient(recipient)
        status = status_map.get(normalized_recipient, "")
        if _status_failed(status):
            failed += 1

    now = _now_iso()
    with _lock:
        state.updated_at = now
        state.sent_total += len(normalized_recipients)
        state.failed_total += failed
        for recipient in normalized_recipients:
            state.recipients.add(_normalize_recipient(recipient))

    _update_recipient_index(state.tenant_id, state.company_id, normalized_recipients, state.campaign_id)
    _index_provider_message_ids(provider_result, state.campaign_id)

    _append_event(
        {
            "timestamp": now,
            "event_type": "sent",
            "channel": state.channel,
            "tenant_id": state.tenant_id,
            "company_id": state.company_id,
            "campaign_id": state.campaign_id,
            "sent": len(normalized_recipients),
            "delivered": 0,
            "failed": failed,
        }
    )
    return state.campaign_id


def record_inbound_reply(
    *,
    channel: str,
    tenant_id: str,
    company_id: str,
    recipient: str,
    message: str,
    campaign_id: str | None = None,
) -> str | None:
    normalized_recipient = _normalize_recipient(recipient)
    if not normalized_recipient:
        return None

    resolved_campaign_id = (campaign_id or "").strip()
    if not resolved_campaign_id:
        with _lock:
            resolved_campaign_id = _recipient_last_campaign.get((tenant_id, company_id, normalized_recipient), "")
    if not resolved_campaign_id:
        return None

    state = _ensure_campaign(
        channel=channel,
        tenant_id=tenant_id,
        company_id=company_id,
        message="",
        campaign_id=resolved_campaign_id,
    )

    now = _now_iso()
    with _lock:
        state.updated_at = now
        state.replies_total += 1

    _append_event(
        {
            "timestamp": now,
            "event_type": "reply",
            "channel": state.channel,
            "tenant_id": state.tenant_id,
            "company_id": state.company_id,
            "campaign_id": state.campaign_id,
            "recipient": normalized_recipient,
            "message": message,
        }
    )
    return state.campaign_id


def record_event(
    *,
    event_type: str,
    channel: str,
    tenant_id: str,
    company_id: str,
    campaign_id: str | None = None,
    campaign_name: str | None = None,
    recipient: str | None = None,
    amount_kobo: int | None = None,
    reference: str | None = None,
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    if _event_deduped(event_id):
        return (campaign_id or "").strip()

    state = _ensure_campaign(
        channel=channel,
        tenant_id=tenant_id,
        company_id=company_id,
        message="",
        campaign_id=campaign_id,
        campaign_name=campaign_name,
    )
    normalized_event_type = (event_type or "").strip().lower()

    now = _now_iso()
    with _lock:
        state.updated_at = now
        if normalized_event_type == "sent":
            state.sent_total += 1
        elif normalized_event_type == "delivered":
            state.delivered_total += 1
        elif normalized_event_type == "failed":
            state.failed_total += 1
        elif normalized_event_type == "reply":
            state.replies_total += 1
        elif normalized_event_type == "payment_initialized":
            state.payments_initialized_total += 1
        elif normalized_event_type in {"payment_success", "conversion"}:
            state.conversions_total += 1
            state.payments_success_total += 1
            if isinstance(amount_kobo, int) and amount_kobo > 0:
                state.revenue_kobo += amount_kobo

    normalized_recipient = _normalize_recipient(recipient or "")
    if normalized_recipient:
        _update_recipient_index(state.tenant_id, state.company_id, [normalized_recipient], state.campaign_id)

    _append_event(
        {
            "timestamp": now,
            "event_type": normalized_event_type,
            "channel": state.channel,
            "tenant_id": state.tenant_id,
            "company_id": state.company_id,
            "campaign_id": state.campaign_id,
            "recipient": normalized_recipient or None,
            "amount_kobo": amount_kobo,
            "reference": reference,
            "metadata": metadata or {},
        }
    )

    return state.campaign_id


def resolve_campaign_for_delivery_report(
    *,
    tenant_id: str,
    company_id: str,
    recipient: str | None = None,
    message_id: str | None = None,
) -> str:
    normalized_message_id = _normalize_message_id(message_id or "")
    if normalized_message_id:
        with _lock:
            campaign_id = _message_id_campaign.get(normalized_message_id, "")
        if campaign_id:
            return campaign_id

    normalized_recipient = _normalize_recipient(recipient or "")
    if normalized_recipient:
        with _lock:
            return _recipient_last_campaign.get((tenant_id, company_id, normalized_recipient), "")
    return ""


def record_delivery_report(
    *,
    tenant_id: str,
    company_id: str,
    recipient: str,
    status: str,
    message_id: str | None = None,
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Apply an SMS delivery report to the matching outbound campaign when known."""
    event_type = (status or "").strip().lower()
    if event_type not in {"delivered", "failed"}:
        return None

    resolved_campaign_id = resolve_campaign_for_delivery_report(
        tenant_id=tenant_id,
        company_id=company_id,
        recipient=recipient,
        message_id=message_id,
    )
    if not resolved_campaign_id:
        return None

    resolved_scope = _campaign_scope_for_id(resolved_campaign_id)
    if resolved_scope is not None:
        tenant_id, company_id = resolved_scope

    return record_event(
        event_type=event_type,
        channel="sms",
        tenant_id=tenant_id,
        company_id=company_id,
        campaign_id=resolved_campaign_id,
        recipient=recipient,
        reference=message_id,
        event_id=event_id,
        metadata=metadata,
    )


def list_known_contacts(
    *,
    tenant_id: str,
    company_id: str,
) -> list[dict[str, str]]:
    """Return unique recipient phones scoped to tenant/company.

    Merges phones from campaign recipient sets and the recipient-last-campaign
    index.  Each entry: {phone, last_campaign_id, last_campaign_name, channel}.
    """
    contacts: dict[str, dict[str, str]] = {}

    with _lock:
        for (t_id, c_id, phone), campaign_id in _recipient_last_campaign.items():
            if t_id != tenant_id or c_id != company_id:
                continue
            state = _campaigns.get(campaign_id)
            if state is None:
                continue
            contacts[phone] = {
                "phone": phone,
                "last_campaign_id": campaign_id,
                "last_campaign_name": state.campaign_name,
                "channel": state.channel,
            }

        for state in _campaigns.values():
            if state.tenant_id != tenant_id or state.company_id != company_id:
                continue
            for phone in state.recipients:
                if phone not in contacts:
                    contacts[phone] = {
                        "phone": phone,
                        "last_campaign_id": state.campaign_id,
                        "last_campaign_name": state.campaign_name,
                        "channel": state.channel,
                    }

    return sorted(contacts.values(), key=lambda c: c["phone"])


def reset_state() -> None:
    """Testing helper: clear in-memory analytics state."""
    with _lock:
        _campaigns.clear()
        _events.clear()
        _seen_event_ids.clear()
        _recipient_last_campaign.clear()
        _message_id_campaign.clear()
