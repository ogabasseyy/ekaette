"""TDD tests for AT SMS callback, send, and campaign endpoints.

Tests the full route → service → provider flow with mocked AT SDK and Gemini.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_sms_app() -> FastAPI:
    """Build a minimal app with just the AT SMS router for testing."""
    from app.api.v1.at.sms import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/at")
    return app


@pytest.fixture()
def sms_client():
    """TestClient with IP allowlist disabled and SMS enabled."""
    with (
        patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
        patch("app.api.v1.at.sms.AT_SMS_ENABLED", True),
    ):
        import app.api.v1.at.security as sec_mod
        sec_mod._at_rate_buckets.clear()
        sec_mod._at_last_prune = 0.0
        app = _build_sms_app()
        yield TestClient(app)


@pytest.fixture()
def sms_client_disabled():
    """TestClient with SMS channel disabled."""
    with (
        patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
        patch("app.api.v1.at.sms.AT_SMS_ENABLED", False),
    ):
        import app.api.v1.at.security as sec_mod
        sec_mod._at_rate_buckets.clear()
        sec_mod._at_last_prune = 0.0
        app = _build_sms_app()
        yield TestClient(app)


# ── Inbound SMS Callback Tests ──


class TestSMSCallback:
    """AT inbound SMS webhook → AI reply → AT send."""

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    def test_inbound_sms_generates_ai_reply(
        self,
        mock_query: AsyncMock,
        mock_send: AsyncMock,
        sms_client: TestClient,
    ) -> None:
        mock_query.return_value = "Your order ships tomorrow!"
        mock_send.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}

        resp = sms_client.post(
            "/api/v1/at/sms/callback",
            data={
                "from": "+2348012345678",
                "to": "12345",
                "text": "Where is my order?",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["reply"] == "Your order ships tomorrow!"
        mock_query.assert_awaited_once()
        mock_send.assert_awaited_once()

    def test_inbound_sms_disabled(self, sms_client_disabled: TestClient) -> None:
        resp = sms_client_disabled.post(
            "/api/v1/at/sms/callback",
            data={"from": "+2348012345678", "to": "12345", "text": "Hello"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    def test_inbound_sms_provider_failure_returns_controlled_error(
        self,
        mock_query: AsyncMock,
        mock_send: AsyncMock,
        sms_client: TestClient,
    ) -> None:
        mock_query.return_value = "Reply text"
        mock_send.side_effect = RuntimeError("provider down")
        resp = sms_client.post(
            "/api/v1/at/sms/callback",
            data={"from": "+2348012345678", "to": "12345", "text": "Where is my order?"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert body["code"] == "AT_SMS_SEND_FAILED"


# ── Outbound SMS Send Tests ──


class TestSMSSend:
    """Single outbound SMS send."""

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_send_sms_success(
        self, mock_send: AsyncMock, sms_client: TestClient
    ) -> None:
        mock_send.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}
        resp = sms_client.post(
            "/api/v1/at/sms/send",
            json={"to": "+2348012345678", "message": "Hello from Ekaette"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_send.assert_awaited_once()

    def test_send_sms_disabled(self, sms_client_disabled: TestClient) -> None:
        resp = sms_client_disabled.post(
            "/api/v1/at/sms/send",
            json={"to": "+2348012345678", "message": "Hello"},
        )
        assert resp.json()["status"] == "disabled"

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_send_sms_truncates_long_message(
        self, mock_send: AsyncMock, sms_client: TestClient
    ) -> None:
        mock_send.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}
        long_msg = "A" * 200
        resp = sms_client.post(
            "/api/v1/at/sms/send",
            json={"to": "+2348012345678", "message": long_msg},
        )
        assert resp.status_code == 200
        # Verify the message sent via provider was truncated
        call_kwargs = mock_send.call_args[1]
        assert "message" in call_kwargs, "send_sms must use 'message' kwarg"
        sent_msg = call_kwargs["message"]
        assert len(sent_msg) <= 160
        assert sent_msg.endswith("...")

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_send_sms_provider_error_returns_502(
        self, mock_send: AsyncMock, sms_client: TestClient
    ) -> None:
        mock_send.side_effect = RuntimeError("provider down")
        resp = sms_client.post(
            "/api/v1/at/sms/send",
            json={"to": "+2348012345678", "message": "Hello from Ekaette"},
        )
        assert resp.status_code == 502
        assert "SMS provider unavailable" in resp.json()["detail"]


# ── Bulk SMS Campaign Tests ──


class TestSMSCampaign:
    """Bulk SMS campaign."""

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_campaign_sms_success(
        self, mock_send: AsyncMock, sms_client: TestClient
    ) -> None:
        mock_send.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}
        resp = sms_client.post(
            "/api/v1/at/sms/campaign",
            json={
                "to": ["+2348012345678", "+2348098765432"],
                "message": "Flash sale today!",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_send.assert_awaited_once()

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_campaign_sms_provider_error_returns_502(
        self, mock_send: AsyncMock, sms_client: TestClient
    ) -> None:
        mock_send.side_effect = RuntimeError("provider down")
        resp = sms_client.post(
            "/api/v1/at/sms/campaign",
            json={
                "to": ["+2348012345678", "+2348098765432"],
                "message": "Flash sale today!",
            },
        )
        assert resp.status_code == 502
        assert "SMS provider unavailable" in resp.json()["detail"]


# ── Service Logic Tests ──


class TestServiceSMS:
    """SMS service business logic."""

    def test_truncate_short_message_unchanged(self) -> None:
        from app.api.v1.at.service_sms import truncate_sms

        assert truncate_sms("Hello") == "Hello"

    def test_truncate_exact_160_unchanged(self) -> None:
        from app.api.v1.at.service_sms import truncate_sms

        msg = "A" * 160
        assert truncate_sms(msg) == msg

    def test_truncate_long_message_with_ellipsis(self) -> None:
        from app.api.v1.at.service_sms import truncate_sms

        msg = "A" * 200
        result = truncate_sms(msg)
        assert len(result) == 160
        assert result.endswith("...")
