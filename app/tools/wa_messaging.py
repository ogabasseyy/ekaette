"""ADK function tool for during-call WhatsApp messaging.

Allows the AI to send account numbers, payment details, etc. to the
caller's WhatsApp chat during a live voice call via the gateway.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
import base64
from urllib.parse import urlparse

import httpx

from app.tools.sms_messaging import resolve_caller_phone_from_context

logger = logging.getLogger(__name__)


def _resolve_whatsapp_send_context(tool_context) -> tuple[str, str, str, str, str] | None:
    caller_phone = resolve_caller_phone_from_context(tool_context)
    if not caller_phone:
        return None

    base_url = os.getenv("WA_SERVICE_API_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise ValueError("WA_SERVICE_API_BASE_URL not configured")

    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("WA_SERVICE_API_BASE_URL must be https")

    secret = os.getenv("WA_SERVICE_SECRET", "")
    if not secret:
        raise ValueError("WA_SERVICE_SECRET not configured")

    tenant_id = str(tool_context.state.get("app:tenant_id", "")).strip()
    company_id = str(tool_context.state.get("app:company_id", "")).strip()
    if not tenant_id or not company_id:
        raise ValueError("Missing tenant/company scope in session state")

    return caller_phone, tenant_id, company_id, base_url, secret


def _build_service_auth_headers(
    *,
    payload: str,
    secret: str,
    idempotency_key: str,
) -> dict[str, str]:
    timestamp = str(time.time())
    nonce = uuid.uuid4().hex
    message = f"{timestamp}:{nonce}:{payload}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key,
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Auth": sig,
    }


async def _post_internal_whatsapp_send(
    *,
    url: str,
    payload: dict[str, object],
    secret: str,
    idempotency_key: str,
) -> dict:
    payload_json = json.dumps(payload)
    headers = _build_service_auth_headers(
        payload=payload_json,
        secret=secret,
        idempotency_key=idempotency_key,
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, content=payload_json.encode(), headers=headers)

        if response.status_code == 200:
            body = response.json()
            return {
                "status": "sent",
                "message_id": body.get("result", {}).get("messages", [{}])[0].get("id", ""),
            }
        logger.warning("WA send failed: status=%d", response.status_code)
        return {"status": "error", "detail": f"HTTP {response.status_code}"}
    except Exception:
        logger.warning("WA send request failed", exc_info=True)
        return {"status": "error", "detail": "Request failed"}


async def send_whatsapp_message(
    text: str,
    tool_context,
    *,
    template_name: str = "",
    template_language: str = "",
) -> dict:
    """Send a WhatsApp text message to the caller during the voice call.

    Use this to share account numbers, payment details, booking confirmations,
    or any information the caller needs in written form.
    """
    if not text:
        return {"status": "error", "detail": "No text provided"}
    try:
        resolved = _resolve_whatsapp_send_context(tool_context)
    except ValueError as exc:
        return {"status": "error", "detail": str(exc)}
    if resolved is None:
        return {"status": "error", "detail": "No caller phone in session"}
    caller_phone, tenant_id, company_id, base_url, secret = resolved

    url = f"{base_url}/api/v1/at/whatsapp/send"
    payload = {
        "to": caller_phone,
        "text": text,
        "type": "text",
        "tenant_id": tenant_id,
        "company_id": company_id,
        "template_name": template_name,
        "template_language": template_language,
    }

    # Idempotency key scoped per tool invocation — use ADK's function_call_id
    # which is stable across retries of the same call but unique per invocation.
    # Falls back to uuid4 if function_call_id is unavailable.
    invocation_id = getattr(tool_context, "function_call_id", None) or uuid.uuid4().hex
    idempotency_key = hashlib.sha256(
        f"{tenant_id}:{company_id}:send_whatsapp_message:{caller_phone}:{text}:{invocation_id}".encode()
    ).hexdigest()

    return await _post_internal_whatsapp_send(
        url=url,
        payload=payload,
        secret=secret,
        idempotency_key=idempotency_key,
    )


async def send_whatsapp_image_message(
    *,
    media_bytes: bytes,
    tool_context,
    mime_type: str = "image/png",
    caption: str = "",
    idempotency_namespace: str = "send_whatsapp_image_message",
) -> dict:
    """Send a generated or prebuilt image to the caller's WhatsApp chat."""
    if not media_bytes:
        return {"status": "error", "detail": "No media bytes provided"}
    if not mime_type.startswith("image/"):
        return {"status": "error", "detail": "mime_type must be an image/* type"}
    try:
        resolved = _resolve_whatsapp_send_context(tool_context)
    except ValueError as exc:
        return {"status": "error", "detail": str(exc)}
    if resolved is None:
        return {"status": "error", "detail": "No caller phone in session"}
    caller_phone, tenant_id, company_id, base_url, secret = resolved

    url = f"{base_url}/api/v1/at/whatsapp/send"
    payload = {
        "to": caller_phone,
        "type": "image",
        "media_base64": base64.b64encode(media_bytes).decode(),
        "mime_type": mime_type,
        "caption": caption,
        "tenant_id": tenant_id,
        "company_id": company_id,
    }

    invocation_id = getattr(tool_context, "function_call_id", None) or uuid.uuid4().hex
    image_hash = hashlib.sha256(media_bytes).hexdigest()
    idempotency_key = hashlib.sha256(
        (
            f"{tenant_id}:{company_id}:{idempotency_namespace}:{caller_phone}:{mime_type}:"
            f"{caption}:{image_hash}:{invocation_id}"
        ).encode()
    ).hexdigest()

    return await _post_internal_whatsapp_send(
        url=url,
        payload=payload,
        secret=secret,
        idempotency_key=idempotency_key,
    )
