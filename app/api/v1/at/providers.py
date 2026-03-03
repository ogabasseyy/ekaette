"""AT SDK/httpx wrappers for voice and SMS.

All external calls go through here. Wrapped with asyncio.to_thread()
because the AT Python SDK is synchronous.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .settings import (
    PAYSTACK_CUSTOMER_URL,
    PAYSTACK_DEDICATED_ACCOUNT_PROVIDERS_URL,
    PAYSTACK_DEDICATED_ACCOUNT_URL,
    PAYSTACK_INITIALIZE_URL,
    PAYSTACK_VERIFY_URL_TEMPLATE,
    WHATSAPP_API_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
)

logger = logging.getLogger(__name__)


def _paystack_json_headers(secret_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }


def _paystack_auth_headers(secret_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret_key}"}


async def make_call(from_: str, to: list[str]) -> dict:
    """Initiate an outbound voice call via AT SDK."""
    import africastalking
    return await asyncio.to_thread(africastalking.Voice.call, callFrom=from_, callTo=to)


async def send_sms(message: str, recipients: list[str]) -> dict:
    """Send SMS via AT SDK."""
    import africastalking
    return await asyncio.to_thread(africastalking.SMS.send, message, recipients)


async def transfer_call(session_id: str, phone_number: str, call_leg: str = "callee") -> dict:
    """Transfer an active call via AT SDK."""
    import africastalking
    return await asyncio.to_thread(
        africastalking.Voice.transfer,
        sessionId=session_id,
        phoneNumber=phone_number,
        callLeg=call_leg,
    )


async def paystack_initialize_transaction(*, secret_key: str, payload: dict) -> tuple[int, dict]:
    """Initialize a Paystack transaction."""
    headers = _paystack_json_headers(secret_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            PAYSTACK_INITIALIZE_URL,
            headers=headers,
            json=payload,
        )
    try:
        body = response.json()
    except Exception:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


async def paystack_verify_transaction(*, secret_key: str, reference: str) -> tuple[int, dict]:
    """Verify a Paystack transaction by reference."""
    headers = _paystack_auth_headers(secret_key)
    verify_url = PAYSTACK_VERIFY_URL_TEMPLATE.format(reference=reference)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(verify_url, headers=headers)
    try:
        body = response.json()
    except Exception:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


async def paystack_create_customer(*, secret_key: str, payload: dict) -> tuple[int, dict]:
    """Create or fetch a Paystack customer record."""
    headers = _paystack_json_headers(secret_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(PAYSTACK_CUSTOMER_URL, headers=headers, json=payload)
    try:
        body = response.json()
    except Exception:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


async def paystack_create_dedicated_account(*, secret_key: str, payload: dict) -> tuple[int, dict]:
    """Create a Paystack dedicated virtual account for a customer."""
    headers = _paystack_json_headers(secret_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(PAYSTACK_DEDICATED_ACCOUNT_URL, headers=headers, json=payload)
    try:
        body = response.json()
    except Exception:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


async def paystack_assign_dedicated_account(*, secret_key: str, payload: dict) -> tuple[int, dict]:
    """Assign a dedicated virtual account to a customer identity."""
    headers = _paystack_json_headers(secret_key)
    assign_url = f"{PAYSTACK_DEDICATED_ACCOUNT_URL}/assign"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(assign_url, headers=headers, json=payload)
    try:
        body = response.json()
    except Exception:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


async def paystack_fetch_dedicated_account_providers(*, secret_key: str) -> tuple[int, dict]:
    """Fetch Paystack dedicated virtual account providers."""
    headers = _paystack_auth_headers(secret_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(PAYSTACK_DEDICATED_ACCOUNT_PROVIDERS_URL, headers=headers)
    try:
        body = response.json()
    except Exception:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


# ── WhatsApp Cloud API (Meta) ──


async def whatsapp_send_text(
    *,
    access_token: str,
    to: str,
    body: str,
    phone_number_id: str | None = None,
    api_version: str | None = None,
) -> tuple[int, dict]:
    """Send a plain text message via WhatsApp Cloud API.

    Uses Graph API endpoint:
      POST https://graph.facebook.com/{version}/{phone_number_id}/messages

    Phone number ``to`` must be E.164 digits without '+' prefix.
    """
    resolved_phone_id = (phone_number_id or "").strip() or WHATSAPP_PHONE_NUMBER_ID
    resolved_version = (api_version or "").strip() or WHATSAPP_API_VERSION
    url = f"https://graph.facebook.com/{resolved_version}/{resolved_phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=headers, json=payload)
    try:
        resp_body = response.json()
    except Exception:
        resp_body = {}
    return response.status_code, resp_body if isinstance(resp_body, dict) else {}
