"""AT SMS route handlers (thin: parse → service → respond).

All business logic lives in service_sms.py.
AT SDK calls go through providers.py.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form

from .security import verify_at_webhook
from .settings import AT_SMS_ENABLED, AT_VIRTUAL_NUMBER
from . import service_sms
from . import providers
from . import bridge_text
from .models import SendSMSRequest, CampaignSMSRequest

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

    # Generate AI response via Gemini text bridge
    ai_reply = await bridge_text.query_text(user_message=text)
    truncated = service_sms.truncate_sms(ai_reply)

    if not truncated:
        truncated = "Thanks for your message. How can I help you today?"

    # Send reply via AT SMS
    result = await providers.send_sms(message=truncated, recipients=[from_])
    logger.info(
        "AT SMS reply sent",
        extra={"from": from_, "to": to, "reply_length": len(truncated)},
    )
    return {"status": "ok", "reply": truncated, "result": result}


@router.post("/sms/send")
async def send_sms(req: SendSMSRequest) -> dict:
    """Send a single outbound SMS."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled", "detail": "SMS channel is disabled"}
    truncated = service_sms.truncate_sms(req.message)
    result = await providers.send_sms(message=truncated, recipients=[req.to])
    return {"status": "ok", "result": result}


@router.post("/sms/campaign")
async def sms_campaign(req: CampaignSMSRequest) -> dict:
    """Bulk SMS campaign to a recipient list."""
    if not AT_SMS_ENABLED:
        return {"status": "disabled", "detail": "SMS channel is disabled"}
    truncated = service_sms.truncate_sms(req.message)
    result = await providers.send_sms(message=truncated, recipients=req.to)
    return {"status": "ok", "result": result}
