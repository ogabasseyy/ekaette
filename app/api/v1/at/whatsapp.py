"""WhatsApp Cloud API route handlers (thin: parse → service → respond).

Endpoints:
  GET  /whatsapp/webhook  — Meta verification challenge
  POST /whatsapp/webhook  — Inbound messages (HMAC verify → enqueue Cloud Tasks)
  POST /whatsapp/process  — Cloud Tasks handler (process + send reply)
  POST /whatsapp/send     — Internal API for during-call sends (service-auth)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.configs import sanitize_log

from . import providers
from . import service_whatsapp
from .settings import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_ENABLED,
    WHATSAPP_PHONE_NUMBER_ID,
    WA_CLOUD_TASKS_MAX_ATTEMPTS,
    WA_TASKS_INVOKER_EMAIL,
)
from .wa_security import (
    verify_cloud_tasks_oidc,
    verify_service_auth,
    verify_wa_verify_token,
    verify_wa_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_task_id(wamid: str) -> str:
    """Deterministic Cloud Tasks-safe task id from wamid.

    Format: wa-{base32(sha256(wamid)).lower()[:40]}
    """
    digest = hashlib.sha256(wamid.encode()).digest()
    b32 = base64.b32encode(digest).decode().lower().rstrip("=")
    return f"wa-{b32[:40]}"


# ── GET /whatsapp/webhook — Meta Verification Challenge ──


@router.get("/whatsapp/webhook")
async def wa_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta webhook verification. Returns hub.challenge as PlainTextResponse."""
    if hub_mode == "subscribe" and verify_wa_verify_token(hub_verify_token):
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── POST /whatsapp/webhook — Inbound Messages ──


@router.post("/whatsapp/webhook")
async def wa_webhook(
    request: Request,
    raw_body: bytes = Depends(verify_wa_webhook),
) -> dict:
    """Inbound WhatsApp messages. HMAC verified → fan-out → enqueue Cloud Tasks."""
    if not WHATSAPP_ENABLED:
        return {"status": "disabled"}

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON")

    enqueue_failures = 0
    enqueue_count = 0

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = value.get("metadata", {})

            # Validate phone_number_id ownership per change
            phone_number_id = metadata.get("phone_number_id", "")
            if phone_number_id != WHATSAPP_PHONE_NUMBER_ID:
                logger.warning(
                    "WA webhook phone_number_id mismatch: %s",
                    hashlib.sha256(phone_number_id.encode()).hexdigest()[:12],
                )
                continue

            # Process messages only (ignore statuses)
            messages = value.get("messages", [])
            for msg in messages:
                wamid = msg.get("id", "")
                from_ = msg.get("from", "")
                if not wamid or not from_:
                    continue

                # Record service window for inbound user messages
                service_whatsapp.record_inbound_timestamp(
                    user_phone=from_,
                    phone_number_id=phone_number_id,
                )

                # Enqueue one Cloud Task per message
                task_id = _safe_task_id(wamid)
                try:
                    await _enqueue_process_task(task_id, msg, phone_number_id)
                    enqueue_count += 1
                except _AlreadyExists:
                    enqueue_count += 1  # Already queued = success
                except Exception:
                    logger.warning("Cloud Task enqueue failed for %s", task_id, exc_info=True)
                    enqueue_failures += 1

    # If any enqueue failed unexpectedly, return non-200 so Meta retries
    if enqueue_failures > 0:
        raise HTTPException(
            status_code=500,
            detail=f"{enqueue_failures} enqueue(s) failed",
        )

    return {"status": "ok", "enqueued": enqueue_count}


class _AlreadyExists(Exception):
    """Raised when a Cloud Task already exists (deterministic task ID)."""


async def _enqueue_process_task(
    task_id: str,
    message: dict,
    phone_number_id: str,
) -> None:
    """Enqueue a Cloud Task for processing. Raises _AlreadyExists if duplicate.

    In production, uses google.cloud.tasks_v2. For dev/test, processes inline.
    """
    try:
        from google.cloud import tasks_v2
        from google.api_core.exceptions import AlreadyExists
        from .settings import (
            WA_CLOUD_TASKS_AUDIENCE,
            WA_CLOUD_TASKS_QUEUE_NAME,
        )

        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        location = os.environ.get("CLOUD_TASKS_LOCATION", "us-central1")

        def _create_cloud_task_sync() -> None:
            client = tasks_v2.CloudTasksClient()
            parent = client.queue_path(project, location, WA_CLOUD_TASKS_QUEUE_NAME)
            task_body = json.dumps({
                "message": message,
                "phone_number_id": phone_number_id,
            })
            task = tasks_v2.Task(
                name=f"{parent}/tasks/{task_id}",
                http_request=tasks_v2.HttpRequest(
                    http_method=tasks_v2.HttpMethod.POST,
                    url=WA_CLOUD_TASKS_AUDIENCE,
                    headers={"Content-Type": "application/json"},
                    body=task_body.encode(),
                    oidc_token=tasks_v2.OidcToken(
                        service_account_email=WA_TASKS_INVOKER_EMAIL,
                        audience=WA_CLOUD_TASKS_AUDIENCE,
                    ),
                ),
            )
            try:
                client.create_task(parent=parent, task=task)
            except AlreadyExists as exc:
                raise _AlreadyExists(f"Task {task_id} already exists") from exc

        await asyncio.to_thread(_create_cloud_task_sync)

    except ImportError as exc:
        if os.getenv("K_SERVICE", "").strip():
            raise RuntimeError(
                "google-cloud-tasks package is required in production. "
                "Install google-cloud-tasks>=2.13.0."
            ) from exc

        # Dev/test: process inline (no Cloud Tasks SDK)
        logger.info("Cloud Tasks not available, processing inline: %s", task_id)
        await _process_message(message, phone_number_id, retry_count=0)


# ── POST /whatsapp/process — Cloud Tasks Handler ──


@router.post("/whatsapp/process")
async def wa_process(
    request: Request,
    _: None = Depends(verify_cloud_tasks_oidc),
) -> dict:
    """Process a queued WhatsApp message. Idempotent via state machine."""
    body = await request.json()
    message = body.get("message", {})
    phone_number_id = body.get("phone_number_id", "")

    # Detect retry count from Cloud Tasks header
    retry_count = 0
    retry_header = request.headers.get("X-CloudTasks-TaskRetryCount", "0")
    try:
        retry_count = int(retry_header)
    except (ValueError, TypeError):
        logger.debug("Invalid Cloud Tasks retry header: %s", sanitize_log(retry_header))
        retry_count = 0

    wamid = message.get("id", "")
    if not wamid:
        return {"status": "skipped", "reason": "no wamid"}

    try:
        await _process_message(message, phone_number_id, retry_count=retry_count)
    except Exception as exc:
        is_final = (retry_count + 1) >= WA_CLOUD_TASKS_MAX_ATTEMPTS
        if is_final:
            await service_whatsapp.write_failure_artifacts(
                wamid=wamid,
                error=str(exc),
            )
            return {"status": "failed", "final": True}
        raise  # non-2xx triggers Cloud Tasks retry

    return {"status": "ok"}


async def _process_message(
    message: dict,
    phone_number_id: str,
    *,
    retry_count: int = 0,
) -> None:
    """Process a single inbound WhatsApp message."""
    from_ = message.get("from", "")
    msg_type = message.get("type", "")

    if not from_ or not msg_type:
        return

    # Fire typing indicator immediately (fire-and-forget)
    # Requires inbound message_id to mark as read + show "typing..."
    wamid = message.get("id", "")
    if wamid:
        try:
            await providers.whatsapp_send_typing_indicator(
                access_token=WHATSAPP_ACCESS_TOKEN,
                message_id=wamid,
            )
        except Exception:
            pass  # Never block message processing

    # Generate reply based on message type
    if msg_type == "text":
        text_body = message.get("text", {}).get("body", "")
        reply = await service_whatsapp.handle_text_message(
            from_=from_,
            text=text_body,
        )
    elif msg_type == "image":
        image_data = message.get("image", {})
        reply = await service_whatsapp.handle_image_message(
            from_=from_,
            media_id=image_data.get("id", ""),
            mime_type=image_data.get("mime_type", ""),
            caption=image_data.get("caption", ""),
        )
    elif msg_type == "video":
        video_data = message.get("video", {})
        reply = await service_whatsapp.handle_video_message(
            from_=from_,
            media_id=video_data.get("id", ""),
            mime_type=video_data.get("mime_type", ""),
            caption=video_data.get("caption", ""),
        )
    elif msg_type == "interactive":
        # Handle button_reply or list_reply
        interactive = message.get("interactive", {})
        reply_data = interactive.get("button_reply") or interactive.get("list_reply") or {}
        reply_text = reply_data.get("title", "") or reply_data.get("id", "")
        reply = await service_whatsapp.handle_text_message(
            from_=from_,
            text=f"Selected: {reply_text}" if reply_text else "Interactive response",
        )
    elif msg_type in service_whatsapp.UNSUPPORTED_MESSAGE_TYPES:
        reply = await service_whatsapp.handle_unsupported_message_type(
            from_=from_,
            message_type=msg_type,
        )
    else:
        logger.info("Unknown WA message type received")
        return

    # Send reply and surface provider failures so Cloud Tasks can retry.
    status, send_body = await providers.whatsapp_send_text(
        access_token=WHATSAPP_ACCESS_TOKEN,
        to=from_,
        body=reply,
    )
    if status < 200 or status >= 300:
        logger.warning(
            "WA outbound send failed; provider returned non-2xx",
            extra={"event": "wa_outbound_send_failed"},
        )
        raise RuntimeError(f"WhatsApp send failed with status={status}")


# ── POST /whatsapp/send — Internal API (service-auth) ──


@router.post("/whatsapp/send")
async def wa_send(
    request: Request,
    _: None = Depends(verify_service_auth),
) -> dict:
    """Internal API for during-call sends. Service-auth protected + idempotency."""
    if not WHATSAPP_ENABLED:
        return {"status": "disabled"}

    body = await request.json()
    to = body.get("to", "")
    text = body.get("text", "")
    msg_type = body.get("type", "text")
    idempotency_key = request.headers.get("X-Idempotency-Key", "")

    if not to or not text:
        raise HTTPException(status_code=400, detail="Missing to or text")

    tenant_id = body.get("tenant_id", "public")
    company_id = body.get("company_id", "ekaette-electronics")
    phone_number_id = body.get("phone_number_id", WHATSAPP_PHONE_NUMBER_ID)

    async def _do_send() -> tuple[int, dict]:
        return await service_whatsapp.send_with_template_fallback(
            to=to,
            text=text,
            phone_number_id=phone_number_id,
            tenant_id=tenant_id,
            company_id=company_id,
        )

    if idempotency_key:
        payload_hash = hashlib.sha256(
            json.dumps({"to": to, "text": text, "type": msg_type}, sort_keys=True).encode()
        ).hexdigest()

        status, result = await service_whatsapp.send_with_idempotency(
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            send_fn=_do_send,
        )

        if status == 409:
            raise HTTPException(status_code=409, detail="Idempotency key conflict")
    else:
        status, result = await _do_send()

    if status >= 400:
        raise HTTPException(status_code=status, detail=result)

    return {"status": "ok", "result": result}
