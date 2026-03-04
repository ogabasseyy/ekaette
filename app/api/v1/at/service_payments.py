"""Paystack payment orchestration + webhook processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import logging
import re
import threading
import uuid
from typing import Any

from app.configs import sanitize_log

from . import campaign_analytics
from . import providers
from .settings import (
    AT_SMS_ENABLED,
    PAYSTACK_DEFAULT_CALLBACK_URL,
    PAYSTACK_DEFAULT_DVA_BANK_SLUG,
    PAYSTACK_DEFAULT_DVA_COUNTRY,
    PAYSTACK_ENABLED,
    PAYSTACK_SECRET_KEY,
    PAYSTACK_WEBHOOK_SECRET,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_ENABLED,
)

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"^[A-Za-z0-9._:-]{6,128}$")
_ACCOUNT_NUMBER_RE = re.compile(r"^[0-9]{8,20}$")


@dataclass(slots=True)
class PaymentGatewayError(Exception):
    message: str
    status_code: int = 502


_payment_lock = threading.Lock()
_payment_records: dict[str, dict[str, Any]] = {}
_virtual_account_records: dict[str, dict[str, Any]] = {}
_virtual_account_by_number: dict[str, str] = {}
_customer_code_by_email: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _secret_key() -> str:
    return (PAYSTACK_SECRET_KEY or "").strip()


def _webhook_secret() -> str:
    candidate = (PAYSTACK_WEBHOOK_SECRET or "").strip()
    if candidate:
        return candidate
    return _secret_key()


def _is_enabled() -> bool:
    return PAYSTACK_ENABLED and bool(_secret_key())


def _normalize_reference(reference: str | None) -> str:
    candidate = (reference or "").strip()
    if candidate and _REF_RE.fullmatch(candidate):
        return candidate
    suffix = uuid.uuid4().hex[:10]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"ekaette-{timestamp}-{suffix}"


def _validate_reference(reference: str | None) -> str:
    candidate = (reference or "").strip()
    if not candidate or not _REF_RE.fullmatch(candidate):
        raise PaymentGatewayError("Invalid payment reference", status_code=400)
    return candidate


def _coerce_amount_kobo(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return 0


def _coerce_metadata(raw: object) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _normalize_account_number(account_number: str | None) -> str:
    candidate = "".join(ch for ch in (account_number or "") if ch.isdigit())
    if _ACCOUNT_NUMBER_RE.fullmatch(candidate):
        return candidate
    return ""


def _coerce_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _store_payment(reference: str, payload: dict[str, Any]) -> None:
    with _payment_lock:
        existing = _payment_records.get(reference, {})
        merged = {**existing, **payload}
        merged.setdefault("reference", reference)
        merged["updated_at"] = _coerce_str(payload.get("updated_at")) or _now_iso()
        _payment_records[reference] = merged


def _store_virtual_account(reference: str, payload: dict[str, Any]) -> None:
    with _payment_lock:
        existing = _virtual_account_records.get(reference, {})
        merged = {**existing, **payload}
        merged.setdefault("reference", reference)
        merged["updated_at"] = _coerce_str(payload.get("updated_at")) or _now_iso()
        _virtual_account_records[reference] = merged
        account_number = _normalize_account_number(_coerce_str(merged.get("account_number")))
        if account_number:
            _virtual_account_by_number[account_number] = reference


def payment_snapshot(reference: str) -> dict[str, Any] | None:
    with _payment_lock:
        record = _payment_records.get(reference)
        return dict(record) if isinstance(record, dict) else None


def virtual_account_snapshot(reference: str) -> dict[str, Any] | None:
    with _payment_lock:
        record = _virtual_account_records.get(reference)
        return dict(record) if isinstance(record, dict) else None


def _virtual_account_from_number(account_number: str | None) -> dict[str, Any] | None:
    normalized = _normalize_account_number(account_number)
    if not normalized:
        return None
    with _payment_lock:
        reference = _virtual_account_by_number.get(normalized)
        if not reference:
            return None
        record = _virtual_account_records.get(reference)
        return dict(record) if isinstance(record, dict) else None


def verify_webhook_signature(*, raw_body: bytes, signature: str | None) -> bool:
    secret = _webhook_secret()
    if not secret:
        return False
    provided = (signature or "").strip().lower()
    if not provided:
        return False
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest().lower()
    return hmac.compare_digest(computed, provided)


async def initialize_transaction(
    *,
    email: str,
    amount_kobo: int,
    currency: str,
    callback_url: str | None,
    reference: str | None,
    tenant_id: str,
    company_id: str,
    campaign_id: str | None,
    customer_phone: str | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create Paystack checkout URL and cache local payment context."""
    if not _is_enabled():
        raise PaymentGatewayError("Paystack is disabled or not configured", status_code=503)

    normalized_reference = _normalize_reference(reference)
    resolved_currency = (currency or "NGN").strip().upper() or "NGN"

    merged_metadata = _coerce_metadata(metadata)
    merged_metadata.setdefault("tenant_id", tenant_id)
    merged_metadata.setdefault("company_id", company_id)
    merged_metadata.setdefault("payment_reference", normalized_reference)
    if campaign_id:
        merged_metadata.setdefault("campaign_id", campaign_id)
    if customer_phone:
        merged_metadata.setdefault("customer_phone", customer_phone)

    payload: dict[str, Any] = {
        "email": email,
        "amount": amount_kobo,
        "currency": resolved_currency,
        "reference": normalized_reference,
        "metadata": merged_metadata,
    }
    resolved_callback = (callback_url or "").strip() or (PAYSTACK_DEFAULT_CALLBACK_URL or "").strip()
    if resolved_callback:
        payload["callback_url"] = resolved_callback

    status_code, body = await providers.paystack_initialize_transaction(
        secret_key=_secret_key(),
        payload=payload,
    )
    if status_code >= 400:
        message = str(body.get("message") or "Paystack initialize failed")
        raise PaymentGatewayError(message, status_code=502)

    if body.get("status") is not True:
        message = str(body.get("message") or "Paystack initialize rejected")
        raise PaymentGatewayError(message, status_code=400)

    data = body.get("data")
    if not isinstance(data, dict):
        raise PaymentGatewayError("Paystack initialize response missing data", status_code=502)

    resolved_reference = _normalize_reference(str(data.get("reference") or normalized_reference))

    _store_payment(
        resolved_reference,
        {
            "reference": resolved_reference,
            "status": "initialized",
            "gateway": "paystack",
            "payment_method": "checkout",
            "tenant_id": tenant_id,
            "company_id": company_id,
            "campaign_id": campaign_id,
            "amount_kobo": amount_kobo,
            "currency": resolved_currency,
            "email": email,
            "metadata": merged_metadata,
            "authorization_url": data.get("authorization_url"),
            "access_code": data.get("access_code"),
            "updated_at": _now_iso(),
        },
    )

    if campaign_id:
        campaign_analytics.record_event(
            event_type="payment_initialized",
            channel="omni",
            tenant_id=tenant_id,
            company_id=company_id,
            campaign_id=campaign_id,
            recipient=customer_phone,
            reference=resolved_reference,
            event_id=f"paystack:init:{resolved_reference}",
            metadata={"gateway": "paystack", "method": "checkout", "email": email},
        )

    return {
        "reference": resolved_reference,
        "authorization_url": data.get("authorization_url"),
        "access_code": data.get("access_code"),
        "raw": body,
    }


def _customer_code_from_create_response(*, email: str, body: dict[str, Any]) -> str:
    data = body.get("data")
    if isinstance(data, dict):
        customer_code = _coerce_str(data.get("customer_code"))
        if customer_code:
            with _payment_lock:
                _customer_code_by_email[email] = customer_code
            return customer_code

    with _payment_lock:
        cached = _customer_code_by_email.get(email, "")
    return cached


def _extract_virtual_account_payload(data: dict[str, Any]) -> dict[str, Any]:
    dedicated = data.get("dedicated_account")
    if isinstance(dedicated, dict):
        account_obj = dedicated
    else:
        account_obj = data

    bank_name = _coerce_str(account_obj.get("bank_name"))
    bank_slug = _coerce_str(account_obj.get("bank_slug"))
    if not bank_name:
        bank = account_obj.get("bank")
        if isinstance(bank, dict):
            bank_name = _coerce_str(bank.get("name"))
            if not bank_slug:
                bank_slug = _coerce_str(bank.get("slug"))

    account_number = _normalize_account_number(
        _coerce_str(account_obj.get("account_number"))
        or _coerce_str(account_obj.get("accountNumber"))
    )
    account_name = _coerce_str(account_obj.get("account_name")) or _coerce_str(account_obj.get("accountName"))

    return {
        "account_number": account_number,
        "account_name": account_name,
        "bank_name": bank_name,
        "bank_slug": bank_slug,
        "dedicated_account_id": account_obj.get("id"),
        "customer_id": account_obj.get("customer"),
        "assignment_status": _coerce_str(account_obj.get("assigned")) or "assigned",
        "raw_data": data,
    }


async def create_virtual_account(
    *,
    email: str,
    first_name: str,
    last_name: str,
    phone: str | None,
    preferred_bank_slug: str | None,
    country: str | None,
    tenant_id: str,
    company_id: str,
    campaign_id: str | None,
    expected_amount_kobo: int | None,
    reference: str | None,
    customer_phone: str | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create a Paystack dedicated virtual account for transfer payments."""
    if not _is_enabled():
        raise PaymentGatewayError("Paystack is disabled or not configured", status_code=503)

    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise PaymentGatewayError("Email is required", status_code=400)

    resolved_reference = _normalize_reference(reference)
    resolved_bank_slug = (preferred_bank_slug or "").strip().lower() or PAYSTACK_DEFAULT_DVA_BANK_SLUG
    resolved_country = (country or "").strip().upper() or PAYSTACK_DEFAULT_DVA_COUNTRY

    merged_metadata = _coerce_metadata(metadata)
    merged_metadata.setdefault("tenant_id", tenant_id)
    merged_metadata.setdefault("company_id", company_id)
    merged_metadata.setdefault("payment_reference", resolved_reference)
    merged_metadata.setdefault("payment_method", "virtual_account")
    if campaign_id:
        merged_metadata.setdefault("campaign_id", campaign_id)
    if customer_phone:
        merged_metadata.setdefault("customer_phone", customer_phone)
    if expected_amount_kobo:
        merged_metadata.setdefault("expected_amount_kobo", expected_amount_kobo)

    customer_payload = {
        "email": normalized_email,
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "phone": (phone or "").strip(),
        "metadata": merged_metadata,
    }
    customer_status, customer_body = await providers.paystack_create_customer(
        secret_key=_secret_key(),
        payload=customer_payload,
    )
    if customer_status >= 500:
        message = str(customer_body.get("message") or "Paystack customer creation failed")
        raise PaymentGatewayError(message, status_code=502)

    customer_code = _customer_code_from_create_response(email=normalized_email, body=customer_body)

    dedicated_payload: dict[str, Any]
    create_mode = "create"
    if customer_code:
        dedicated_payload = {
            "customer": customer_code,
            "preferred_bank": resolved_bank_slug,
            "country": resolved_country,
        }
        dedicated_status, dedicated_body = await providers.paystack_create_dedicated_account(
            secret_key=_secret_key(),
            payload=dedicated_payload,
        )
    else:
        create_mode = "assign"
        dedicated_payload = {
            "email": normalized_email,
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "phone": (phone or "").strip(),
            "preferred_bank": resolved_bank_slug,
            "country": resolved_country,
        }
        dedicated_status, dedicated_body = await providers.paystack_assign_dedicated_account(
            secret_key=_secret_key(),
            payload=dedicated_payload,
        )

    if dedicated_status >= 400:
        message = str(dedicated_body.get("message") or "Paystack virtual account creation failed")
        raise PaymentGatewayError(message, status_code=502)

    if dedicated_body.get("status") is not True:
        message = str(dedicated_body.get("message") or "Paystack virtual account request rejected")
        raise PaymentGatewayError(message, status_code=400)

    dedicated_data = dedicated_body.get("data")
    if not isinstance(dedicated_data, dict):
        raise PaymentGatewayError("Paystack virtual account response missing data", status_code=502)

    account_payload = _extract_virtual_account_payload(dedicated_data)
    account_number = _normalize_account_number(_coerce_str(account_payload.get("account_number")))
    if not account_number:
        raise PaymentGatewayError(
            "Virtual account provisioning is still in progress. Retry shortly.",
            status_code=409,
        )

    va_record = {
        "reference": resolved_reference,
        "status": "active",
        "tenant_id": tenant_id,
        "company_id": company_id,
        "campaign_id": campaign_id,
        "email": normalized_email,
        "customer_phone": customer_phone or phone,
        "expected_amount_kobo": expected_amount_kobo,
        "payment_method": "virtual_account",
        "provisioning_mode": create_mode,
        "account_number": account_number,
        "account_name": _coerce_str(account_payload.get("account_name")),
        "bank_name": _coerce_str(account_payload.get("bank_name")),
        "bank_slug": _coerce_str(account_payload.get("bank_slug")) or resolved_bank_slug,
        "dedicated_account_id": account_payload.get("dedicated_account_id"),
        "metadata": merged_metadata,
        "updated_at": _now_iso(),
    }
    _store_virtual_account(resolved_reference, va_record)

    _store_payment(
        resolved_reference,
        {
            "reference": resolved_reference,
            "status": "awaiting_transfer",
            "gateway": "paystack",
            "payment_method": "virtual_account",
            "tenant_id": tenant_id,
            "company_id": company_id,
            "campaign_id": campaign_id,
            "amount_kobo": expected_amount_kobo,
            "currency": "NGN",
            "email": normalized_email,
            "virtual_account_reference": resolved_reference,
            "virtual_account_number": account_number,
            "virtual_account_bank": _coerce_str(va_record.get("bank_name")),
            "metadata": merged_metadata,
            "updated_at": _now_iso(),
        },
    )

    if campaign_id:
        campaign_analytics.record_event(
            event_type="payment_initialized",
            channel="omni",
            tenant_id=tenant_id,
            company_id=company_id,
            campaign_id=campaign_id,
            recipient=customer_phone or phone,
            reference=resolved_reference,
            event_id=f"paystack:va:init:{resolved_reference}",
            metadata={
                "gateway": "paystack",
                "method": "virtual_account",
                "account_number": account_number,
                "bank": va_record.get("bank_name"),
            },
        )

    return {
        "reference": resolved_reference,
        "account_number": account_number,
        "account_name": _coerce_str(va_record.get("account_name")),
        "bank_name": _coerce_str(va_record.get("bank_name")),
        "bank_slug": _coerce_str(va_record.get("bank_slug")),
        "status": "awaiting_transfer",
        "raw": dedicated_body,
    }


async def list_virtual_account_providers() -> list[dict[str, Any]]:
    """Return available Paystack dedicated account providers."""
    if not _is_enabled():
        raise PaymentGatewayError("Paystack is disabled or not configured", status_code=503)

    status_code, body = await providers.paystack_fetch_dedicated_account_providers(secret_key=_secret_key())
    if status_code >= 400:
        message = str(body.get("message") or "Paystack providers fetch failed")
        raise PaymentGatewayError(message, status_code=502)
    if body.get("status") is not True:
        message = str(body.get("message") or "Paystack providers request rejected")
        raise PaymentGatewayError(message, status_code=400)

    data = body.get("data")
    if not isinstance(data, list):
        return []

    providers_list: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        providers_list.append(
            {
                "slug": _coerce_str(item.get("slug")),
                "bank_name": _coerce_str(item.get("bank_name")) or _coerce_str(item.get("name")),
                "id": item.get("id"),
            }
        )
    return providers_list


async def verify_transaction(reference: str) -> dict[str, Any]:
    """Verify a Paystack transaction and sync analytics on success."""
    if not _is_enabled():
        raise PaymentGatewayError("Paystack is disabled or not configured", status_code=503)

    normalized_reference = _validate_reference(reference)
    status_code, body = await providers.paystack_verify_transaction(
        secret_key=_secret_key(),
        reference=normalized_reference,
    )
    if status_code >= 400:
        message = str(body.get("message") or "Paystack verify failed")
        raise PaymentGatewayError(message, status_code=502)
    if body.get("status") is not True:
        message = str(body.get("message") or "Paystack verify rejected")
        raise PaymentGatewayError(message, status_code=400)

    data = body.get("data")
    if not isinstance(data, dict):
        raise PaymentGatewayError("Paystack verify response missing data", status_code=502)

    processed = process_gateway_event(
        {
            "event": f"charge.{str(data.get('status') or '').lower()}",
            "data": data,
        }
    )

    return {
        "reference": normalized_reference,
        "verification": body,
        "processed": processed,
        "payment": payment_snapshot(normalized_reference),
    }


def _extract_account_number_from_charge(data: dict[str, Any]) -> str:
    authorization = data.get("authorization")
    if isinstance(authorization, dict):
        for key in (
            "receiver_bank_account_number",
            "account_number",
            "receiver_account_number",
        ):
            normalized = _normalize_account_number(_coerce_str(authorization.get(key)))
            if normalized:
                return normalized

    dedicated = data.get("dedicated_account")
    if isinstance(dedicated, dict):
        normalized = _normalize_account_number(_coerce_str(dedicated.get("account_number")))
        if normalized:
            return normalized

    metadata = _coerce_metadata(data.get("metadata"))
    normalized = _normalize_account_number(_coerce_str(metadata.get("virtual_account_number")))
    if normalized:
        return normalized

    return ""


def _campaign_context_from_data(
    data: dict[str, Any],
) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    metadata = _coerce_metadata(data.get("metadata"))
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    company_id = str(metadata.get("company_id") or "").strip()
    campaign_id = metadata.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        campaign_id = None
    customer_phone = metadata.get("customer_phone")
    if not isinstance(customer_phone, str) or not customer_phone.strip():
        customer_phone = None

    account_number = _extract_account_number_from_charge(data)
    virtual_account_reference: str | None = None
    if account_number:
        record = _virtual_account_from_number(account_number)
        if isinstance(record, dict):
            virtual_account_reference = _coerce_str(record.get("reference")) or None
            if not campaign_id:
                maybe_campaign = record.get("campaign_id")
                if isinstance(maybe_campaign, str) and maybe_campaign.strip():
                    campaign_id = maybe_campaign.strip()
            if not customer_phone:
                maybe_phone = record.get("customer_phone")
                if isinstance(maybe_phone, str) and maybe_phone.strip():
                    customer_phone = maybe_phone.strip()
            if not tenant_id and isinstance(record.get("tenant_id"), str):
                tenant_id = str(record["tenant_id"])
            if not company_id and isinstance(record.get("company_id"), str):
                company_id = str(record["company_id"])

    if not tenant_id:
        tenant_id = "public"
    if not company_id:
        company_id = "ekaette-electronics"

    return tenant_id, company_id, campaign_id, customer_phone, virtual_account_reference, account_number or None


def process_gateway_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Process normalized Paystack event payload and update analytics state."""
    event = str(payload.get("event") or "").strip().lower()
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PaymentGatewayError("Webhook payload missing data", status_code=400)

    reference = _validate_reference(str(data.get("reference") or ""))
    amount_kobo = _coerce_amount_kobo(data.get("amount"))
    currency = str(data.get("currency") or "NGN")
    status = str(data.get("status") or "").strip().lower()
    gateway_customer = data.get("customer")
    customer_email = gateway_customer.get("email") if isinstance(gateway_customer, dict) else None
    authorization = data.get("authorization")
    payment_channel = _coerce_str(authorization.get("channel")) if isinstance(authorization, dict) else ""

    (
        tenant_id,
        company_id,
        campaign_id,
        customer_phone,
        virtual_account_reference,
        virtual_account_number,
    ) = _campaign_context_from_data(data)

    payment_method = "virtual_account" if payment_channel == "dedicated_nuban" else "checkout"

    _store_payment(
        reference,
        {
            "reference": reference,
            "status": status or event,
            "gateway": "paystack",
            "payment_method": payment_method,
            "payment_channel": payment_channel,
            "tenant_id": tenant_id,
            "company_id": company_id,
            "campaign_id": campaign_id,
            "amount_kobo": amount_kobo,
            "currency": currency,
            "email": customer_email,
            "metadata": _coerce_metadata(data.get("metadata")),
            "gateway_event": event,
            "virtual_account_reference": virtual_account_reference,
            "virtual_account_number": virtual_account_number,
            "updated_at": _now_iso(),
        },
    )

    # Keep the original virtual-account reference in sync so agents that check
    # payment status using the announced VA reference see the latest result.
    if virtual_account_reference and virtual_account_reference != reference:
        _store_payment(
            virtual_account_reference,
            {
                "status": status or event,
                "gateway_event": event,
                "payment_method": payment_method,
                "payment_channel": payment_channel,
                "last_gateway_reference": reference,
                "last_paid_amount_kobo": amount_kobo,
                "updated_at": _now_iso(),
            },
        )

    if virtual_account_reference:
        _store_virtual_account(
            virtual_account_reference,
            {
                "last_payment_reference": reference,
                "last_payment_status": status or event,
                "last_paid_amount_kobo": amount_kobo,
                "last_payment_at": _now_iso(),
                "status": "paid" if event in {"charge.success", "charge.succeeded"} else "active",
            },
        )

    if event in {"charge.success", "charge.succeeded"}:
        if campaign_id:
            campaign_analytics.record_event(
                event_type="payment_success",
                channel="omni",
                tenant_id=tenant_id,
                company_id=company_id,
                campaign_id=campaign_id,
                recipient=customer_phone,
                amount_kobo=amount_kobo,
                reference=reference,
                event_id=f"paystack:success:{reference}",
                metadata={
                    "gateway": "paystack",
                    "status": status,
                    "payment_method": payment_method,
                    "payment_channel": payment_channel,
                    "virtual_account_reference": virtual_account_reference,
                },
            )
    elif event in {"charge.failed", "charge.abandoned"}:
        if campaign_id:
            campaign_analytics.record_event(
                event_type="failed",
                channel="omni",
                tenant_id=tenant_id,
                company_id=company_id,
                campaign_id=campaign_id,
                recipient=customer_phone,
                reference=reference,
                event_id=f"paystack:failed:{reference}:{event}",
                metadata={
                    "gateway": "paystack",
                    "status": status,
                    "payment_method": payment_method,
                    "payment_channel": payment_channel,
                    "virtual_account_reference": virtual_account_reference,
                },
            )

    logger.info(
        "Paystack event processed",
        extra={
            "event": sanitize_log(event),
            "reference": sanitize_log(reference),
            "tenant_id": sanitize_log(tenant_id),
            "company_id": sanitize_log(company_id),
            "campaign_id": sanitize_log(str(campaign_id) if campaign_id else None),
            "status": sanitize_log(status),
            "payment_method": sanitize_log(payment_method),
            "payment_channel": sanitize_log(payment_channel),
            "virtual_account_reference": sanitize_log(
                str(virtual_account_reference) if virtual_account_reference else None
            ),
        },
    )

    return {
        "event": event,
        "reference": reference,
        "status": status,
        "campaign_id": campaign_id,
        "payment_method": payment_method,
        "payment_channel": payment_channel,
        "virtual_account_reference": virtual_account_reference,
    }


def _format_va_sms(account_number: str, bank_name: str, account_name: str, amount_kobo: int | None) -> str:
    """Format virtual account details into an SMS-sized message."""
    parts = [f"Pay to {account_number} ({bank_name})"]
    if account_name:
        parts.append(f"Name: {account_name}")
    if amount_kobo and amount_kobo > 0:
        naira = amount_kobo / 100
        parts.append(f"Amount: NGN {naira:,.2f}")
    return ". ".join(parts)


async def send_va_notification_sms(
    *,
    phone: str,
    account_number: str,
    bank_name: str,
    account_name: str,
    amount_kobo: int | None,
) -> bool:
    """Fire-and-forget SMS with virtual account details. Returns True on success."""
    if not AT_SMS_ENABLED:
        logger.debug("SMS disabled — skipping VA notification to %s", phone)
        return False
    if not phone or not phone.strip():
        return False

    message = _format_va_sms(account_number, bank_name, account_name, amount_kobo)
    try:
        await providers.send_sms(message=message, recipients=[phone.strip()])
        logger.info("VA SMS sent to %s", phone)
        return True
    except Exception:
        logger.warning("VA SMS failed for %s", phone, exc_info=True)
        return False


async def send_va_notification_whatsapp(
    *,
    phone: str,
    account_number: str,
    bank_name: str,
    account_name: str,
    amount_kobo: int | None,
) -> bool:
    """Fire-and-forget WhatsApp text with virtual account details. Returns True on success."""
    if not WHATSAPP_ENABLED:
        logger.debug("WhatsApp disabled — skipping VA notification to %s", phone)
        return False
    if not phone or not phone.strip():
        return False
    token = WHATSAPP_ACCESS_TOKEN
    if not token:
        logger.debug("WhatsApp access token not configured — skipping")
        return False

    message = _format_va_sms(account_number, bank_name, account_name, amount_kobo)
    try:
        status_code, body = await providers.whatsapp_send_text(
            access_token=token,
            to=phone.strip(),
            body=message,
        )
        if status_code >= 400:
            wa_error = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
            logger.warning("WhatsApp send failed (%s): %s", status_code, wa_error)
            return False
        logger.info("VA WhatsApp sent to %s", phone)
        return True
    except Exception:
        logger.warning("VA WhatsApp failed for %s", phone, exc_info=True)
        return False


def reset_state() -> None:
    """Testing helper: clear in-memory payment records."""
    with _payment_lock:
        _payment_records.clear()
        _virtual_account_records.clear()
        _virtual_account_by_number.clear()
        _customer_code_by_email.clear()
