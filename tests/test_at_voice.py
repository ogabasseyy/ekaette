"""TDD tests for AT voice callback, outbound, campaign, and transfer endpoints.

Tests the full route → service → provider flow with mocked AT SDK.
Includes idempotency and callback dedup tests.
"""

from __future__ import annotations

import time

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
        patch("app.api.v1.at.voice.SIP_BRIDGE_ENDPOINT", "sip:ekaette@xx.sip.africastalking.com"),
        patch("app.api.v1.at.voice.AT_VIRTUAL_NUMBER", "+23417006000"),
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

    @patch("app.api.v1.at.voice.service_voice.mark_outbound_callback_hint")
    def test_outbound_active_call_marks_fast_answer_hint(
        self,
        mock_mark_hint,
        voice_client: TestClient,
    ) -> None:
        resp = voice_client.post(
            "/api/v1/at/voice/callback",
            data={
                "isActive": "1",
                "sessionId": "AT-outbound-001",
                "direction": "Outbound",
                "callerNumber": "+2348012345678",
                "destinationNumber": "+23417006000",
            },
        )
        assert resp.status_code == 200
        mock_mark_hint.assert_called_once_with(
            tenant_id="public",
            company_id="ekaette-electronics",
            phone="+2348012345678",
        )

    @patch("app.api.v1.at.voice.service_voice.maybe_trigger_post_call_callback", new_callable=AsyncMock)
    def test_ended_call_checks_for_post_call_callback(
        self,
        mock_post_call_callback: AsyncMock,
        voice_client: TestClient,
    ) -> None:
        resp = voice_client.post(
            "/api/v1/at/voice/callback",
            data={
                "isActive": "0",
                "sessionId": "AT-end-001",
                "direction": "Inbound",
                "callerNumber": "+2348012345678",
                "destinationNumber": "+23417006000",
                "durationInSeconds": "2",
            },
        )
        assert resp.status_code == 200
        mock_post_call_callback.assert_awaited_once_with(
            caller_phone="+2348012345678",
            direction="Inbound",
            duration_seconds="2",
            tenant_id="public",
            company_id="ekaette-electronics",
        )


class TestPostCallCallbackFallback:
    @pytest.mark.asyncio
    async def test_short_inbound_call_triggers_flash_callback_with_new_threshold(
        self,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        monkeypatch.setenv("AT_FLASH_CALLBACK_ENABLED", "true")
        monkeypatch.setenv("AT_FLASH_CALLBACK_MAX_DURATION_SECONDS", "8")
        monkeypatch.setattr(service_voice, "AT_CALLBACK_DIAL_FALLBACK", True)
        monkeypatch.setattr(service_voice, "_load_callback_request", lambda *args, **kwargs: None)

        with patch("app.api.v1.at.service_voice.trigger_callback", new_callable=AsyncMock) as mock_trigger:
            await service_voice.maybe_trigger_post_call_callback(
                caller_phone="+2348012345678",
                direction="Inbound",
                duration_seconds="6",
                tenant_id="public",
                company_id="ekaette-electronics",
            )

        mock_trigger.assert_awaited_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="flash_callback",
            reason="Short inbound call requested callback",
        )


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


class TestCallbackRequest:
    @patch("app.api.v1.at.voice.service_voice.register_callback_request")
    def test_callback_request_after_hangup(
        self,
        mock_register,
        voice_client: TestClient,
    ) -> None:
        mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
        resp = voice_client.post(
            "/api/v1/at/voice/callback-request",
            json={"phone": "+2348012345678", "reason": "Low airtime"},
            headers={"Idempotency-Key": "callback-001"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        mock_register.assert_called_once()

    @patch("app.api.v1.at.voice.service_voice.trigger_callback", new_callable=AsyncMock)
    def test_callback_request_immediate(
        self,
        mock_trigger: AsyncMock,
        voice_client: TestClient,
    ) -> None:
        mock_trigger.return_value = {"status": "queued", "phone": "+2348012345678"}
        resp = voice_client.post(
            "/api/v1/at/voice/callback-request",
            json={"phone": "+2348012345678", "mode": "immediate"},
            headers={"Idempotency-Key": "callback-002"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        mock_trigger.assert_awaited_once()


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

    @pytest.mark.asyncio
    @patch("app.api.v1.at.service_voice.mark_outbound_callback_hint")
    @patch("app.api.v1.at.providers.make_call", new_callable=AsyncMock)
    @patch("app.api.v1.at.service_voice.get_callback_prewarm")
    @patch("app.api.v1.at.service_voice.request_callback_prewarm")
    async def test_trigger_callback_waits_for_prewarm_ready(
        self,
        mock_request_prewarm,
        mock_get_prewarm,
        mock_make_call: AsyncMock,
        mock_mark_hint,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
        mock_get_prewarm.side_effect = [
            {"status": "warming", "phone": "+2348012345678"},
            {"status": "ready", "phone": "+2348012345678"},
        ]
        mock_make_call.return_value = {"status": "Queued"}

        async def _no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr(service_voice.asyncio, "sleep", _no_sleep)

        result = await service_voice.trigger_callback(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="manual_callback_request",
        )

        assert result["status"] == "queued"
        mock_request_prewarm.assert_called_once()
        assert mock_get_prewarm.call_count >= 2
        mock_make_call.assert_awaited_once()
        mock_mark_hint.assert_called_once_with(
            tenant_id="public",
            company_id="ekaette-electronics",
            phone="+2348012345678",
        )

    @pytest.mark.asyncio
    @patch("app.api.v1.at.service_voice.clear_callback_prewarm")
    @patch("app.api.v1.at.providers.make_call", new_callable=AsyncMock)
    @patch("app.api.v1.at.service_voice.get_callback_prewarm")
    @patch("app.api.v1.at.service_voice.request_callback_prewarm")
    async def test_trigger_callback_fails_closed_when_prewarm_not_ready(
        self,
        mock_request_prewarm,
        mock_get_prewarm,
        mock_make_call: AsyncMock,
        mock_clear_prewarm,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
        mock_get_prewarm.return_value = {
            "status": "failed",
            "detail": "Callback prewarm timed out",
            "phone": "+2348012345678",
        }

        result = await service_voice.trigger_callback(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="manual_callback_request",
        )

        assert result["status"] == "error"
        assert "timed out" in result["detail"]
        mock_request_prewarm.assert_called_once()
        mock_clear_prewarm.assert_called_once()
        mock_make_call.assert_not_awaited()

    def test_register_callback_request_returns_error_when_persistence_fails(
        self,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        monkeypatch.setattr(
            service_voice,
            "_save_callback_request_verified",
            lambda *args, **kwargs: False,
        )

        result = service_voice.register_callback_request(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_ai_request",
        )

        assert result["status"] == "error"
        assert "queue callback request" in result["detail"].lower()

    def test_register_callback_request_returns_error_when_verification_fails(
        self,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        monkeypatch.setattr(
            service_voice,
            "_save_callback_request_verified",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(service_voice, "_load_callback_request", lambda *args, **kwargs: None)

        result = service_voice.register_callback_request(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_ai_request",
        )

        assert result["status"] == "error"
        assert "verify callback request" in result["detail"].lower()

    def test_load_callback_request_expires_stale_queued_local_record(
        self,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        monkeypatch.setattr(service_voice, "_uses_firestore", lambda: False)
        key = service_voice._callback_key("public", "ekaette-electronics", "+2348012345678")
        service_voice._CALLBACK_REQUESTS_LOCAL[key] = {
            "status": "queued",
            "phone": "+2348012345678",
            "cooldown_until": time.time() - 10,
        }

        result = service_voice._load_callback_request(
            "public",
            "ekaette-electronics",
            "+2348012345678",
        )

        assert result is None
        assert key not in service_voice._CALLBACK_REQUESTS_LOCAL

    def test_register_callback_request_overrides_queued_cooldown_for_explicit_voice_request(
        self,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        now = time.time()
        monkeypatch.setattr(
            service_voice,
            "_load_callback_request",
            lambda *args, **kwargs: {
                "status": "queued",
                "phone": "+2348012345678",
                "source": "voice_ai_request",
                "cooldown_until": now + 1800,
            },
        )
        deleted: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            service_voice,
            "_delete_callback_request",
            lambda tenant_id, company_id, phone: deleted.append((tenant_id, company_id, phone)),
        )
        monkeypatch.setattr(
            service_voice,
            "_save_callback_request_verified",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            service_voice,
            "_load_callback_request",
            lambda *args, **kwargs: {
                "status": "pending",
                "phone": "+2348012345678",
            } if deleted else {
                "status": "queued",
                "phone": "+2348012345678",
                "source": "voice_ai_request",
                "cooldown_until": now + 1800,
            },
        )

        result = service_voice.register_callback_request(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_user_callback_intent",
            trigger_after_hangup=True,
        )

        assert result["status"] == "pending"
        assert deleted == [("public", "ekaette-electronics", "+2348012345678")]

    def test_register_callback_request_keeps_cooldown_for_non_override_source(
        self,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        now = time.time()
        monkeypatch.setattr(
            service_voice,
            "_load_callback_request",
            lambda *args, **kwargs: {
                "status": "queued",
                "phone": "+2348012345678",
                "source": "voice_ai_request",
                "cooldown_until": now + 1800,
            },
        )

        result = service_voice.register_callback_request(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="flash_callback",
            trigger_after_hangup=False,
        )

        assert result["status"] == "cooldown"

    def test_register_callback_request_overrides_failed_cooldown_for_explicit_voice_request(
        self,
        monkeypatch,
    ) -> None:
        from app.api.v1.at import service_voice

        service_voice._CALLBACK_REQUESTS_LOCAL.clear()
        now = time.time()
        deleted: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            service_voice,
            "_delete_callback_request",
            lambda tenant_id, company_id, phone: deleted.append((tenant_id, company_id, phone)),
        )
        monkeypatch.setattr(
            service_voice,
            "_save_callback_request_verified",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            service_voice,
            "_load_callback_request",
            lambda *args, **kwargs: {
                "status": "pending",
                "phone": "+2348012345678",
            } if deleted else {
                "status": "failed",
                "phone": "+2348012345678",
                "source": "manual_callback_request",
                "cooldown_until": now + 1800,
            },
        )

        result = service_voice.register_callback_request(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_agent_callback_promise",
            trigger_after_hangup=True,
        )

        assert result["status"] == "pending"
        assert deleted == [("public", "ekaette-electronics", "+2348012345678")]
