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
    WA_GRAPH_RETRY_MAX_ATTEMPTS,
    WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS,
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


# Media size limits per type (WhatsApp Cloud API 2026)
MEDIA_SIZE_LIMITS: dict[str, int] = {
    "image": 5 * 1024 * 1024,
    "audio": 16 * 1024 * 1024,
    "video": 16 * 1024 * 1024,
    "document": 100 * 1024 * 1024,
    "sticker": 500 * 1024,
}


import random


async def _wa_graph_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: dict | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict]:
    """Graph API request with bounded transient retry (429, 5xx, network timeout)."""
    last_exc: Exception | None = None
    for attempt in range(WA_GRAPH_RETRY_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "POST":
                    response = await client.post(url, headers=headers, json=json)
                else:
                    response = await client.get(url, headers=headers)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < WA_GRAPH_RETRY_MAX_ATTEMPTS - 1:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS)
                        except (ValueError, TypeError):
                            delay = _jitter_backoff(attempt)
                    else:
                        delay = _jitter_backoff(attempt)
                    import asyncio
                    await asyncio.sleep(delay)
                    continue

            try:
                body = response.json()
            except Exception:
                body = {}
            return response.status_code, body if isinstance(body, dict) else {}
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < WA_GRAPH_RETRY_MAX_ATTEMPTS - 1:
                import asyncio
                await asyncio.sleep(_jitter_backoff(attempt))
                continue
            raise

    if last_exc:
        raise last_exc
    return 500, {}


def _jitter_backoff(attempt: int) -> float:
    """Full-jitter exponential backoff capped by max backoff setting."""
    base = min(2 ** attempt, WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS)
    return random.uniform(0, base)


async def whatsapp_download_media(
    *,
    access_token: str,
    media_id: str,
    media_type: str = "image",
    api_version: str | None = None,
) -> tuple[bytes, str]:
    """Download media from WhatsApp Cloud API (two-step: get URL, then download).

    Returns (content_bytes, content_type).
    Enforces Content-Length + hard byte cap per media type.
    """
    resolved_version = (api_version or "").strip() or WHATSAPP_API_VERSION
    size_limit = MEDIA_SIZE_LIMITS.get(media_type, MEDIA_SIZE_LIMITS["image"])
    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: Get media URL
    meta_url = f"https://graph.facebook.com/{resolved_version}/{media_id}"
    status, meta_body = await _wa_graph_request("GET", meta_url, headers=headers)
    if status != 200:
        raise RuntimeError(f"Media metadata fetch failed: {status}")

    download_url = meta_body.get("url", "")
    if not download_url:
        raise RuntimeError("No download URL in media metadata")

    # Step 2: Stream download with byte cap
    content_type = "application/octet-stream"
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("GET", download_url, headers=headers) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"Media download failed: {resp.status_code}")

            content_type = resp.headers.get("content-type", content_type)

            # Check Content-Length header first
            content_length = resp.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > size_limit:
                        raise ValueError(
                            f"Media too large: {int(content_length)} bytes "
                            f"(limit {size_limit} for {media_type})"
                        )
                except (ValueError, TypeError) as exc:
                    if "Media too large" in str(exc):
                        raise
                    pass

            # Stream with hard byte cap
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(chunk_size=8192):
                total += len(chunk)
                if total > size_limit:
                    raise ValueError(
                        f"Media stream exceeded limit: >{size_limit} bytes for {media_type}"
                    )
                chunks.append(chunk)

    return b"".join(chunks), content_type


async def whatsapp_send_interactive(
    *,
    access_token: str,
    to: str,
    interactive: dict,
    phone_number_id: str | None = None,
    api_version: str | None = None,
) -> tuple[int, dict]:
    """Send interactive message (buttons/lists) via Graph API."""
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
        "type": "interactive",
        "interactive": interactive,
    }
    return await _wa_graph_request("POST", url, headers=headers, json=payload)


async def whatsapp_send_template(
    *,
    access_token: str,
    to: str,
    template_name: str,
    language_code: str = "en_US",
    components: list | None = None,
    phone_number_id: str | None = None,
    api_version: str | None = None,
) -> tuple[int, dict]:
    """Send template message (for outside service window)."""
    resolved_phone_id = (phone_number_id or "").strip() or WHATSAPP_PHONE_NUMBER_ID
    resolved_version = (api_version or "").strip() or WHATSAPP_API_VERSION
    url = f"https://graph.facebook.com/{resolved_version}/{resolved_phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    template_obj: dict = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_obj["components"] = components

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": "template",
        "template": template_obj,
    }
    return await _wa_graph_request("POST", url, headers=headers, json=payload)
