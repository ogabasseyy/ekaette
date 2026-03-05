"""Gemini Live API function tool for during-call WhatsApp messaging.

Allows the AI to send account numbers, payment details, etc. to the
user's WhatsApp chat during a live voice call.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx

logger = logging.getLogger(__name__)


# Gemini function declaration for the tools config
SEND_WA_MESSAGE_TOOL = {
    "function_declarations": [
        {
            "name": "send_whatsapp_message",
            "description": (
                "Send a WhatsApp text message to the caller during the voice call. "
                "Use this to share account numbers, payment details, booking confirmations, "
                "or any information the caller needs in written form."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The message text to send via WhatsApp",
                    },
                },
                "required": ["text"],
            },
        }
    ]
}


def _build_service_auth_headers(
    body: str,
    secret: str,
) -> dict[str, str]:
    """Build service-auth headers: HMAC + timestamp + nonce."""
    timestamp = str(time.time())
    nonce = uuid.uuid4().hex
    message = f"{timestamp}:{nonce}:{body}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Auth": sig,
    }


async def handle_send_wa_message(
    args: dict,
    caller_phone: str,
    config,
) -> dict:
    """Handle the send_whatsapp_message tool call from Gemini.

    Posts intent to Cloud Run /api/v1/at/whatsapp/send with service-auth.
    """
    text = args.get("text", "")
    if not text:
        return {"status": "error", "detail": "No text provided"}

    if not config.wa_service_api_base_url:
        return {"status": "error", "detail": "WA_SERVICE_API_BASE_URL not configured"}

    if not config.wa_service_secret:
        return {"status": "error", "detail": "WA_SERVICE_SECRET not configured"}

    url = f"{config.wa_service_api_base_url}/api/v1/at/whatsapp/send"

    payload = json.dumps({
        "to": caller_phone,
        "text": text,
        "type": "text",
        "tenant_id": config.tenant_id,
        "company_id": config.company_id,
    })

    tenant_id = str(getattr(config, "tenant_id", "") or "public")
    company_id = str(getattr(config, "company_id", "") or "ekaette-electronics")
    invocation_scope = str(
        args.get("invocation_id") or args.get("call_id") or args.get("session_id") or caller_phone
    )

    # Deterministic idempotency key scoped by tenant/company/tool/invocation.
    idempotency_key = hashlib.sha256(
        f"{tenant_id}:{company_id}:send_whatsapp_message:{invocation_scope}:{text}".encode()
    ).hexdigest()

    auth_headers = _build_service_auth_headers(payload, config.wa_service_secret)
    headers = {
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key,
        **auth_headers,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, content=payload.encode(), headers=headers)

        if response.status_code == 200:
            body = response.json()
            return {"status": "sent", "message_id": body.get("result", {}).get("messages", [{}])[0].get("id", "")}
        else:
            content_length = response.headers.get("content-length", "unknown")
            logger.warning(
                "WA send failed: status=%d content_length=%s",
                response.status_code,
                content_length,
            )
            return {"status": "error", "detail": f"HTTP {response.status_code}"}

    except Exception:
        logger.warning("WA send request failed", exc_info=True)
        return {"status": "error", "detail": "Request failed"}
