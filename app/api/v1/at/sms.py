"""AT SMS route handlers (thin: parse → service → respond).

All business logic lives in service_sms.py.
AT SDK calls go through providers.py.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from .security import verify_at_webhook
from .settings import AT_SMS_ENABLED, AT_SMS_SENDER_ID, AT_VIRTUAL_NUMBER
from . import service_sms
from . import providers
from . import bridge_text
from . import campaign_analytics
from . import service_voice
from .models import SendSMSRequest, CampaignSMSRequest

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_at_webhook)])

_INBOUND_FROM_KEYS = ("from", "source", "phoneNumber")
_INBOUND_TO_KEYS = ("to", "destination")
_INBOUND_TEXT_KEYS = ("text", "body", "message")
_INBOUND_DATE_KEYS = ("date", "createdAt")
_INBOUND_ID_KEYS = ("id", "messageId")
_DLR_STATUS_KEYS = ("status", "deliveryStatus", "messageStatus")
_DLR_MESSAGE_ID_KEYS = ("messageId", "message_id", "id")
_DLR_RECIPIENT_KEYS = ("phoneNumber", "number", "to", "recipient", "destination")
_DLR_REASON_KEYS = ("failureReason", "failure_reason", "errorMessage")
_DLR_NETWORK_KEYS = ("networkCode", "network_code")


def _first_present(payload: dict[str, str], *keys: str) -> str:
    for key in keys:
        raw_value = payload.get(key, "")
        value = raw_value.strip() if isinstance(raw_value, str) else ""
        if value:
            return value
    return ""


async def _read_form_payload(request: Request) -> dict[str, str]:
    form = await request.form()
    payload: dict[str, str] = {}
    for key in form.keys():
        value = form.get(key)
        if isinstance(value, str):
            payload[key] = value
        elif value is not None:
            payload[key] = str(value)
    return payload


def _delivery_report_response(
    *,
    payload: dict[str, str],
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
) -> dict[str, Any]:
    status = _first_present(payload, *_DLR_STATUS_KEYS)
    message_id = _first_present(payload, *_DLR_MESSAGE_ID_KEYS)
    recipient = _first_present(payload, *_DLR_RECIPIENT_KEYS)
    reason = _first_present(payload, *_DLR_REASON_KEYS)
    network_code = _first_present(payload, *_DLR_NETWORK_KEYS)
    event_type = service_sms.delivery_report_event_type(status)
    synthesized_event_id = ""
    if message_id and event_type:
        synthesized_event_id = f"sms-dlr:{message_id}:{event_type}"

    metadata = {
        "raw_status": status,
        "failure_reason": reason,
        "network_code": network_code,
        "payload": payload,
    }
    campaign_id = (
        campaign_analytics.record_delivery_report(
            tenant_id=tenant_id,
            company_id=company_id,
            recipient=recipient,
            status=event_type or "",
            message_id=message_id or None,
            event_id=synthesized_event_id or None,
            metadata=metadata,
        )
        if event_type and recipient
        else None
    )
    response_status = "ok" if event_type else "ignored"
    logger.info(
        "AT SMS delivery report received",
        extra={
            "event_type": event_type or "",
            "message_id": message_id,
            "recipient": recipient,
            "campaign_id": campaign_id,
        },
    )
    return {
        "status": response_status,
        "event_type": event_type,
        "message_id": message_id or None,
        "recipient": recipient or None,
        "campaign_id": campaign_id,
    }


@router.post("/sms/callback")
async def sms_callback(
    request: Request,
) -> dict:
    """AT inbound SMS webhook. Also accepts misrouted SMS delivery reports."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled"}

    payload = await _read_form_payload(request)
    if service_sms.is_delivery_report_payload(payload):
        return _delivery_report_response(payload=payload)

    from_ = _first_present(payload, *_INBOUND_FROM_KEYS)
    to = _first_present(payload, *_INBOUND_TO_KEYS)
    text = _first_present(payload, *_INBOUND_TEXT_KEYS)
    date = _first_present(payload, *_INBOUND_DATE_KEYS)
    id = _first_present(payload, *_INBOUND_ID_KEYS)

    tenant_id, company_id = service_voice.resolve_tenant_context(to or AT_VIRTUAL_NUMBER)

    # Generate AI response via Gemini text bridge
    try:
        ai_reply = await bridge_text.query_text(user_message=text)
        truncated = service_sms.truncate_sms(ai_reply)
    except Exception:
        logger.warning("AT SMS AI reply generation failed; using fallback reply", exc_info=True)
        truncated = service_sms.fallback_sms_reply()

    if not truncated:
        truncated = service_sms.fallback_sms_reply()

    # Send reply via AT SMS
    try:
        result = await providers.send_sms(
            message=truncated,
            recipients=[from_],
            sender_id=AT_SMS_SENDER_ID or None,
        )
    except Exception:
        logger.warning("AT SMS callback reply failed", exc_info=True)
        return {
            "status": "error",
            "code": "AT_SMS_SEND_FAILED",
            "reply": truncated,
            "campaign_id": None,
            "detail": "SMS provider unavailable",
        }

    campaign_id = campaign_analytics.record_inbound_reply(
        channel="sms",
        tenant_id=tenant_id,
        company_id=company_id,
        recipient=from_,
        message=text,
    )
    logger.info(
        "AT SMS reply sent",
        extra={"reply_length": len(truncated)},
    )
    return {
        "status": "ok",
        "reply": truncated,
        "result": result,
        "campaign_id": campaign_id,
    }


@router.post("/sms/delivery-report")
@router.post("/sms/dlr")
async def sms_delivery_report(request: Request) -> dict:
    """AT SMS delivery report webhook."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled"}
    payload = await _read_form_payload(request)
    return _delivery_report_response(payload=payload)


@router.post("/sms/send")
async def send_sms(req: SendSMSRequest) -> dict:
    """Send a single outbound SMS."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled", "detail": "SMS channel is disabled"}
    truncated = service_sms.truncate_sms(req.message)
    try:
        result = await providers.send_sms(
            message=truncated,
            recipients=[req.to],
            sender_id=req.sender_id or AT_SMS_SENDER_ID or None,
        )
    except Exception as exc:
        logger.warning("AT SMS send failed", exc_info=True)
        raise HTTPException(status_code=502, detail="SMS provider unavailable") from exc

    campaign_id = campaign_analytics.record_outbound_campaign(
        channel="sms",
        tenant_id=req.tenant_id,
        company_id=req.company_id,
        recipients=[req.to],
        message=truncated,
        provider_result=result if isinstance(result, dict) else {},
        campaign_id=req.campaign_id,
        campaign_name=req.campaign_name,
    )
    return {"status": "ok", "result": result, "campaign_id": campaign_id}


@router.post("/sms/campaign")
async def sms_campaign(req: CampaignSMSRequest) -> dict:
    """Bulk SMS campaign to a recipient list."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled", "detail": "SMS channel is disabled"}
    truncated = service_sms.truncate_sms(req.message)
    try:
        result = await providers.send_sms(
            message=truncated,
            recipients=req.to,
            sender_id=req.sender_id or AT_SMS_SENDER_ID or None,
        )
    except Exception as exc:
        logger.warning("AT SMS campaign failed", exc_info=True, extra={"to_count": len(req.to)})
        raise HTTPException(status_code=502, detail="SMS provider unavailable") from exc

    campaign_id = campaign_analytics.record_outbound_campaign(
        channel="sms",
        tenant_id=req.tenant_id,
        company_id=req.company_id,
        recipients=req.to,
        message=truncated,
        provider_result=result if isinstance(result, dict) else {},
        campaign_id=req.campaign_id,
        campaign_name=req.campaign_name,
    )
    return {"status": "ok", "result": result, "campaign_id": campaign_id}
