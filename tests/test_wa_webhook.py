"""TDD tests for WhatsApp webhook routes: verify, inbound, process, send."""

from __future__ import annotations

import builtins
import base64
import hashlib
import hmac
import json
import time
import uuid

import pytest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

APP_SECRET = "test_app_secret_for_webhook"


def _sign_payload(payload: bytes) -> str:
    sig = hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _build_wa_app() -> FastAPI:
    from app.api.v1.at.whatsapp import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/at")
    return app


def _make_webhook_payload(
    *,
    messages: list[dict] | None = None,
    phone_number_id: str = "test_phone_id",
    statuses: list[dict] | None = None,
) -> dict:
    """Build a realistic Meta webhook payload."""
    value: dict = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "2348124975729",
            "phone_number_id": phone_number_id,
        },
    }
    if messages:
        value["messages"] = messages
        value["contacts"] = [{"profile": {"name": "Test User"}, "wa_id": messages[0].get("from", "")}]
    if statuses:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{"field": "messages", "value": value}]}],
    }


@pytest.fixture()
def wa_client():
    """TestClient with WA webhook security mocked for testing."""
    with (
        patch("app.api.v1.at.wa_security.WHATSAPP_APP_SECRET", APP_SECRET),
        patch("app.api.v1.at.wa_security.WHATSAPP_VERIFY_TOKEN", "test_verify"),
        patch("app.api.v1.at.wa_security.WA_WEBHOOK_RATE_LIMIT_MODE", "best_effort_local"),
        patch("app.api.v1.at.wa_security.WA_SERVICE_SECRET", "svc_secret"),
        patch("app.api.v1.at.wa_security.WA_SERVICE_SECRET_PREVIOUS", ""),
        patch("app.api.v1.at.wa_security.WA_SERVICE_AUTH_MAX_SKEW_SECONDS", 300),
        patch("app.api.v1.at.wa_security.WA_CLOUD_TASKS_QUEUE_NAME", "wa-webhook-processing"),
        patch("app.api.v1.at.wa_security.WA_CLOUD_TASKS_AUDIENCE", "https://test.example.com/process"),
        patch("app.api.v1.at.whatsapp.WHATSAPP_ENABLED", True),
        patch("app.api.v1.at.whatsapp.WHATSAPP_PHONE_NUMBER_ID", "test_phone_id"),
    ):
        from app.api.v1.at.wa_security import reset_nonce_store
        reset_nonce_store()
        app = _build_wa_app()
        yield TestClient(app)


@pytest.fixture()
def wa_client_disabled():
    """TestClient with WhatsApp disabled."""
    with (
        patch("app.api.v1.at.wa_security.WHATSAPP_APP_SECRET", APP_SECRET),
        patch("app.api.v1.at.wa_security.WA_WEBHOOK_RATE_LIMIT_MODE", "best_effort_local"),
        patch("app.api.v1.at.whatsapp.WHATSAPP_ENABLED", False),
    ):
        app = _build_wa_app()
        yield TestClient(app)


# ── GET /whatsapp/webhook — Verification ──


class TestWebhookVerification:
    """Meta webhook verification challenge."""

    def test_valid_verification(self, wa_client: TestClient) -> None:
        resp = wa_client.get(
            "/api/v1/at/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test_verify",
                "hub.challenge": "challenge_string_123",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "challenge_string_123"

    def test_invalid_verify_token(self, wa_client: TestClient) -> None:
        resp = wa_client.get(
            "/api/v1/at/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "challenge",
            },
        )
        assert resp.status_code == 403

    def test_wrong_mode(self, wa_client: TestClient) -> None:
        resp = wa_client.get(
            "/api/v1/at/whatsapp/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.verify_token": "test_verify",
                "hub.challenge": "challenge",
            },
        )
        assert resp.status_code == 403


# ── POST /whatsapp/webhook — Inbound Messages ──


class TestWebhookInbound:
    """Inbound WhatsApp message webhook → enqueue."""

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_inbound_text_enqueued(self, mock_enqueue, wa_client: TestClient) -> None:
        payload = _make_webhook_payload(messages=[{
            "id": "wamid.test123",
            "from": "2348012345678",
            "timestamp": "1709000000",
            "type": "text",
            "text": {"body": "Hello Ekaette"},
        }])
        raw = json.dumps(payload).encode()
        sig = _sign_payload(raw)
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 1
        mock_enqueue.assert_awaited_once()

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_inbound_image_enqueued(self, mock_enqueue, wa_client: TestClient) -> None:
        payload = _make_webhook_payload(messages=[{
            "id": "wamid.img456",
            "from": "2348012345678",
            "timestamp": "1709000000",
            "type": "image",
            "image": {"id": "media_123", "mime_type": "image/jpeg"},
        }])
        raw = json.dumps(payload).encode()
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": _sign_payload(raw)},
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 1

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_batched_fan_out(self, mock_enqueue, wa_client: TestClient) -> None:
        """Multiple messages[] → multiple enqueues."""
        payload = _make_webhook_payload(messages=[
            {"id": "wamid.a", "from": "234", "type": "text", "text": {"body": "Hi"}},
            {"id": "wamid.b", "from": "234", "type": "text", "text": {"body": "Hello"}},
        ])
        raw = json.dumps(payload).encode()
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": _sign_payload(raw)},
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 2
        assert mock_enqueue.await_count == 2

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_status_update_not_enqueued(self, mock_enqueue, wa_client: TestClient) -> None:
        """statuses should be ignored (no enqueue)."""
        payload = _make_webhook_payload(statuses=[{
            "id": "wamid.status1",
            "status": "delivered",
            "timestamp": "1709000000",
            "recipient_id": "234",
        }])
        raw = json.dumps(payload).encode()
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": _sign_payload(raw)},
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 0
        mock_enqueue.assert_not_awaited()

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_phone_number_id_mismatch_skipped(self, mock_enqueue, wa_client: TestClient) -> None:
        payload = _make_webhook_payload(
            messages=[{"id": "wamid.x", "from": "234", "type": "text", "text": {"body": "Hi"}}],
            phone_number_id="wrong_phone_id",
        )
        raw = json.dumps(payload).encode()
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": _sign_payload(raw)},
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 0
        mock_enqueue.assert_not_awaited()

    def test_disabled_channel(self, wa_client_disabled: TestClient) -> None:
        payload = _make_webhook_payload(messages=[{
            "id": "wamid.dis", "from": "234", "type": "text", "text": {"body": "Hi"},
        }])
        raw = json.dumps(payload).encode()
        sig = _sign_payload(raw)
        resp = wa_client_disabled.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": sig},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    def test_hmac_reject(self, wa_client: TestClient) -> None:
        raw = b'{"test": true}'
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )
        assert resp.status_code == 403

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_already_exists_treated_as_success(self, mock_enqueue, wa_client: TestClient) -> None:
        from app.api.v1.at.whatsapp import _AlreadyExists
        mock_enqueue.side_effect = _AlreadyExists("duplicate")
        payload = _make_webhook_payload(messages=[{
            "id": "wamid.dup", "from": "234", "type": "text", "text": {"body": "Hi"},
        }])
        raw = json.dumps(payload).encode()
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": _sign_payload(raw)},
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 1

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_enqueue_failure_returns_500(self, mock_enqueue, wa_client: TestClient) -> None:
        mock_enqueue.side_effect = RuntimeError("queue down")
        payload = _make_webhook_payload(messages=[{
            "id": "wamid.fail", "from": "234", "type": "text", "text": {"body": "Hi"},
        }])
        raw = json.dumps(payload).encode()
        resp = wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": _sign_payload(raw)},
        )
        assert resp.status_code == 500

    @patch("app.api.v1.at.whatsapp._enqueue_process_task", new_callable=AsyncMock)
    def test_service_window_recorded_before_enqueue(self, mock_enqueue, wa_client: TestClient) -> None:
        """Service window timestamp must be recorded synchronously before 200."""
        from app.api.v1.at import service_whatsapp
        service_whatsapp.reset_service_windows()

        payload = _make_webhook_payload(messages=[{
            "id": "wamid.sw1", "from": "2348000000000", "type": "text", "text": {"body": "Hi"},
        }])
        raw = json.dumps(payload).encode()
        wa_client.post(
            "/api/v1/at/whatsapp/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": _sign_payload(raw)},
        )
        assert service_whatsapp.check_service_window(
            "2348000000000", "test_phone_id"
        )


# ── Safe Task ID Tests ──


class TestSafeTaskId:
    """Deterministic task ID from wamid."""

    def test_deterministic(self) -> None:
        from app.api.v1.at.whatsapp import _safe_task_id
        id1 = _safe_task_id("wamid.test123")
        id2 = _safe_task_id("wamid.test123")
        assert id1 == id2

    def test_prefix(self) -> None:
        from app.api.v1.at.whatsapp import _safe_task_id
        assert _safe_task_id("wamid.x").startswith("wa-")

    def test_cloud_tasks_safe(self) -> None:
        """Task ID must be lowercase alphanumeric + hyphens."""
        from app.api.v1.at.whatsapp import _safe_task_id
        tid = _safe_task_id("wamid.HBgNMjM0ODEyNDk3NTcy")
        assert tid == tid.lower()
        assert all(c.isalnum() or c == "-" for c in tid)


class TestCloudTasksImportBehavior:
    async def test_enqueue_import_error_falls_back_inline_in_dev(self, monkeypatch):
        from app.api.v1.at import whatsapp as wa_mod

        mock_process = AsyncMock()
        monkeypatch.setattr(wa_mod, "_process_message", mock_process)
        monkeypatch.delenv("K_SERVICE", raising=False)

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith("google.cloud") or name.startswith("google.api_core.exceptions"):
                raise ImportError("missing google-cloud-tasks")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            await wa_mod._enqueue_process_task("task-dev", {"id": "wamid.dev"}, "phone-id")

        mock_process.assert_awaited_once()

    async def test_enqueue_import_error_raises_in_production(self, monkeypatch):
        from app.api.v1.at import whatsapp as wa_mod

        monkeypatch.setenv("K_SERVICE", "wa-service")
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith("google.cloud") or name.startswith("google.api_core.exceptions"):
                raise ImportError("missing google-cloud-tasks")
            return real_import(name, globals, locals, fromlist, level)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(RuntimeError, match="google-cloud-tasks"),
        ):
            await wa_mod._enqueue_process_task("task-prod", {"id": "wamid.prod"}, "phone-id")


# ── POST /whatsapp/process — Process Handler ──


def _build_wa_app_with_oidc_bypass() -> FastAPI:
    """Build app with OIDC dependency overridden for process handler tests."""
    from app.api.v1.at.whatsapp import router
    from app.api.v1.at.wa_security import verify_cloud_tasks_oidc

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/at")

    async def _noop():
        return None

    app.dependency_overrides[verify_cloud_tasks_oidc] = _noop
    return app


class TestProcessHandler:
    """Cloud Tasks process handler."""

    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    @patch("app.api.v1.at.service_whatsapp._get_adk_runner_and_service", return_value=(None, None, None, None, ""))
    def test_process_text_message(self, _mock_adk, mock_query, mock_send) -> None:
        mock_query.return_value = "AI reply here"
        mock_send.return_value = (200, {"messages": [{"id": "wamid.out1"}]})
        app = _build_wa_app_with_oidc_bypass()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/at/whatsapp/process",
            json={
                "message": {
                    "id": "wamid.proc1",
                    "from": "2348012345678",
                    "type": "text",
                    "text": {"body": "Where is my order?"},
                },
                "phone_number_id": "test_phone_id",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_query.assert_awaited_once()
        mock_send.assert_awaited_once()

    def test_process_no_wamid_skipped(self) -> None:
        app = _build_wa_app_with_oidc_bypass()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/at/whatsapp/process",
            json={"message": {}, "phone_number_id": "test"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    @patch("app.api.v1.at.service_whatsapp.handle_unsupported_message_type", new_callable=AsyncMock)
    def test_process_unsupported_type(self, mock_unsupported, mock_send) -> None:
        mock_unsupported.return_value = "Sorry, I can't process document messages yet."
        mock_send.return_value = (200, {})
        app = _build_wa_app_with_oidc_bypass()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/at/whatsapp/process",
            json={
                "message": {
                    "id": "wamid.doc1",
                    "from": "234",
                    "type": "document",
                    "document": {"id": "media_d"},
                },
                "phone_number_id": "test",
            },
        )
        assert resp.status_code == 200
        mock_unsupported.assert_awaited_once()

    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    def test_process_retries_when_send_fails(self, mock_query, mock_send) -> None:
        mock_query.return_value = "AI reply here"
        mock_send.return_value = (500, {"error": {"message": "provider error"}})
        app = _build_wa_app_with_oidc_bypass()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/at/whatsapp/process",
            json={
                "message": {
                    "id": "wamid.proc-fail",
                    "from": "2348012345678",
                    "type": "text",
                    "text": {"body": "Where is my order?"},
                },
                "phone_number_id": "test_phone_id",
            },
        )
        assert resp.status_code == 500

    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    @patch("app.api.v1.at.service_whatsapp.handle_video_message", new_callable=AsyncMock)
    def test_process_skips_outbound_send_when_media_is_handed_to_live_call(
        self,
        mock_handle_video,
        mock_send,
    ) -> None:
        mock_handle_video.return_value = ""
        app = _build_wa_app_with_oidc_bypass()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/at/whatsapp/process",
            json={
                "message": {
                    "id": "wamid.video1",
                    "from": "2348012345678",
                    "type": "video",
                    "video": {"id": "media-video-1", "mime_type": "video/mp4"},
                },
                "phone_number_id": "test_phone_id",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_handle_video.assert_awaited_once()
        mock_send.assert_not_awaited()


# ── POST /whatsapp/send — Internal API ──


class TestSendEndpoint:
    """Internal API for during-call sends."""

    def test_send_requires_service_auth(self, wa_client: TestClient) -> None:
        """Without service-auth headers → 403."""
        resp = wa_client.post(
            "/api/v1/at/whatsapp/send",
            json={"to": "+234", "text": "hello"},
        )
        assert resp.status_code == 403

    @patch("app.api.v1.at.providers.whatsapp_send_image", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.whatsapp_upload_media", new_callable=AsyncMock)
    def test_send_image_with_service_auth(
        self,
        mock_upload,
        mock_send_image,
        wa_client: TestClient,
    ) -> None:
        mock_upload.return_value = "media-123"
        mock_send_image.return_value = (200, {"messages": [{"id": "wamid.image1"}]})

        payload = {
            "to": "+2348012345678",
            "type": "image",
            "media_base64": base64.b64encode(b"\x89PNG").decode(),
            "mime_type": "image/png",
            "caption": "Preview image",
            "tenant_id": "public",
            "company_id": "ekaette-electronics",
        }
        body = json.dumps(payload)
        timestamp = str(time.time())
        nonce = uuid.uuid4().hex
        sig = hmac.new(
            b"svc_secret",
            f"{timestamp}:{nonce}:{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "X-Service-Timestamp": timestamp,
            "X-Service-Nonce": nonce,
            "X-Service-Auth": sig,
            "X-Idempotency-Key": "wa-image-test",
            "Content-Type": "application/json",
        }

        resp = wa_client.post(
            "/api/v1/at/whatsapp/send",
            data=body,
            headers=headers,
        )

        assert resp.status_code == 200
        mock_upload.assert_awaited_once()
        mock_send_image.assert_awaited_once()
        upload_kwargs = mock_upload.await_args.kwargs
        assert upload_kwargs["media_bytes"] == b"\x89PNG"
        assert upload_kwargs["mime_type"] == "image/png"
        send_kwargs = mock_send_image.await_args.kwargs
        assert send_kwargs["media_id"] == "media-123"
        assert send_kwargs["to"] == "+2348012345678"
        assert send_kwargs["caption"] == "Preview image"
