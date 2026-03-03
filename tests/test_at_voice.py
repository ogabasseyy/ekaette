"""TDD tests for AT voice callback, outbound, campaign, and transfer endpoints.

Tests the full route → service → provider flow with mocked AT SDK.
Includes idempotency and callback dedup tests.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_voice_app() -> FastAPI:
    """Build a minimal app with just the AT voice router for testing."""
    from app.api.v1.at.voice import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/at")
    return app


@pytest.fixture()
def voice_client():
    """TestClient with IP allowlist disabled and voice enabled."""
    with (
        patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
        patch("app.api.v1.at.voice.AT_VOICE_ENABLED", True),
        patch("app.api.v1.at.voice.SIP_BRIDGE_ENDPOINT", "sip:ekaette@test.sip.africastalking.com"),
    ):
        import app.api.v1.at.security as sec_mod
        import app.api.v1.at.idempotency as idem_mod
        sec_mod._at_rate_buckets.clear()
        sec_mod._at_last_prune = 0.0
        idem_mod._store.clear()
        idem_mod._callback_seen.clear()
        app = _build_voice_app()
        yield TestClient(app)


@pytest.fixture()
def voice_client_disabled():
    """TestClient with voice channel disabled."""
    with (
        patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
        patch("app.api.v1.at.voice.AT_VOICE_ENABLED", False),
    ):
        import app.api.v1.at.security as sec_mod
        sec_mod._at_rate_buckets.clear()
        sec_mod._at_last_prune = 0.0
        app = _build_voice_app()
        yield TestClient(app)


# ── Callback Tests ──


class TestVoiceCallback:
    """AT voice webhook callback endpoint."""

    def test_active_call_returns_dial_xml(self, voice_client: TestClient) -> None:
        """Active call should return <Dial> XML to bridge to SIP."""
        resp = voice_client.post(
            "/api/v1/at/voice/callback",
            data={
                "isActive": "1",
                "sessionId": "AT-session-001",
                "direction": "Inbound",
                "callerNumber": "+2348012345678",
                "destinationNumber": "+23417006000",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/xml"
        body = resp.text
        assert "<Response>" in body
        assert "<Dial" in body

    def test_ended_call_returns_empty_response(self, voice_client: TestClient) -> None:
        """Ended call (isActive=0) should return empty <Response/>."""
        resp = voice_client.post(
            "/api/v1/at/voice/callback",
            data={
                "isActive": "0",
                "sessionId": "AT-session-002",
                "callerNumber": "+2348012345678",
                "durationInSeconds": "45",
                "amount": "1.50",
            },
        )
        assert resp.status_code == 200
        assert "<Response/>" in resp.text

    def test_disabled_voice_returns_empty_response(
        self, voice_client_disabled: TestClient
    ) -> None:
        """When AT_VOICE_ENABLED=false, callback returns empty response."""
        resp = voice_client_disabled.post(
            "/api/v1/at/voice/callback",
            data={"isActive": "1", "sessionId": "test"},
        )
        assert resp.status_code == 200
        assert "<Response/>" in resp.text

    def test_callback_defaults_to_active(self, voice_client: TestClient) -> None:
        """Missing isActive defaults to '1' (active)."""
        resp = voice_client.post(
            "/api/v1/at/voice/callback",
            data={"sessionId": "AT-default"},
        )
        assert resp.status_code == 200
        assert "<Dial" in resp.text

    def test_duplicate_callback_is_deduplicated(self, voice_client: TestClient) -> None:
        """Same sessionId+isActive delivered twice returns safe response."""
        data = {"isActive": "1", "sessionId": "AT-dedup-test", "callerNumber": "+234"}
        resp1 = voice_client.post("/api/v1/at/voice/callback", data=data)
        assert resp1.status_code == 200
        assert "<Dial" in resp1.text
        # Second delivery — deduplicated
        resp2 = voice_client.post("/api/v1/at/voice/callback", data=data)
        assert resp2.status_code == 200
        assert "<Response/>" in resp2.text


# ── Outbound Call Tests ──


class TestOutboundCall:
    """Outbound voice call initiation with idempotency."""

    @patch("app.api.v1.at.providers.make_call", new_callable=AsyncMock)
    def test_outbound_call_success(
        self, mock_call: AsyncMock, voice_client: TestClient
    ) -> None:
        mock_call.return_value = {"status": "Queued"}
        resp = voice_client.post(
            "/api/v1/at/voice/call",
            json={"to": "+2348012345678"},
            headers={"Idempotency-Key": "call-001"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        mock_call.assert_awaited_once()

    def test_outbound_call_requires_idempotency_key(self, voice_client: TestClient) -> None:
        """Missing Idempotency-Key header returns 400."""
        resp = voice_client.post(
            "/api/v1/at/voice/call",
            json={"to": "+2348012345678"},
        )
        assert resp.status_code == 400
        assert "Idempotency-Key" in resp.json()["detail"]

    @patch("app.api.v1.at.providers.make_call", new_callable=AsyncMock)
    def test_outbound_call_idempotent_replay(
        self, mock_call: AsyncMock, voice_client: TestClient
    ) -> None:
        """Same idempotency key + payload replays cached response."""
        mock_call.return_value = {"status": "Queued"}
        headers = {"Idempotency-Key": "call-replay-001"}
        payload = {"to": "+2348012345678"}

        resp1 = voice_client.post("/api/v1/at/voice/call", json=payload, headers=headers)
        assert resp1.status_code == 200

        # Second request — replayed from cache (provider NOT called again)
        resp2 = voice_client.post("/api/v1/at/voice/call", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json() == resp1.json()
        mock_call.assert_awaited_once()  # Only called once

    @patch("app.api.v1.at.providers.make_call", new_callable=AsyncMock)
    def test_outbound_call_idempotency_key_reuse_different_payload(
        self, mock_call: AsyncMock, voice_client: TestClient
    ) -> None:
        """Same key with different payload returns 409."""
        mock_call.return_value = {"status": "Queued"}
        headers = {"Idempotency-Key": "call-conflict-001"}

        voice_client.post(
            "/api/v1/at/voice/call",
            json={"to": "+2348012345678"},
            headers=headers,
        )
        resp2 = voice_client.post(
            "/api/v1/at/voice/call",
            json={"to": "+2349999999999"},  # Different payload
            headers=headers,
        )
        assert resp2.status_code == 409

    def test_outbound_call_disabled(self, voice_client_disabled: TestClient) -> None:
        resp = voice_client_disabled.post(
            "/api/v1/at/voice/call",
            json={"to": "+2348012345678"},
            headers={"Idempotency-Key": "disabled-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    @patch("app.api.v1.at.providers.make_call", new_callable=AsyncMock)
    def test_outbound_call_provider_error_returns_502(
        self, mock_call: AsyncMock, voice_client: TestClient
    ) -> None:
        mock_call.side_effect = RuntimeError("provider down")
        resp = voice_client.post(
            "/api/v1/at/voice/call",
            json={"to": "+2348012345678"},
            headers={"Idempotency-Key": "call-provider-error-001"},
        )
        assert resp.status_code == 502
        assert "Voice provider unavailable" in resp.json()["detail"]


# ── Campaign Tests ──


class TestVoiceCampaign:
    """Outbound voice campaign with idempotency."""

    @patch("app.api.v1.at.providers.make_call", new_callable=AsyncMock)
    def test_campaign_success(
        self, mock_call: AsyncMock, voice_client: TestClient
    ) -> None:
        mock_call.return_value = {"status": "Queued"}
        resp = voice_client.post(
            "/api/v1/at/voice/campaign",
            json={
                "to": ["+2348012345678", "+2348098765432"],
                "message": "Your order is ready",
            },
            headers={"Idempotency-Key": "campaign-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── Transfer Tests ──


class TestVoiceTransfer:
    """Call transfer to human agent with idempotency."""

    @patch("app.api.v1.at.providers.transfer_call", new_callable=AsyncMock)
    def test_transfer_success(
        self, mock_transfer: AsyncMock, voice_client: TestClient
    ) -> None:
        mock_transfer.return_value = {"status": "Success"}
        resp = voice_client.post(
            "/api/v1/at/voice/transfer",
            json={
                "session_id": "AT-session-001",
                "transfer_to": "+2348099999999",
            },
            headers={"Idempotency-Key": "transfer-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_transfer.assert_awaited_once()

    def test_transfer_disabled(self, voice_client_disabled: TestClient) -> None:
        resp = voice_client_disabled.post(
            "/api/v1/at/voice/transfer",
            json={"session_id": "AT-123", "transfer_to": "+2348099999999"},
            headers={"Idempotency-Key": "transfer-disabled-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    @patch("app.api.v1.at.providers.transfer_call", new_callable=AsyncMock)
    def test_transfer_provider_error_returns_502(
        self, mock_transfer: AsyncMock, voice_client: TestClient
    ) -> None:
        mock_transfer.side_effect = RuntimeError("transfer down")
        resp = voice_client.post(
            "/api/v1/at/voice/transfer",
            json={"session_id": "AT-session-001", "transfer_to": "+2348099999999"},
            headers={"Idempotency-Key": "transfer-provider-error-001"},
        )
        assert resp.status_code == 502
        assert "Voice transfer unavailable" in resp.json()["detail"]


# ── Service Logic Tests ──


class TestServiceVoice:
    """Voice service business logic (XML building, logging)."""

    def test_build_dial_xml_contains_sip_endpoint(self) -> None:
        from app.api.v1.at.service_voice import build_dial_xml

        xml = build_dial_xml("sip:ekaette@xx.sip.africastalking.com", "+23417006000")
        assert '<?xml version="1.0"' in xml
        assert "<Response>" in xml
        assert "<Dial" in xml
        assert "sip:ekaette@xx.sip.africastalking.com" in xml
        assert "+23417006000" in xml

    def test_build_end_xml(self) -> None:
        from app.api.v1.at.service_voice import build_end_xml

        assert build_end_xml() == "<Response/>"

    def test_resolve_tenant_context_defaults(self) -> None:
        from app.api.v1.at.service_voice import resolve_tenant_context

        tenant_id, company_id = resolve_tenant_context("+23417006000")
        assert tenant_id == "public"
        assert company_id == "ekaette-electronics"
