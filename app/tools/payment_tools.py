"""Payment tools for voice/chat agents (Paystack virtual accounts + status checks)."""

from __future__ import annotations

from typing import Any

from app.api.v1.at import service_payments


def _tenant_company_from_context(tool_context: Any) -> tuple[str, str]:
    state = getattr(tool_context, "state", {}) if tool_context is not None else {}
    tenant_id = state.get("app:tenant_id") if isinstance(state.get("app:tenant_id"), str) else "public"
    company_id = (
        state.get("app:company_id")
        if isinstance(state.get("app:company_id"), str)
        else "ekaette-electronics"
    )
    return tenant_id, company_id


async def create_virtual_account_payment(
    customer_email: str,
    customer_first_name: str,
    customer_last_name: str,
    customer_phone: str | None = None,
    expected_amount_kobo: int | None = None,
    campaign_id: str | None = None,
    preferred_bank_slug: str | None = None,
    reference: str | None = None,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Create a dedicated transfer account that can be read out on a live call."""
    tenant_id, company_id = _tenant_company_from_context(tool_context)

    try:
        result = await service_payments.create_virtual_account(
            email=customer_email,
            first_name=customer_first_name,
            last_name=customer_last_name,
            phone=customer_phone,
            preferred_bank_slug=preferred_bank_slug,
            country=None,
            tenant_id=tenant_id,
            company_id=company_id,
            campaign_id=campaign_id,
            expected_amount_kobo=expected_amount_kobo,
            reference=reference,
            customer_phone=customer_phone,
            metadata={"source": "agent_tool"},
        )
    except service_payments.PaymentGatewayError as exc:
        return {
            "error": exc.message,
            "code": "PAYMENT_VIRTUAL_ACCOUNT_CREATE_FAILED",
            "status_code": exc.status_code,
        }
    except Exception as exc:
        return {
            "error": f"Unexpected payment error: {exc}",
            "code": "PAYMENT_VIRTUAL_ACCOUNT_UNEXPECTED",
            "status_code": 500,
        }

    notify_kwargs = dict(
        phone=customer_phone or "",
        account_number=result.get("account_number", ""),
        bank_name=result.get("bank_name", ""),
        account_name=result.get("account_name", ""),
        amount_kobo=expected_amount_kobo,
    )
    sms_sent = await service_payments.send_va_notification_sms(**notify_kwargs)
    whatsapp_sent = await service_payments.send_va_notification_whatsapp(**notify_kwargs)

    return {
        "status": "ok",
        "payment_method": "virtual_account",
        "reference": result.get("reference"),
        "account_number": result.get("account_number"),
        "account_name": result.get("account_name"),
        "bank_name": result.get("bank_name"),
        "bank_slug": result.get("bank_slug"),
        "sms_sent": sms_sent,
        "whatsapp_sent": whatsapp_sent,
        "instructions": (
            "Share this account on voice and follow up via SMS/WhatsApp. "
            "Confirm payment only after webhook/verify success."
        ),
    }


def _check_record_ownership(record: dict[str, Any] | None, tenant_id: str, company_id: str) -> bool:
    """Verify a payment record belongs to the caller's tenant/company."""
    if record is None:
        return True  # Nothing to check
    rec_tenant = record.get("tenant_id", "")
    rec_company = record.get("company_id", "")
    if rec_tenant and rec_tenant != tenant_id:
        return False
    if rec_company and rec_company != company_id:
        return False
    return True


async def check_payment_status(reference: str, tool_context: Any = None) -> dict[str, Any]:
    """Check payment status from local state, then fallback to Paystack verify."""
    tenant_id, company_id = _tenant_company_from_context(tool_context)

    local_payment = service_payments.payment_snapshot(reference)
    local_virtual = service_payments.virtual_account_snapshot(reference)

    # Enforce tenant/company scope on local records
    if not _check_record_ownership(local_payment, tenant_id, company_id):
        return {"error": "Payment not found", "code": "PAYMENT_NOT_FOUND", "reference": reference}
    if not _check_record_ownership(local_virtual, tenant_id, company_id):
        return {"error": "Payment not found", "code": "PAYMENT_NOT_FOUND", "reference": reference}

    if local_payment is not None:
        return {
            "status": "ok",
            "source": "local",
            "reference": reference,
            "payment": local_payment,
            "virtual_account": local_virtual,
        }

    try:
        verified = await service_payments.verify_transaction(reference)
    except service_payments.PaymentGatewayError as exc:
        return {
            "error": exc.message,
            "code": "PAYMENT_VERIFY_FAILED",
            "status_code": exc.status_code,
            "reference": reference,
        }
    except Exception:
        return {
            "error": "Unexpected payment verification error",
            "code": "PAYMENT_VERIFY_UNEXPECTED",
            "status_code": 500,
            "reference": reference,
        }

    return {
        "status": "ok",
        "source": "gateway",
        "reference": reference,
        "payment": verified.get("payment"),
        "processed": verified.get("processed"),
    }


async def get_virtual_account_record(reference: str, tool_context: Any = None) -> dict[str, Any]:
    """Fetch virtual account metadata by reference."""
    tenant_id, company_id = _tenant_company_from_context(tool_context)
    record = service_payments.virtual_account_snapshot(reference)

    # Enforce tenant/company scope
    if not _check_record_ownership(record, tenant_id, company_id):
        return {"error": "Virtual account not found", "code": "VIRTUAL_ACCOUNT_NOT_FOUND", "reference": reference}
    if record is None:
        return {
            "error": "Virtual account not found",
            "code": "VIRTUAL_ACCOUNT_NOT_FOUND",
            "reference": reference,
        }
    return {
        "status": "ok",
        "reference": reference,
        "virtual_account": record,
    }
