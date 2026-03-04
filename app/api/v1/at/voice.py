"""AT Voice route handlers (thin: parse → service → respond).

All business logic lives in service_voice.py.
AT SDK calls go through providers.py.
Idempotency for outbound/campaign/transfer via idempotency.py.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response

from .security import verify_at_webhook
from .settings import AT_VOICE_ENABLED, SIP_BRIDGE_ENDPOINT, AT_VIRTUAL_NUMBER
from . import service_voice
from . import providers
from . import campaign_analytics
from .models import OutboundCallRequest, CampaignCallRequest, TransferRequest
from .idempotency import (
    require_idempotency_key,
    idempotency_preflight,
    idempotency_commit,
    is_duplicate_callback,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_at_webhook)])


@router.post("/voice/callback")
async def voice_callback(
    isActive: Annotated[str, Form()] = "1",
    sessionId: Annotated[str, Form()] = "",
    direction: Annotated[str, Form()] = "Inbound",
    callerNumber: Annotated[str, Form()] = "",
    destinationNumber: Annotated[str, Form()] = "",
    durationInSeconds: Annotated[str, Form()] = "",
    amount: Annotated[str, Form()] = "",
) -> Response:
    """AT voice webhook callback. Returns XML actions.

    AT delivers callbacks at-least-once — dedup by sessionId+isActive.
    """
    if not AT_VOICE_ENABLED:
        return Response(content=service_voice.build_end_xml(), media_type="application/xml")

    # Callback dedup (at-least-once delivery safety)
    event_key = f"voice:{isActive}"
    if is_duplicate_callback(sessionId, event_key):
        logger.info("AT voice callback deduplicated")
        return Response(content=service_voice.build_end_xml(), media_type="application/xml")

    if isActive == "0":
        service_voice.log_call_ended(sessionId, callerNumber, durationInSeconds, amount)
        return Response(content=service_voice.build_end_xml(), media_type="application/xml")

    xml = service_voice.build_dial_xml(SIP_BRIDGE_ENDPOINT, AT_VIRTUAL_NUMBER)
    service_voice.log_call_bridged(sessionId, callerNumber, direction)
    return Response(content=xml, media_type="application/xml")


@router.post("/voice/call")
async def outbound_call(
    req: OutboundCallRequest,
    idempotency_key: str = Depends(require_idempotency_key),
) -> dict:
    """Initiate an outbound voice call. Requires Idempotency-Key header."""
    if not AT_VOICE_ENABLED:
        return {"status": "disabled", "detail": "Voice channel is disabled"}

    cached = idempotency_preflight(
        scope="at_voice_call",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        payload={
            "to": req.to,
            "tenant_id": req.tenant_id,
            "company_id": req.company_id,
            "campaign_id": req.campaign_id,
            "campaign_name": req.campaign_name,
        },
    )
    if cached is not None:
        return cached

    try:
        result = await providers.make_call(from_=AT_VIRTUAL_NUMBER, to=[req.to])
    except Exception as exc:
        logger.warning("AT outbound call failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Voice provider unavailable") from exc

    campaign_id = campaign_analytics.record_outbound_campaign(
        channel="voice",
        tenant_id=req.tenant_id,
        company_id=req.company_id,
        recipients=[req.to],
        message=req.campaign_name or "Voice outreach",
        provider_result=result if isinstance(result, dict) else {},
        campaign_id=req.campaign_id,
        campaign_name=req.campaign_name,
    )
    body = {"status": "ok", "result": result, "campaign_id": campaign_id}
    idempotency_commit(
        scope="at_voice_call",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        body=body,
    )
    return body


@router.post("/voice/campaign")
async def voice_campaign(
    req: CampaignCallRequest,
    idempotency_key: str = Depends(require_idempotency_key),
) -> dict:
    """Outbound voice campaign. Requires Idempotency-Key header."""
    if not AT_VOICE_ENABLED:
        return {"status": "disabled", "detail": "Voice channel is disabled"}

    cached = idempotency_preflight(
        scope="at_voice_campaign",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        payload={
            "to": req.to,
            "message": req.message,
            "tenant_id": req.tenant_id,
            "company_id": req.company_id,
            "campaign_id": req.campaign_id,
            "campaign_name": req.campaign_name,
        },
    )
    if cached is not None:
        return cached

    try:
        result = await providers.make_call(from_=AT_VIRTUAL_NUMBER, to=req.to)
    except Exception as exc:
        logger.warning("AT voice campaign failed", exc_info=True, extra={"to_count": len(req.to)})
        raise HTTPException(status_code=502, detail="Voice provider unavailable") from exc

    campaign_id = campaign_analytics.record_outbound_campaign(
        channel="voice",
        tenant_id=req.tenant_id,
        company_id=req.company_id,
        recipients=req.to,
        message=req.message,
        provider_result=result if isinstance(result, dict) else {},
        campaign_id=req.campaign_id,
        campaign_name=req.campaign_name,
    )
    body = {"status": "ok", "result": result, "campaign_id": campaign_id}
    idempotency_commit(
        scope="at_voice_campaign",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        body=body,
    )
    return body


@router.post("/voice/transfer")
async def voice_transfer(
    req: TransferRequest,
    idempotency_key: str = Depends(require_idempotency_key),
) -> dict:
    """Transfer an active call to a human agent. Requires Idempotency-Key header."""
    if not AT_VOICE_ENABLED:
        return {"status": "disabled", "detail": "Voice channel is disabled"}

    cached = idempotency_preflight(
        scope="at_voice_transfer",
        tenant_id="public",
        idempotency_key=idempotency_key,
        payload={"session_id": req.session_id, "transfer_to": req.transfer_to},
    )
    if cached is not None:
        return cached

    try:
        result = await providers.transfer_call(
            session_id=req.session_id,
            phone_number=req.transfer_to,
        )
    except Exception as exc:
        logger.warning("AT voice transfer failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Voice transfer unavailable") from exc

    body = {"status": "ok", "result": result}
    idempotency_commit(
        scope="at_voice_transfer",
        tenant_id="public",
        idempotency_key=idempotency_key,
        body=body,
    )
    return body
