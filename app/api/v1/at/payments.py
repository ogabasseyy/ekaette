"""AT payment routes (Paystack)."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from .idempotency import idempotency_commit, idempotency_preflight, require_idempotency_key
from .models import PaystackInitializeRequest, PaystackVirtualAccountCreateRequest
from .service_payments import PaymentGatewayError
from . import service_payments

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/payments/paystack/initialize")
async def paystack_initialize(
    req: PaystackInitializeRequest,
    idempotency_key: str = Depends(require_idempotency_key),
) -> dict:
    """Initialize a Paystack checkout and return authorization URL."""
    payload = req.model_dump(by_alias=True)
    cached = idempotency_preflight(
        scope="paystack_initialize",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if cached is not None:
        return cached

    try:
        initialized = await service_payments.initialize_transaction(
            email=req.email,
            amount_kobo=req.amount_kobo,
            currency=req.currency,
            callback_url=req.callback_url,
            reference=req.reference,
            tenant_id=req.tenant_id,
            company_id=req.company_id,
            campaign_id=req.campaign_id,
            customer_phone=req.customer_phone,
            metadata=req.metadata,
        )
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    body = {
        "status": "ok",
        "gateway": "paystack",
        "tenant_id": req.tenant_id,
        "company_id": req.company_id,
        "campaign_id": req.campaign_id,
        "reference": initialized["reference"],
        "authorization_url": initialized["authorization_url"],
        "access_code": initialized["access_code"],
    }
    idempotency_commit(
        scope="paystack_initialize",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        body=body,
    )
    return body


@router.post("/payments/paystack/virtual-accounts")
async def paystack_virtual_account_create(
    req: PaystackVirtualAccountCreateRequest,
    idempotency_key: str = Depends(require_idempotency_key),
) -> dict:
    """Create a dedicated virtual account for bank transfer payments."""
    payload = req.model_dump(by_alias=True)
    cached = idempotency_preflight(
        scope="paystack_virtual_account_create",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if cached is not None:
        return cached

    try:
        created = await service_payments.create_virtual_account(
            email=req.email,
            first_name=req.first_name,
            last_name=req.last_name,
            phone=req.phone,
            preferred_bank_slug=req.preferred_bank_slug,
            country=req.country,
            tenant_id=req.tenant_id,
            company_id=req.company_id,
            campaign_id=req.campaign_id,
            expected_amount_kobo=req.expected_amount_kobo,
            reference=req.reference,
            customer_phone=req.customer_phone,
            metadata=req.metadata,
        )
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    notify_phone = req.phone or req.customer_phone or ""
    notify_kwargs = dict(
        phone=notify_phone,
        account_number=created["account_number"],
        bank_name=created["bank_name"],
        account_name=created["account_name"],
        amount_kobo=req.expected_amount_kobo,
    )
    sms_sent = await service_payments.send_va_notification_sms(**notify_kwargs)
    whatsapp_sent = await service_payments.send_va_notification_whatsapp(**notify_kwargs)

    body = {
        "status": "ok",
        "gateway": "paystack",
        "payment_method": "virtual_account",
        "tenant_id": req.tenant_id,
        "company_id": req.company_id,
        "campaign_id": req.campaign_id,
        "reference": created["reference"],
        "account_number": created["account_number"],
        "account_name": created["account_name"],
        "bank_name": created["bank_name"],
        "bank_slug": created["bank_slug"],
        "sms_sent": sms_sent,
        "whatsapp_sent": whatsapp_sent,
        "instructions": "Send payment via bank transfer to this account. Confirmation is automatic.",
    }
    idempotency_commit(
        scope="paystack_virtual_account_create",
        tenant_id=req.tenant_id,
        idempotency_key=idempotency_key,
        body=body,
    )
    return body


@router.get("/payments/paystack/virtual-accounts/providers")
async def paystack_virtual_account_providers() -> dict:
    """Fetch available banks/providers for dedicated virtual accounts."""
    try:
        providers = await service_payments.list_virtual_account_providers()
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {
        "status": "ok",
        "gateway": "paystack",
        "payment_method": "virtual_account",
        "providers": providers,
    }


@router.get("/payments/paystack/virtual-accounts/{reference}")
async def paystack_virtual_account_get(reference: str) -> dict:
    """Fetch a previously created virtual account record by reference."""
    record = service_payments.virtual_account_snapshot(reference)
    if record is None:
        raise HTTPException(status_code=404, detail="Virtual account not found")
    return {
        "status": "ok",
        "gateway": "paystack",
        "payment_method": "virtual_account",
        "virtual_account": record,
    }


@router.get("/payments/paystack/verify/{reference}")
async def paystack_verify(reference: str) -> dict:
    """Verify Paystack transaction status by reference."""
    try:
        result = await service_payments.verify_transaction(reference)
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return {
        "status": "ok",
        "gateway": "paystack",
        "reference": result["reference"],
        "payment": result.get("payment"),
        "processed": result.get("processed"),
        "verification": result.get("verification"),
    }


@router.post("/payments/paystack/webhook")
async def paystack_webhook(request: Request) -> dict:
    """Receive Paystack webhook and update payment + campaign analytics state."""
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature")
    if not service_payments.verify_webhook_signature(raw_body=raw_body, signature=signature):
        raise HTTPException(status_code=401, detail="Invalid Paystack signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be an object")

    try:
        processed = service_payments.process_gateway_event(payload)
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    logger.info(
        "Paystack webhook accepted",
        extra={
            "reference": processed.get("reference"),
            "event": processed.get("event"),
            "campaign_id": processed.get("campaign_id"),
        },
    )
    return {
        "status": "ok",
        "processed": processed,
    }
