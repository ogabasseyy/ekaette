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
from urllib.parse import urlparse

import httpx

from app.tools.sms_messaging import resolve_caller_phone_from_context

logger = logging.getLogger(__name__)


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
    caller_phone = resolve_caller_phone_from_context(tool_context)
    if not caller_phone:
        return {"status": "error", "detail": "No caller phone in session"}

    if not text:
        return {"status": "error", "detail": "No text provided"}

    base_url = os.getenv("WA_SERVICE_API_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        return {"status": "error", "detail": "WA_SERVICE_API_BASE_URL not configured"}

    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return {"status": "error", "detail": "WA_SERVICE_API_BASE_URL must be https"}

    secret = os.getenv("WA_SERVICE_SECRET", "")
    if not secret:
        return {"status": "error", "detail": "WA_SERVICE_SECRET not configured"}

    tenant_id = str(tool_context.state.get("app:tenant_id", "")).strip()
    company_id = str(tool_context.state.get("app:company_id", "")).strip()
    if not tenant_id or not company_id:
        return {
            "status": "error",
            "detail": "Missing tenant/company scope in session state",
        }

    url = f"{base_url}/api/v1/at/whatsapp/send"
    payload = json.dumps({
        "to": caller_phone,
        "text": text,
        "type": "text",
        "tenant_id": tenant_id,
        "company_id": company_id,
        "template_name": template_name,
        "template_language": template_language,
    })

    # HMAC service auth
    timestamp = str(time.time())
    nonce = uuid.uuid4().hex
    message = f"{timestamp}:{nonce}:{payload}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    # Idempotency key scoped per tool invocation — use ADK's function_call_id
    # which is stable across retries of the same call but unique per invocation.
    # Falls back to uuid4 if function_call_id is unavailable.
    invocation_id = getattr(tool_context, "function_call_id", None) or uuid.uuid4().hex
    idempotency_key = hashlib.sha256(
        f"{tenant_id}:{company_id}:send_whatsapp_message:{caller_phone}:{text}:{invocation_id}".encode()
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key,
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Auth": sig,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, content=payload.encode(), headers=headers)

        if response.status_code == 200:
            body = response.json()
            return {
                "status": "sent",
                "message_id": body.get("result", {}).get("messages", [{}])[0].get("id", ""),
            }
        else:
            logger.warning(
                "WA send failed: status=%d", response.status_code,
            )
            return {"status": "error", "detail": f"HTTP {response.status_code}"}
    except Exception:
        logger.warning("WA send request failed", exc_info=True)
        return {"status": "error", "detail": "Request failed"}
