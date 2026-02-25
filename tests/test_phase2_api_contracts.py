"""Phase 2 — API contract tests (TDD Red).

Tests for:
1. Token endpoint: canonical fields in response when REGISTRY_ENABLED
2. WebSocket session_started: canonical fields when registry state present
3. Voice resolution: _native_audio_live_config uses voice override
4. Onboarding config endpoint: GET /api/onboarding/config
5. build_onboarding_config helper in registry_loader
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient


# ═══ Fixtures ═══


@pytest.fixture
def app():
    from main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def main_module():
    import main
    return main


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_rate_limit_state(main_module):
    main_module._rate_limit_buckets.clear()
    yield
    main_module._rate_limit_buckets.clear()


class _FakeAsyncAuthTokens:
    def __init__(self):
        self.last_config = None

    async def create(self, *, config=None):
        self.last_config = config
        return SimpleNamespace(name="auth_tokens/test-token")


class _FakeTokenClient:
    def __init__(self):
        self.aio = SimpleNamespace(auth_tokens=_FakeAsyncAuthTokens())


def _fake_live_event():
    return SimpleNamespace(
        content=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        actions=None,
        turn_complete=False,
        usage_metadata=None,
        live_session_resumption_update=None,
        author="ekaette_router",
    )


async def _fake_run_live(**kwargs):
    yield _fake_live_event()
    await asyncio.sleep(0)


# ═══ Token Endpoint: Canonical Fields ═══


class TestTokenEndpointCanonicalFields:
    """When REGISTRY_ENABLED=true and session state has canonical keys,
    the token response should include them."""

    @pytest.mark.asyncio
    async def test_token_includes_canonical_fields_when_registry_enabled(
        self, client, main_module, monkeypatch
    ):
        fake_client = _FakeTokenClient()
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setenv("REGISTRY_ENABLED", "true")

        response = await client.post(
            "/api/token",
            headers={"Origin": "http://localhost:5173"},
            json={
                "userId": "user_123",
                "tenantId": "public",
                "industry": "electronics",
                "companyId": "ekaette-electronics",
            },
        )
        assert response.status_code == 200
        payload = response.json()

        # Legacy fields still present
        assert "token" in payload
        assert "industry" in payload
        assert "companyId" in payload
        assert "tenantId" in payload

        # Canonical fields added
        assert "voice" in payload
        assert isinstance(payload["voice"], str)

    @pytest.mark.asyncio
    async def test_token_legacy_fields_unchanged_without_registry(
        self, client, main_module, monkeypatch
    ):
        """Without REGISTRY_ENABLED, token response stays the same as Phase 0."""
        fake_client = _FakeTokenClient()
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.delenv("REGISTRY_ENABLED", raising=False)

        response = await client.post(
            "/api/token",
            headers={"Origin": "http://localhost:5173"},
            json={
                "userId": "user_123",
                "tenantId": "public",
                "industry": "electronics",
                "companyId": "ekaette-electronics",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        # Phase 0 baseline fields + voice (always present in Phase 2)
        assert set(payload.keys()) == {
            "token", "expiresAt", "maxUses", "industry", "companyId",
            "tenantId", "userId", "model", "fallbackModelUsed",
            "manualVadActive", "vadMode", "voice",
        }


# ═══ WebSocket session_started: Canonical Fields ═══


class TestSessionStartedCanonicalFields:
    """session_started message should include canonical fields when
    session state has them (registry path)."""

    def test_session_started_includes_canonical_fields_when_present(
        self, app, main_module, monkeypatch
    ):
        """When session state has canonical keys, session_started exposes them."""
        from tests.conftest import make_session_state

        state = make_session_state("electronics", "ekaette-electronics")
        # Add canonical keys as registry would
        state["app:tenant_id"] = "public"
        state["app:industry_template_id"] = "electronics"
        state["app:capabilities"] = ["catalog_lookup", "valuation_tradein"]
        state["app:registry_version"] = "v1-abc123"

        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return SimpleNamespace(id=session_id, state=dict(state))

        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))

        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=electronics&companyId=ekaette-electronics",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["type"] == "session_started"

                # Legacy fields preserved
                assert payload["industry"] == "electronics"
                assert payload["companyId"] == "ekaette-electronics"
                assert "voice" in payload
                assert "sessionId" in payload

                # Canonical fields present
                assert payload["tenantId"] == "public"
                assert payload["industryTemplateId"] == "electronics"
                assert payload["capabilities"] == ["catalog_lookup", "valuation_tradein"]
                assert payload["registryVersion"] == "v1-abc123"

                ws.close(code=1000)

    def test_session_started_omits_canonical_when_not_in_state(
        self, app, main_module, monkeypatch
    ):
        """Without canonical keys in state, session_started is unchanged from Phase 0."""
        from tests.conftest import make_session_state

        state = make_session_state("hotel", "ekaette-hotel")
        # No canonical keys — legacy-only

        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return SimpleNamespace(id=session_id, state=dict(state))

        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))

        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=hotel&companyId=ekaette-hotel",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["type"] == "session_started"
                assert payload["industry"] == "hotel"

                # Canonical fields absent (not in session state)
                assert "industryTemplateId" not in payload
                assert "capabilities" not in payload
                assert "registryVersion" not in payload

                ws.close(code=1000)


# ═══ Voice Resolution ═══


class TestVoiceResolution:
    """_native_audio_live_config should respect voice_override parameter."""

    def test_voice_override_used_when_provided(self, main_module):
        config = main_module._native_audio_live_config("electronics", voice_override="Charon")
        speech_config = config["speech_config"]
        voice_name = speech_config.voice_config.prebuilt_voice_config.voice_name
        assert voice_name == "Charon"

    def test_fallback_to_industry_map_without_override(self, main_module):
        config = main_module._native_audio_live_config("hotel")
        speech_config = config["speech_config"]
        voice_name = speech_config.voice_config.prebuilt_voice_config.voice_name
        assert voice_name == "Puck"

    def test_voice_override_none_uses_industry_map(self, main_module):
        config = main_module._native_audio_live_config("fashion", voice_override=None)
        speech_config = config["speech_config"]
        voice_name = speech_config.voice_config.prebuilt_voice_config.voice_name
        assert voice_name == "Kore"

    def test_session_started_voice_matches_state(
        self, app, main_module, monkeypatch
    ):
        """When session has app:voice, session_started voice should match."""
        from tests.conftest import make_session_state

        state = make_session_state("hotel", "acme-hotel")
        state["app:voice"] = "Charon"  # Company override

        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return SimpleNamespace(id=session_id, state=dict(state))

        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))

        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=hotel&companyId=acme-hotel",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["voice"] == "Charon"
                ws.close(code=1000)


# ═══ Onboarding Config Endpoint ═══


class TestOnboardingConfigEndpoint:
    """GET /api/onboarding/config returns templates + companies."""

    @pytest.mark.asyncio
    async def test_returns_templates_in_compat_mode(self, client):
        """Without registry, builds response from local configs."""
        response = await client.get(
            "/api/onboarding/config?tenantId=public",
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.status_code == 200
        data = response.json()

        assert data["tenantId"] == "public"
        assert isinstance(data["templates"], list)
        assert len(data["templates"]) >= 4  # electronics, hotel, automotive, fashion

        template_ids = [t["id"] for t in data["templates"]]
        assert "electronics" in template_ids
        assert "hotel" in template_ids
        assert "automotive" in template_ids
        assert "fashion" in template_ids

        # Each template has required fields
        for template in data["templates"]:
            assert "id" in template
            assert "label" in template
            assert "defaultVoice" in template
            assert "theme" in template
            assert "capabilities" in template
            assert "status" in template

    @pytest.mark.asyncio
    async def test_returns_companies(self, client):
        response = await client.get(
            "/api/onboarding/config?tenantId=public",
            headers={"Origin": "http://localhost:5173"},
        )
        data = response.json()
        assert isinstance(data["companies"], list)
        assert len(data["companies"]) >= 4

        company_ids = [c["id"] for c in data["companies"]]
        assert "ekaette-electronics" in company_ids
        assert "ekaette-hotel" in company_ids

        for company in data["companies"]:
            assert "id" in company
            assert "templateId" in company
            assert "displayName" in company

    @pytest.mark.asyncio
    async def test_returns_defaults(self, client):
        response = await client.get(
            "/api/onboarding/config?tenantId=public",
            headers={"Origin": "http://localhost:5173"},
        )
        data = response.json()
        assert "defaults" in data
        assert "templateId" in data["defaults"]
        assert "companyId" in data["defaults"]

    @pytest.mark.asyncio
    async def test_missing_tenant_id_returns_400(self, client):
        response = await client.get(
            "/api/onboarding/config",
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_all_templates_have_active_status(self, client):
        """Compat mode templates should all be active."""
        response = await client.get(
            "/api/onboarding/config?tenantId=public",
            headers={"Origin": "http://localhost:5173"},
        )
        data = response.json()
        for template in data["templates"]:
            assert template["status"] == "active"


# ═══ build_onboarding_config helper ═══


class TestBuildOnboardingConfig:
    """Test the registry_loader helper that builds onboarding config."""

    @pytest.mark.asyncio
    async def test_compat_mode_returns_all_local_industries(self):
        from app.configs.registry_loader import build_onboarding_config

        config = await build_onboarding_config(None, "public")
        assert config["tenantId"] == "public"
        assert len(config["templates"]) >= 4

        ids = [t["id"] for t in config["templates"]]
        assert "electronics" in ids
        assert "hotel" in ids

    @pytest.mark.asyncio
    async def test_compat_mode_template_shape(self):
        from app.configs.registry_loader import build_onboarding_config

        config = await build_onboarding_config(None, "public")
        electronics = next(t for t in config["templates"] if t["id"] == "electronics")

        assert electronics["label"] == "Electronics & Gadgets"
        assert electronics["defaultVoice"] == "Aoede"
        assert isinstance(electronics["theme"], dict)
        assert "accent" in electronics["theme"]
        assert electronics["status"] == "active"
        assert isinstance(electronics["capabilities"], list)

    @pytest.mark.asyncio
    async def test_compat_mode_company_shape(self):
        from app.configs.registry_loader import build_onboarding_config

        config = await build_onboarding_config(None, "public")
        companies = config["companies"]
        elec_co = next(c for c in companies if c["id"] == "ekaette-electronics")

        assert elec_co["templateId"] == "electronics"
        assert isinstance(elec_co["displayName"], str)

    @pytest.mark.asyncio
    async def test_compat_mode_defaults(self):
        from app.configs.registry_loader import build_onboarding_config

        config = await build_onboarding_config(None, "public")
        assert config["defaults"]["templateId"] == "electronics"
        assert config["defaults"]["companyId"] == "ekaette-electronics"
