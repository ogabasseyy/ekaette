"""TDD tests for WhatsApp webhook HMAC, service-auth, and Cloud Tasks OIDC security."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.v1.at.wa_security as wa_security


def _build_wa_security_app() -> FastAPI:
    """Build minimal app with a test endpoint using each security dependency."""
    from fastapi import Depends

    app = FastAPI()

    @app.post("/test/webhook")
    async def test_webhook(raw_body: bytes = Depends(wa_security.verify_wa_webhook)):
        return {"status": "ok", "length": len(raw_body)}

    @app.post("/test/service")
    async def test_service(_: None = Depends(wa_security.verify_service_auth)):
        return {"status": "ok"}

    @app.post("/test/oidc")
    async def test_oidc(_: None = Depends(wa_security.verify_cloud_tasks_oidc)):
        return {"status": "ok"}

    return app


APP_SECRET = "test_app_secret_123"
SERVICE_SECRET = "test_service_secret_456"


def _sign_payload(payload: bytes, secret: str = APP_SECRET) -> str:
    """Generate X-Hub-Signature-256 header value."""
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _service_auth_headers(body: str, secret: str = SERVICE_SECRET) -> dict:
    """Generate service-auth headers."""
    timestamp = str(time.time())
    nonce = f"nonce-{time.time_ns()}"
    message = f"{timestamp}:{nonce}:{body}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Auth": sig,
    }


@pytest.fixture()
def wa_security_client():
    """TestClient with WA security configured for best_effort_local mode."""
    with (
        patch("app.api.v1.at.wa_security.WHATSAPP_APP_SECRET", APP_SECRET),
        patch("app.api.v1.at.wa_security.WHATSAPP_VERIFY_TOKEN", "my_verify_token"),
        patch("app.api.v1.at.wa_security.WA_SERVICE_SECRET", SERVICE_SECRET),
        patch("app.api.v1.at.wa_security.WA_SERVICE_SECRET_PREVIOUS", ""),
        patch("app.api.v1.at.wa_security.WA_SERVICE_AUTH_MAX_SKEW_SECONDS", 300),
        patch("app.api.v1.at.wa_security.WA_WEBHOOK_RATE_LIMIT_MODE", "best_effort_local"),
        patch("app.api.v1.at.wa_security.WA_CLOUD_TASKS_QUEUE_NAME", "wa-webhook-processing"),
        patch("app.api.v1.at.wa_security.WA_CLOUD_TASKS_AUDIENCE", "https://test.example.com/process"),
        patch("app.api.v1.at.wa_security.WA_TASKS_INVOKER_EMAIL", "wa-tasks-invoker@test.iam.gserviceaccount.com"),
    ):
        wa_security.reset_nonce_store()
        app = _build_wa_security_app()
        yield TestClient(app)


# ── HMAC Webhook Tests ──


class TestWebhookHMAC:
    """Meta HMAC-SHA256 verification on raw bytes."""

    def test_valid_hmac_passes(self, wa_security_client: TestClient) -> None:
        payload = json.dumps({"test": "data"}).encode()
        sig = _sign_payload(payload)
        resp = wa_security_client.post(
            "/test/webhook",
            content=payload,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_invalid_hmac_rejected(self, wa_security_client: TestClient) -> None:
        payload = json.dumps({"test": "data"}).encode()
        resp = wa_security_client.post(
            "/test/webhook",
            content=payload,
            headers={"X-Hub-Signature-256": "sha256=invalid", "Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_missing_signature_rejected(self, wa_security_client: TestClient) -> None:
        payload = json.dumps({"test": "data"}).encode()
        resp = wa_security_client.post(
            "/test/webhook",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_hmac_on_raw_bytes_not_parsed_json(self, wa_security_client: TestClient) -> None:
        """HMAC must be computed on raw wire bytes, not re-serialized JSON."""
        raw = b'{"key":  "value"}'  # note: extra space
        sig = _sign_payload(raw)
        resp = wa_security_client.post(
            "/test/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200

    def test_constant_time_comparison(self, wa_security_client: TestClient) -> None:
        """Ensure hmac.compare_digest is used (not ==)."""
        import inspect
        source = inspect.getsource(wa_security.verify_wa_webhook)
        assert "compare_digest" in source


class TestWebhookRateLimitMode:
    """Webhook rate-limit mode behavior."""

    def test_edge_enforced_requires_header(self) -> None:
        """In edge_enforced mode, missing header → 403."""
        with (
            patch("app.api.v1.at.wa_security.WHATSAPP_APP_SECRET", APP_SECRET),
            patch("app.api.v1.at.wa_security.WA_WEBHOOK_RATE_LIMIT_MODE", "edge_enforced"),
            patch("app.api.v1.at.wa_security.WA_EDGE_RATELIMIT_HEADER", "X-Edge-RateLimit-Checked"),
        ):
            wa_security.reset_nonce_store()
            app = _build_wa_security_app()
            client = TestClient(app)
            payload = b'{"test": true}'
            sig = _sign_payload(payload)
            resp = client.post(
                "/test/webhook",
                content=payload,
                headers={"X-Hub-Signature-256": sig},
            )
            assert resp.status_code == 403

    def test_edge_enforced_passes_with_header(self) -> None:
        """In edge_enforced mode, with header → passes."""
        with (
            patch("app.api.v1.at.wa_security.WHATSAPP_APP_SECRET", APP_SECRET),
            patch("app.api.v1.at.wa_security.WA_WEBHOOK_RATE_LIMIT_MODE", "edge_enforced"),
            patch("app.api.v1.at.wa_security.WA_EDGE_RATELIMIT_HEADER", "X-Edge-RateLimit-Checked"),
        ):
            wa_security.reset_nonce_store()
            app = _build_wa_security_app()
            client = TestClient(app)
            payload = b'{"test": true}'
            sig = _sign_payload(payload)
            resp = client.post(
                "/test/webhook",
                content=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-Edge-RateLimit-Checked": "1",
                },
            )
            assert resp.status_code == 200


# ── Service Auth Tests ──


class TestServiceAuth:
    """Service-to-service HMAC + timestamp + nonce verification."""

    def test_valid_service_auth_passes(self, wa_security_client: TestClient) -> None:
        body = json.dumps({"to": "+234", "text": "hello"})
        headers = _service_auth_headers(body)
        headers["Content-Type"] = "application/json"
        resp = wa_security_client.post("/test/service", content=body.encode(), headers=headers)
        assert resp.status_code == 200

    def test_missing_headers_rejected(self, wa_security_client: TestClient) -> None:
        resp = wa_security_client.post(
            "/test/service",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_expired_timestamp_rejected(self, wa_security_client: TestClient) -> None:
        body = "{}"
        old_ts = str(time.time() - 600)  # 10 min ago, > 300s skew
        nonce = "nonce-expired"
        message = f"{old_ts}:{nonce}:{body}"
        sig = hmac.new(SERVICE_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        resp = wa_security_client.post(
            "/test/service",
            content=body.encode(),
            headers={
                "X-Service-Timestamp": old_ts,
                "X-Service-Nonce": nonce,
                "X-Service-Auth": sig,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    def test_nonce_replay_rejected(self, wa_security_client: TestClient) -> None:
        body = json.dumps({"to": "+234"})
        ts = str(time.time())
        nonce = "replay-nonce-123"
        message = f"{ts}:{nonce}:{body}"
        sig = hmac.new(SERVICE_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-Service-Timestamp": ts,
            "X-Service-Nonce": nonce,
            "X-Service-Auth": sig,
            "Content-Type": "application/json",
        }
        # First request succeeds
        resp1 = wa_security_client.post("/test/service", content=body.encode(), headers=headers)
        assert resp1.status_code == 200
        # Same nonce replayed → rejected
        resp2 = wa_security_client.post("/test/service", content=body.encode(), headers=headers)
        assert resp2.status_code == 403

    def test_invalid_hmac_rejected(self, wa_security_client: TestClient) -> None:
        body = "{}"
        ts = str(time.time())
        nonce = "nonce-bad-hmac"
        resp = wa_security_client.post(
            "/test/service",
            content=body.encode(),
            headers={
                "X-Service-Timestamp": ts,
                "X-Service-Nonce": nonce,
                "X-Service-Auth": "badhash",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    def test_previous_secret_during_rotation(self) -> None:
        """During rotation, previous secret should also be accepted."""
        prev_secret = "old_secret_789"
        with (
            patch("app.api.v1.at.wa_security.WHATSAPP_APP_SECRET", APP_SECRET),
            patch("app.api.v1.at.wa_security.WA_SERVICE_SECRET", SERVICE_SECRET),
            patch("app.api.v1.at.wa_security.WA_SERVICE_SECRET_PREVIOUS", prev_secret),
            patch("app.api.v1.at.wa_security.WA_SERVICE_AUTH_MAX_SKEW_SECONDS", 300),
            patch("app.api.v1.at.wa_security.WA_WEBHOOK_RATE_LIMIT_MODE", "best_effort_local"),
            patch("app.api.v1.at.wa_security.WA_CLOUD_TASKS_QUEUE_NAME", "wa-webhook-processing"),
            patch("app.api.v1.at.wa_security.WA_CLOUD_TASKS_AUDIENCE", "https://test.example.com"),
            patch("app.api.v1.at.wa_security.WA_TASKS_INVOKER_EMAIL", "wa-tasks-invoker@test.iam.gserviceaccount.com"),
        ):
            wa_security.reset_nonce_store()
            app = _build_wa_security_app()
            client = TestClient(app)

            body = json.dumps({"key": "val"})
            headers = _service_auth_headers(body, secret=prev_secret)
            headers["Content-Type"] = "application/json"
            resp = client.post("/test/service", content=body.encode(), headers=headers)
            assert resp.status_code == 200


# ── Cloud Tasks OIDC Tests ──


class TestCloudTasksOIDC:
    """Cloud Tasks OIDC + queue/task header verification."""

    def test_missing_cloud_tasks_headers_rejected(self, wa_security_client: TestClient) -> None:
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_wrong_queue_name_rejected(self, wa_security_client: TestClient) -> None:
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={
                "X-CloudTasks-QueueName": "wrong-queue",
                "X-CloudTasks-TaskName": "wa-test123",
                "Authorization": "Bearer fake-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    def test_wrong_task_prefix_rejected(self, wa_security_client: TestClient) -> None:
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={
                "X-CloudTasks-QueueName": "wa-webhook-processing",
                "X-CloudTasks-TaskName": "bad-prefix-123",
                "Authorization": "Bearer fake-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    def test_missing_bearer_token_rejected(self, wa_security_client: TestClient) -> None:
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={
                "X-CloudTasks-QueueName": "wa-webhook-processing",
                "X-CloudTasks-TaskName": "wa-test123",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    @patch("app.api.v1.at.wa_security._verify_oidc_token", new_callable=AsyncMock)
    def test_valid_oidc_passes(self, mock_verify, wa_security_client: TestClient) -> None:
        mock_verify.return_value = {
            "aud": "https://test.example.com/process",
            "iss": "https://accounts.google.com",
            "email": "wa-tasks-invoker@test.iam.gserviceaccount.com",
            "email_verified": True,
        }
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={
                "X-CloudTasks-QueueName": "wa-webhook-processing",
                "X-CloudTasks-TaskName": "wa-test123",
                "Authorization": "Bearer valid-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

    @patch("app.api.v1.at.wa_security._verify_oidc_token", new_callable=AsyncMock)
    def test_wrong_audience_rejected(self, mock_verify, wa_security_client: TestClient) -> None:
        mock_verify.return_value = {
            "aud": "https://wrong.example.com",
            "iss": "https://accounts.google.com",
            "email": "wa-tasks-invoker@test.iam.gserviceaccount.com",
        }
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={
                "X-CloudTasks-QueueName": "wa-webhook-processing",
                "X-CloudTasks-TaskName": "wa-test123",
                "Authorization": "Bearer valid-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    @patch("app.api.v1.at.wa_security._verify_oidc_token", new_callable=AsyncMock)
    def test_wrong_service_account_rejected(self, mock_verify, wa_security_client: TestClient) -> None:
        mock_verify.return_value = {
            "aud": "https://test.example.com/process",
            "iss": "https://accounts.google.com",
            "email": "other-sa@test.iam.gserviceaccount.com",
        }
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={
                "X-CloudTasks-QueueName": "wa-webhook-processing",
                "X-CloudTasks-TaskName": "wa-test123",
                "Authorization": "Bearer valid-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    @patch("app.api.v1.at.wa_security._verify_oidc_token", new_callable=AsyncMock)
    def test_oidc_operational_error_returns_500(self, mock_verify, wa_security_client: TestClient) -> None:
        mock_verify.side_effect = RuntimeError("token verifier unavailable")
        resp = wa_security_client.post(
            "/test/oidc",
            content=b"{}",
            headers={
                "X-CloudTasks-QueueName": "wa-webhook-processing",
                "X-CloudTasks-TaskName": "wa-test123",
                "Authorization": "Bearer valid-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 500


# ── Verify Token Tests ──


class TestVerifyToken:
    """Webhook verification token check."""

    def test_valid_token(self) -> None:
        with patch("app.api.v1.at.wa_security.WHATSAPP_VERIFY_TOKEN", "my_token"):
            assert wa_security.verify_wa_verify_token("my_token") is True

    def test_invalid_token(self) -> None:
        with patch("app.api.v1.at.wa_security.WHATSAPP_VERIFY_TOKEN", "my_token"):
            assert wa_security.verify_wa_verify_token("wrong") is False

    def test_empty_config(self) -> None:
        with patch("app.api.v1.at.wa_security.WHATSAPP_VERIFY_TOKEN", ""):
            assert wa_security.verify_wa_verify_token("anything") is False
