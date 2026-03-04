"""AT SMS route handlers (thin: parse → service → respond).

All business logic lives in service_sms.py.
AT SDK calls go through providers.py.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException

from app.configs import sanitize_log

from .security import verify_at_webhook
from .settings import AT_SMS_ENABLED, AT_VIRTUAL_NUMBER
from . import service_sms
from . import providers
from . import bridge_text
from . import campaign_analytics
from . import service_voice
from .models import SendSMSRequest, CampaignSMSRequest
from app.tools.pii_redaction import redact_pii

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_at_webhook)])


@router.post("/sms/callback")
async def sms_callback(
    from_: Annotated[str, Form(alias="from")] = "",
    to: Annotated[str, Form()] = "",
    text: Annotated[str, Form()] = "",
    date: Annotated[str, Form()] = "",
    id: Annotated[str, Form()] = "",
) -> dict:
    """AT inbound SMS webhook. Generates AI reply and sends back."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled"}

    tenant_id, company_id = service_voice.resolve_tenant_context(to or AT_VIRTUAL_NUMBER)

    # Generate AI response via Gemini text bridge
    ai_reply = await bridge_text.query_text(user_message=text)
    truncated = service_sms.truncate_sms(ai_reply)

    if not truncated:
        truncated = "Thanks for your message. How can I help you today?"

    # Send reply via AT SMS
    try:
        result = await providers.send_sms(message=truncated, recipients=[from_])
    except Exception as exc:
        logger.warning(
            "AT SMS callback reply failed",
            exc_info=True,
            extra={
                "from": sanitize_log(redact_pii(from_)),
                "to": sanitize_log(redact_pii(to)),
            },
        )
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
        extra={
            "from": sanitize_log(redact_pii(from_)),
            "to": sanitize_log(redact_pii(to)),
            "reply_length": len(truncated),
        },
    )
    return {
        "status": "ok",
        "reply": truncated,
        "result": result,
        "campaign_id": campaign_id,
    }


@router.post("/sms/send")
async def send_sms(req: SendSMSRequest) -> dict:
    """Send a single outbound SMS."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled", "detail": "SMS channel is disabled"}
    truncated = service_sms.truncate_sms(req.message)
    try:
        result = await providers.send_sms(message=truncated, recipients=[req.to])
    except Exception as exc:
        logger.warning(
            "AT SMS send failed",
            exc_info=True,
            extra={"to": sanitize_log(redact_pii(req.to))},
        )
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
        result = await providers.send_sms(message=truncated, recipients=req.to)
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
