"""Payment tools for voice/chat agents (Paystack virtual accounts + status checks)."""

from __future__ import annotations

import logging
from typing import Any

from app.api.v1.at import service_payments
from app.configs import sanitize_log
from app.tools.sms_messaging import (
    resolve_caller_phone_from_state,
    resolve_sms_sender_id_from_state,
)

logger = logging.getLogger(__name__)


def _format_naira_from_kobo(amount_kobo: int | None) -> str:
    if not isinstance(amount_kobo, int) or amount_kobo <= 0:
        return ""
    return f"{amount_kobo / 100:,.2f} naira"


def _tenant_company_from_context(tool_context: Any) -> tuple[str, str]:
    state = getattr(tool_context, "state", {}) if tool_context is not None else {}
    tenant_id = state.get("app:tenant_id") if isinstance(state.get("app:tenant_id"), str) else "public"
    company_id = (
        state.get("app:company_id")
        if isinstance(state.get("app:company_id"), str)
        else "ekaette-electronics"
    )
    return tenant_id, company_id


def _record_matches_scope(record: dict[str, Any], *, tenant_id: str, company_id: str) -> bool:
    record_tenant_id = str(record.get("tenant_id") or "").strip()
    record_company_id = str(record.get("company_id") or "").strip()
    if not record_tenant_id or not record_company_id:
        return False
    return record_tenant_id == tenant_id and record_company_id == company_id


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
    state = getattr(tool_context, "state", {}) if tool_context is not None else {}
    resolved_customer_phone = (
        customer_phone.strip()
        if isinstance(customer_phone, str) and customer_phone.strip()
        else resolve_caller_phone_from_state(state)
    )
    resolved_sender_id = resolve_sms_sender_id_from_state(state)

    try:
        result = await service_payments.create_virtual_account(
            email=customer_email,
            first_name=customer_first_name,
            last_name=customer_last_name,
            phone=resolved_customer_phone,
            preferred_bank_slug=preferred_bank_slug,
            country=None,
            tenant_id=tenant_id,
            company_id=company_id,
            campaign_id=campaign_id,
            expected_amount_kobo=expected_amount_kobo,
            reference=reference,
            customer_phone=resolved_customer_phone,
            metadata={"source": "agent_tool"},
        )
    except service_payments.PaymentGatewayError as exc:
        return {
            "error": exc.message,
            "code": "PAYMENT_VIRTUAL_ACCOUNT_CREATE_FAILED",
            "status_code": exc.status_code,
        }
    except Exception as exc:
        safe_context = {
            "code": "PAYMENT_VIRTUAL_ACCOUNT_UNEXPECTED",
            "tenant_id": sanitize_log(tenant_id),
            "company_id": sanitize_log(company_id),
            "error": sanitize_log(str(exc)),
        }
        logger.error(
            "Unexpected virtual account creation error: %s",
            sanitize_log(str(safe_context)),
        )
        return {
            "error": "Unexpected payment error",
            "code": "PAYMENT_VIRTUAL_ACCOUNT_UNEXPECTED",
            "status_code": 500,
        }

    notify_kwargs = dict(
        phone=resolved_customer_phone,
        account_number=result.get("account_number", ""),
        bank_name=result.get("bank_name", ""),
        account_name=result.get("account_name", ""),
        amount_kobo=expected_amount_kobo,
        sender_id=resolved_sender_id,
    )
    sms_sent = await service_payments.send_va_notification_sms(**notify_kwargs)
    whatsapp_sent = await service_payments.send_va_notification_whatsapp(
        phone=resolved_customer_phone,
        account_number=result.get("account_number", ""),
        bank_name=result.get("bank_name", ""),
        account_name=result.get("account_name", ""),
        amount_kobo=expected_amount_kobo,
    )

    return {
        "status": "ok",
        "payment_method": "virtual_account",
        "reference": result.get("reference"),
        "account_number": result.get("account_number"),
        "account_name": result.get("account_name"),
        "bank_name": result.get("bank_name"),
        "bank_slug": result.get("bank_slug"),
        "expected_amount_kobo": expected_amount_kobo,
        "expected_amount_display": _format_naira_from_kobo(expected_amount_kobo),
        "currency_name": "naira" if isinstance(expected_amount_kobo, int) and expected_amount_kobo > 0 else "",
        "notification_phone": resolved_customer_phone,
        "sms_sender_id": resolved_sender_id,
        "sms_sent": sms_sent,
        "whatsapp_sent": whatsapp_sent,
        "instructions": (
            "Share this account on voice and follow up via SMS/WhatsApp. "
            "Confirm payment only after webhook/verify success."
        ),
    }


async def check_payment_status(reference: str, tool_context: Any = None) -> dict[str, Any]:
    """Check payment status from local state, then fallback to Paystack verify."""
    tenant_id, company_id = _tenant_company_from_context(tool_context)

    local_payment = service_payments.payment_snapshot(reference)
    local_virtual = service_payments.virtual_account_snapshot(reference)

    if local_payment is not None:
        if not _record_matches_scope(local_payment, tenant_id=tenant_id, company_id=company_id):
            return {
                "error": "Payment record not found for tenant/company scope",
                "code": "PAYMENT_REFERENCE_NOT_FOUND",
                "reference": reference,
            }
        if isinstance(local_virtual, dict) and not _record_matches_scope(
            local_virtual,
            tenant_id=tenant_id,
            company_id=company_id,
        ):
            local_virtual = None
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
    except Exception as exc:
        safe_context = {
            "code": "PAYMENT_VERIFY_UNEXPECTED",
            "tenant_id": sanitize_log(tenant_id),
            "company_id": sanitize_log(company_id),
            "reference": sanitize_log(reference),
            "error": sanitize_log(str(exc)),
        }
        logger.error(
            "Unexpected payment verification error: %s",
            sanitize_log(str(safe_context)),
        )
        return {
            "error": "Unexpected payment verification error",
            "code": "PAYMENT_VERIFY_UNEXPECTED",
            "status_code": 500,
            "reference": reference,
        }

    verified_payment = verified.get("payment")
    if isinstance(verified_payment, dict) and not _record_matches_scope(
        verified_payment,
        tenant_id=tenant_id,
        company_id=company_id,
    ):
        return {
            "error": "Payment record not found for tenant/company scope",
            "code": "PAYMENT_REFERENCE_NOT_FOUND",
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
    if record is None:
        return {
            "error": "Virtual account not found",
            "code": "VIRTUAL_ACCOUNT_NOT_FOUND",
            "reference": reference,
        }
    if not _record_matches_scope(record, tenant_id=tenant_id, company_id=company_id):
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
