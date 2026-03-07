"""Tests for the FastAPI application (main.py)."""

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient


@pytest.fixture
def app():
    """Import the FastAPI app."""
    from main import app
    return app


@pytest.fixture
def main_module():
    """Import main module for helper-level assertions."""
    import main
    return main


@pytest.fixture
def admin_runtime():
    """Runtime proxy used by admin route modules."""
    from app.api.v1.admin.runtime import runtime

    return runtime


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client for testing FastAPI endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_rate_limit_state(main_module):
    """Keep in-memory rate-limit state isolated between tests."""
    from app.api.v1.admin import settings as admin_settings

    main_module._rate_limit_buckets.clear()
    admin_settings.reset_runtime_state()
    yield
    main_module._rate_limit_buckets.clear()
    admin_settings.reset_runtime_state()


class TestHealthEndpoint:
    """Test the health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        response = await client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_returns_json(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_includes_app_name(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "app" in data
        assert data["app"] == "ekaette"


class TestCORSHeaders:
    """Test CORS middleware is properly configured."""

    @pytest.mark.asyncio
    async def test_cors_allows_configured_origin(self, client):
        response = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    @pytest.mark.asyncio
    async def test_cors_blocks_unknown_origin(self, client):
        response = await client.options(
            "/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = response.headers.get("access-control-allow-origin")
        assert allow_origin != "http://evil.example.com"


class TestWebSocketEndpoint:
    """Test WebSocket endpoint is registered."""

    def test_ws_route_registered(self, app):
        """The /ws/{user_id}/{session_id} WebSocket route exists in the app."""
        ws_paths = [
            route.path for route in app.routes
            if hasattr(route, "path") and "/ws/" in route.path
        ]
        assert "/ws/{user_id}/{session_id}" in ws_paths

    def test_ws_resumption_preserves_company_state_from_session(self, app, main_module, monkeypatch):
        """Existing session company/industry should override query params on reconnect."""

        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return SimpleNamespace(
                    id=session_id,
                    state={
                        "app:industry": "hotel",
                        "app:industry_config": {"name": "Hotels & Hospitality", "voice": "Puck"},
                        "app:company_id": "ekaette-hotel",
                        "app:company_profile": {
                            "name": "Ekaette Grand Hotel",
                            "facts": {"rooms": 120},
                        },
                        "app:company_knowledge": [
                            {"id": "kb-hotel-checkout", "title": "Late checkout policy", "text": "Late checkout until 1 PM."}
                        ],
                    },
                )

        async def _fake_run_live(**kwargs):
            yield SimpleNamespace(
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
            await asyncio.sleep(0)

        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))

        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=electronics&companyId=ekaette-electronics",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["type"] == "session_started"
                assert payload["industry"] == "hotel"
                assert payload["companyId"] == "ekaette-hotel"
                ws.close(code=1000)

    def test_ws_missing_origin_rejected_by_default(self, app):
        with TestClient(app) as tc:
            with pytest.raises(Exception) as excinfo:
                with tc.websocket_connect("/ws/user_123/session_abc"):
                    pass
        # Starlette raises WebSocketDisconnect; assert close code explicitly.
        assert getattr(excinfo.value, "code", None) == 1008

    def test_ws_invalid_path_params_rejected(self, app):
        with TestClient(app) as tc:
            with pytest.raises(Exception) as excinfo:
                with tc.websocket_connect(
                    "/ws/user%20bad/session_abc",
                    headers={"origin": "http://localhost:5173"},
                ):
                    pass
        assert getattr(excinfo.value, "code", None) == 1008

    def test_ws_missing_origin_can_be_allowed_by_policy(
        self, app, main_module, monkeypatch, caplog
    ):
        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return SimpleNamespace(
                    id=session_id,
                    state={
                        "app:industry": "electronics",
                        "app:industry_config": {
                            "name": "Electronics & Gadgets",
                            "voice": "Aoede",
                        },
                        "app:company_id": "ekaette-electronics",
                        "app:company_profile": {},
                        "app:company_knowledge": [],
                    },
                )

        async def _fake_run_live(**kwargs):
            yield SimpleNamespace(
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
            await asyncio.sleep(0)

        monkeypatch.setattr(main_module, "ALLOW_MISSING_WS_ORIGIN", True)
        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))
        monkeypatch.setattr(main_module, "_registry_enabled", lambda: False)

        caplog.set_level(logging.DEBUG, logger=main_module.__name__)
        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=electronics&companyId=ekaette-electronics"
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["type"] == "session_started"
                assert payload["industry"] == "electronics"
                ws.close(code=1000)

        assert "WebSocket accepted without Origin header by policy" in caplog.text

    def test_ws_missing_token_rejected_when_secret_configured(
        self, app, main_module, monkeypatch
    ):
        from app.api.v1.public import ws_auth

        secret = "test-ws-secret-route"
        monkeypatch.setattr(main_module, "WS_TOKEN_SECRET", secret)
        monkeypatch.setattr(ws_auth, "_WS_TOKEN_SECRET", secret)
        ws_auth._used_jtis.clear()

        with TestClient(app) as tc:
            with pytest.raises(Exception) as excinfo:
                with tc.websocket_connect(
                    "/ws/user_123/session_abc?industry=electronics&companyId=ekaette-electronics",
                    headers={"origin": "http://localhost:5173"},
                ):
                    pass

        assert getattr(excinfo.value, "code", None) == 4401

    def test_ws_token_claims_override_query_context_end_to_end(
        self, app, main_module, monkeypatch
    ):
        from app.api.v1.public import ws_auth

        secret = "test-ws-secret-route"
        monkeypatch.setattr(main_module, "WS_TOKEN_SECRET", secret)
        monkeypatch.setattr(ws_auth, "_WS_TOKEN_SECRET", secret)
        ws_auth._used_jtis.clear()

        async def _fake_run_live(*args, **kwargs):
            yield SimpleNamespace(
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
            await asyncio.sleep(0)

        monkeypatch.setattr(main_module, "_tenant_allowed", lambda tenant_id: tenant_id == "tenant-from-token")
        monkeypatch.setattr(main_module, "session_service", _NewSessionService())
        monkeypatch.setattr(type(main_module.runner), "run_live", _fake_run_live)
        monkeypatch.setattr(main_module, "_registry_enabled", lambda: False)
        monkeypatch.setattr(main_module, "load_industry_config", _stub_industry_config_loader)
        monkeypatch.setattr(main_module, "load_company_profile", _stub_company_profile_loader)
        monkeypatch.setattr(main_module, "load_company_knowledge", _stub_company_knowledge_loader)

        token = ws_auth.create_ws_token(
            "user_123",
            "tenant-from-token",
            "company-from-token",
            300,
        )

        with TestClient(app) as tc:
            with tc.websocket_connect(
                (
                    "/ws/user_123/session_abc"
                    "?industry=electronics"
                    "&tenantId=blocked"
                    "&companyId=query-company"
                    f"&token={token}"
                ),
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["type"] == "session_started"
                assert payload["companyId"] == "company-from-token"
                ws.close(code=1000)


class TestSecurityHelpers:
    """Test allowlist parsing and origin validation helpers."""

    def test_parse_allowlist_strips_whitespace_and_empty(self, main_module):
        parsed = main_module._parse_allowlist(" http://a.com, ,http://b.com ,,")
        assert parsed == ["http://a.com", "http://b.com"]

    def test_is_origin_allowed_true_for_known_origin(self, main_module):
        assert main_module._is_origin_allowed("http://localhost:5173") is True

    def test_is_origin_allowed_false_for_unknown_origin(self, main_module):
        assert main_module._is_origin_allowed("http://evil.example.com") is False

    def test_is_origin_allowed_true_when_origin_missing(self, main_module):
        """None origin = same-origin proxy request (Vite/nginx), always allowed."""
        assert main_module._is_origin_allowed(None) is True

    def test_websocket_origin_allowed_false_when_origin_missing_by_default(self, main_module):
        assert main_module._is_websocket_origin_allowed(None) is False

    def test_websocket_origin_allowed_true_when_origin_missing_if_enabled(
        self, main_module, monkeypatch
    ):
        monkeypatch.setattr(main_module, "ALLOW_MISSING_WS_ORIGIN", True)
        assert main_module._is_websocket_origin_allowed(None) is True


class TestStructuredMessageExtraction:
    def test_extracts_server_message_from_state_delta(self, main_module):
        state_delta = {
            "temp:last_server_message": {
                "id": 4,
                "type": "valuation_result",
                "deviceName": "iPhone 14 Pro",
            }
        }
        message = main_module._extract_server_message_from_state_delta(state_delta)
        assert message is not None
        assert message["type"] == "valuation_result"
        assert message["id"] == 4

    def test_returns_none_for_missing_server_message(self, main_module):
        message = main_module._extract_server_message_from_state_delta(
            {"app:industry": "electronics"}
        )
        assert message is None


class TestIdempotencyBackends:
    @pytest.mark.asyncio
    async def test_firestore_idempotency_preflight_and_replay(self, monkeypatch):
        from app.api.v1.admin import idempotency as idempotency_module
        from app.api.v1.admin import shared as admin_shared
        from app.api.v1.admin import settings as admin_settings
        import uuid

        class _FakeSnapshot:
            def __init__(self, payload):
                self._payload = payload
                self.exists = payload is not None

            def to_dict(self):
                return dict(self._payload) if isinstance(self._payload, dict) else {}

        class _FakeDocRef:
            def __init__(self, store, path):
                self._store = store
                self._path = path

            def collection(self, name):
                return _FakeDocRef(self._store, self._path + ("collection", str(name)))

            def document(self, doc_id):
                return _FakeDocRef(self._store, self._path + ("document", str(doc_id)))

            async def create(self, payload):
                if self._path in self._store:
                    from google.api_core.exceptions import AlreadyExists

                    raise AlreadyExists("exists")
                self._store[self._path] = dict(payload)

            async def get(self):
                return _FakeSnapshot(self._store.get(self._path))

            async def set(self, payload, merge=True):
                if merge and self._path in self._store:
                    current = dict(self._store[self._path])
                    current.update(dict(payload))
                    self._store[self._path] = current
                    return
                self._store[self._path] = dict(payload)

            async def delete(self):
                self._store.pop(self._path, None)

        class _FakeDb:
            def __init__(self):
                self.store = {}

            def collection(self, name):
                return _FakeDocRef(self.store, ("collection", str(name)))

        fake_db = _FakeDb()
        admin_settings.reset_runtime_state()
        monkeypatch.setattr(admin_settings, "IDEMPOTENCY_STORE_BACKEND", "firestore")
        monkeypatch.setattr(admin_shared, "_registry_db_client", lambda: fake_db)
        idempotency_key_value = f"key-{uuid.uuid4().hex}"

        idempotency_key, fingerprint, response = await idempotency_module._idempotency_preflight(
            scope="admin_test",
            tenant_id="public",
            payload={"a": 1},
            idempotency_key_or_response=idempotency_key_value,
        )
        assert response is None
        assert idempotency_key == idempotency_key_value
        assert isinstance(fingerprint, str) and fingerprint

        first = await idempotency_module._idempotency_commit(
            scope="admin_test",
            tenant_id="public",
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            status_code=201,
            body={"ok": True},
        )
        assert first.status_code == 201
        assert first.headers.get("Idempotency-Replayed") == "false"

        _, _, replay = await idempotency_module._idempotency_preflight(
            scope="admin_test",
            tenant_id="public",
            payload={"a": 1},
            idempotency_key_or_response=idempotency_key_value,
        )
        assert replay is not None
        assert replay.status_code == 201
        assert replay.headers.get("Idempotency-Replayed") == "true"


class _FakeAsyncAuthTokens:
    def __init__(self):
        self.last_config = None

    async def create(self, *, config=None):
        self.last_config = config
        return SimpleNamespace(name="auth_tokens/test-token")


class _FakeTokenClient:
    def __init__(self):
        self.aio = SimpleNamespace(auth_tokens=_FakeAsyncAuthTokens())


class TestTokenEndpoint:
    @pytest.mark.asyncio
    async def test_token_rejects_invalid_origin(self, client):
        response = await client.post(
            "/api/token",
            headers={"Origin": "http://evil.example.com"},
            json={"userId": "user_123", "tenantId": "public", "industry": "electronics"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "Origin not allowed"

    @pytest.mark.asyncio
    async def test_token_rejects_invalid_user_payload(self, client):
        response = await client.post(
            "/api/token",
            headers={"Origin": "http://localhost:5173"},
            json={"userId": "bad user id", "tenantId": "public", "industry": "electronics"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_token_rejects_unknown_tenant(self, client, main_module, monkeypatch):
        fake_client = _FakeTokenClient()
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})

        response = await client.post(
            "/api/token",
            headers={"Origin": "http://localhost:5173"},
            json={"userId": "user_123", "tenantId": "blocked", "industry": "electronics"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "Tenant not allowed"

    @pytest.mark.asyncio
    async def test_token_returns_constrained_single_use_token(self, client, main_module, monkeypatch):
        fake_client = _FakeTokenClient()
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(main_module, "TOKEN_MAX_USES", 1)

        response = await client.post(
            "/api/token",
            headers={"Origin": "http://localhost:5173"},
            json={"userId": "user_123", "tenantId": "public", "industry": "electronics"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token"].startswith("auth_tokens/")
        assert body["maxUses"] == 1
        assert body["tenantId"] == "public"
        assert body["userId"] == "user_123"
        assert body["fallbackModelUsed"] is False

        config = fake_client.aio.auth_tokens.last_config
        assert config is not None
        assert config.uses == 1
        assert config.live_connect_constraints.model in main_module.LIVE_MODEL_CANDIDATES
        assert config.live_connect_constraints.config.proactivity is not None
        assert config.live_connect_constraints.config.speech_config is not None

    @pytest.mark.asyncio
    async def test_token_falls_back_to_next_model_candidate(self, client, main_module, monkeypatch):
        class _FlakyAuthTokens:
            def __init__(self):
                self.calls = 0
                self.last_config = None

            async def create(self, *, config=None):
                self.calls += 1
                self.last_config = config
                if self.calls == 1:
                    raise RuntimeError("primary model unavailable")
                return SimpleNamespace(name="auth_tokens/test-token")

        fake_client = SimpleNamespace(aio=SimpleNamespace(auth_tokens=_FlakyAuthTokens()))
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(main_module, "LIVE_MODEL_CANDIDATES", ["model-primary", "model-fallback"])

        response = await client.post(
            "/api/token",
            headers={"Origin": "http://localhost:5173"},
            json={"userId": "user_123", "tenantId": "public", "industry": "electronics"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["model"] == "model-fallback"
        assert body["fallbackModelUsed"] is True

    @pytest.mark.asyncio
    async def test_token_rate_limit_returns_429(self, client, main_module, monkeypatch):
        fake_client = _FakeTokenClient()
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(main_module, "TOKEN_RATE_LIMIT", 1)

        headers = {"Origin": "http://localhost:5173"}
        payload = {"userId": "user_123", "tenantId": "public", "industry": "electronics"}
        first = await client.post("/api/token", headers=headers, json=payload)
        second = await client.post("/api/token", headers=headers, json=payload)

        assert first.status_code == 200
        assert second.status_code == 429

    @pytest.mark.asyncio
    async def test_token_logs_debug_when_origin_missing(
        self, client, main_module, monkeypatch, caplog
    ):
        fake_client = _FakeTokenClient()
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(main_module, "_registry_enabled", lambda: False)
        caplog.set_level(logging.DEBUG, logger=main_module.__name__)

        response = await client.post(
            "/api/token",
            json={"userId": "user_123", "tenantId": "public", "industry": "electronics"},
        )
        assert response.status_code == 200
        assert "HTTP request accepted without Origin header endpoint=api_token" in caplog.text


class TestOnboardingConfigEndpoint:
    @pytest.mark.asyncio
    async def test_onboarding_config_allows_missing_origin_and_logs_debug(
        self, client, main_module, monkeypatch, caplog
    ):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [],
                "companies": [],
                "defaults": {},
                "uiPolicies": {},
                "version": "test",
            }

        monkeypatch.setattr(main_module, "_registry_enabled", lambda: False)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        caplog.set_level(logging.DEBUG, logger=main_module.__name__)

        response = await client.get("/api/onboarding/config?tenantId=public")
        assert response.status_code == 200
        assert response.json()["tenantId"] == "public"
        assert "HTTP request accepted without Origin header endpoint=api_onboarding" in caplog.text


class TestRuntimeBootstrapEndpoint:
    @pytest.mark.asyncio
    async def test_bootstrap_allows_missing_origin_and_returns_runtime_context(
        self, client, main_module, monkeypatch
    ):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [
                    {
                        "id": "telecom",
                        "category": "telecom",
                        "defaultVoice": "Charon",
                        "capabilities": ["policy_qa", "connector_dispatch"],
                    }
                ],
                "companies": [
                    {
                        "id": "ekaette-telecom",
                        "templateId": "telecom",
                        "displayName": "Ekaette Telecom",
                    }
                ],
                "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
            }

        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(
            main_module,
            "_resolve_registry_runtime_config",
            AsyncMock(
                return_value=SimpleNamespace(
                    tenant_id="public",
                    company_id="ekaette-telecom",
                    industry_template_id="telecom",
                    template_category="telecom",
                    capabilities=["policy_qa", "connector_dispatch"],
                    registry_version="v1-telecom",
                    voice="Charon",
                )
            ),
        )
        monkeypatch.setattr(main_module, "_registry_enabled", lambda: True)

        response = await client.get("/api/v1/runtime/bootstrap?tenantId=public")
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["tenantId"] == "public"
        assert payload["companyId"] == "ekaette-telecom"
        assert payload["industryTemplateId"] == "telecom"
        assert payload["industry"] == "telecom"
        assert payload["voice"] == "Charon"
        assert payload["capabilities"] == ["policy_qa", "connector_dispatch"]
        assert payload["onboardingRequired"] is False
        assert payload["sessionPolicy"]["industryLocked"] is True

    @pytest.mark.asyncio
    async def test_bootstrap_rejects_invalid_origin(self, client):
        response = await client.get(
            "/api/v1/runtime/bootstrap?tenantId=public",
            headers={"Origin": "http://evil.example.com"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "Origin not allowed"

    @pytest.mark.asyncio
    async def test_bootstrap_returns_company_selection_required_when_no_default(
        self, client, main_module, monkeypatch
    ):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "electronics"}, {"id": "hotel"}],
                "companies": [
                    {
                        "id": "ekaette-electronics",
                        "templateId": "electronics",
                        "displayName": "Ekaette Devices Hub",
                    },
                    {
                        "id": "ekaette-hotel",
                        "templateId": "hotel",
                        "displayName": "Ekaette Suites",
                    },
                ],
                "defaults": {"templateId": "electronics", "companyId": ""},
            }

        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(main_module, "_registry_enabled", lambda: False)

        response = await client.get("/api/v1/runtime/bootstrap?tenantId=public")
        assert response.status_code == 409
        payload = response.json()
        assert payload["code"] == "NEED_COMPANY_SELECTION"
        assert payload["onboardingRequired"] is True
        assert len(payload["companies"]) == 2


class TestAdminV1Endpoints:
    @staticmethod
    def _admin_headers(
        *,
        tenant_id: str = "public",
        roles: str = "tenant_admin",
        scopes: str = "",
        admin_key: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "x-user-id": "admin-user",
            "x-tenant-id": tenant_id,
            "x-roles": roles,
        }
        if scopes:
            headers["x-scopes"] = scopes
        if admin_key:
            headers["x-admin-key"] = admin_key
        return headers

    @pytest.mark.asyncio
    async def test_admin_companies_requires_auth_headers(self, client):
        response = await client.get("/api/v1/admin/companies?tenantId=public")
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_admin_companies_requires_admin_role(self, client):
        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(roles="viewer"),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "ADMIN_SCOPE_REQUIRED"

    @pytest.mark.asyncio
    async def test_admin_write_endpoint_rejects_read_only_scope(self, client):
        response = await client.post(
            "/api/v1/admin/companies?tenantId=public",
            headers={
                **self._admin_headers(roles="", scopes="admin:read"),
                "Idempotency-Key": "scope-check-1",
            },
            json={
                "companyId": "ekaette-telecom",
                "displayName": "Ekaette Telecom",
                "industryTemplateId": "telecom",
                "status": "active",
            },
        )
        assert response.status_code == 403
        payload = response.json()
        assert payload["code"] == "ADMIN_SCOPE_REQUIRED"
        assert payload["requiredScope"] == "write"

    @pytest.mark.asyncio
    async def test_admin_shared_secret_enforced_when_enabled(self, client, admin_runtime, monkeypatch):
        monkeypatch.setattr(admin_runtime, "ADMIN_REQUIRE_SHARED_SECRET", True)
        monkeypatch.setattr(admin_runtime, "ADMIN_SHARED_SECRET", "expected-secret")
        monkeypatch.setattr(admin_runtime, "ADMIN_AUTH_MODE", "headers")

        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(),
        )
        assert response.status_code == 401
        assert response.json()["code"] == "ADMIN_SHARED_SECRET_INVALID"

        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(admin_key="expected-secret"),
        )
        assert response.status_code in {200, 503}

    @pytest.mark.asyncio
    async def test_admin_iap_mode_requires_assertion(self, client, admin_runtime, monkeypatch):
        monkeypatch.setattr(admin_runtime, "ADMIN_AUTH_MODE", "iap")
        monkeypatch.setattr(admin_runtime, "ADMIN_IAP_AUDIENCE", "test-audience")

        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(),
        )
        assert response.status_code == 401
        assert response.json()["code"] == "ADMIN_IAP_TOKEN_REQUIRED"

    @pytest.mark.asyncio
    async def test_admin_iap_mode_accepts_verified_assertion(self, client, admin_runtime, monkeypatch):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(_db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [{"id": "ekaette-telecom", "templateId": "telecom"}],
                "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
            }

        def _fake_verify_token(*args, **kwargs):
            return {
                "iss": "https://cloud.google.com/iap",
                "sub": "user-123",
                "email": "admin@example.com",
                "tenant_id": "public",
                "scope": "admin:read admin:write",
            }

        monkeypatch.setattr(admin_runtime, "ADMIN_AUTH_MODE", "iap")
        monkeypatch.setattr(admin_runtime, "ADMIN_IAP_AUDIENCE", "test-audience")
        monkeypatch.setattr(admin_runtime, "ADMIN_IAP_ALLOWLIST_EMAILS", {"admin@example.com"})
        monkeypatch.setattr(admin_runtime.google_id_token, "verify_token", _fake_verify_token)
        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )

        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers={"x-goog-iap-jwt-assertion": "fake-iap-jwt"},
        )
        assert response.status_code == 200
        assert response.json()["tenantId"] == "public"

    @pytest.mark.asyncio
    async def test_admin_iap_mode_rejects_bad_issuer(self, client, admin_runtime, monkeypatch):
        def _fake_verify_token(*args, **kwargs):
            return {
                "iss": "https://issuer.example.com",
                "sub": "user-123",
                "email": "admin@example.com",
                "tenant_id": "public",
            }

        monkeypatch.setattr(admin_runtime, "ADMIN_AUTH_MODE", "iap")
        monkeypatch.setattr(admin_runtime, "ADMIN_IAP_AUDIENCE", "test-audience")
        monkeypatch.setattr(admin_runtime, "ADMIN_IAP_ALLOWLIST_EMAILS", {"admin@example.com"})
        monkeypatch.setattr(admin_runtime.google_id_token, "verify_token", _fake_verify_token)

        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers={"x-goog-iap-jwt-assertion": "fake-iap-jwt"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "ADMIN_IAP_ISSUER_INVALID"

    @pytest.mark.asyncio
    async def test_admin_companies_rejects_tenant_mismatch(self, client):
        response = await client.get(
            "/api/v1/admin/companies?tenantId=other-tenant",
            headers=self._admin_headers(tenant_id="public"),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "TENANT_FORBIDDEN"

    @pytest.mark.asyncio
    async def test_admin_rate_limit_is_enforced(self, client, admin_runtime, monkeypatch):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [{"id": "ekaette-telecom", "templateId": "telecom"}],
                "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
            }

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(admin_runtime, "ADMIN_RATE_LIMIT", 1)
        if hasattr(admin_runtime, "_rate_limit_buckets"):
            admin_runtime._rate_limit_buckets.clear()

        first = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(),
        )
        assert first.status_code == 200

        second = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(),
        )
        assert second.status_code == 429
        assert second.json()["code"] == "ADMIN_RATE_LIMIT_EXCEEDED"

    @pytest.mark.asyncio
    async def test_admin_companies_returns_tenant_companies(
        self, client, admin_runtime, monkeypatch
    ):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [
                    {
                        "id": "ekaette-telecom",
                        "templateId": "telecom",
                        "displayName": "Ekaette Telecom",
                    }
                ],
                "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
            }

        monkeypatch.setattr(admin_runtime, "TOKEN_ALLOWED_TENANTS", {"public"})
        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )

        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(tenant_id="public"),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["tenantId"] == "public"
        assert payload["count"] == 1
        assert payload["companies"][0]["id"] == "ekaette-telecom"

    @pytest.mark.asyncio
    async def test_admin_mcp_providers_returns_allowlist(self, client, admin_runtime, monkeypatch):
        monkeypatch.setattr(admin_runtime, "MCP_PROVIDER_ALLOWLIST", {"mock", "salesforce"})

        response = await client.get(
            "/api/v1/admin/mcp/providers?tenantId=public",
            headers=self._admin_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["tenantId"] == "public"
        assert payload["count"] == 2
        provider_ids = [item["id"] for item in payload["providers"]]
        assert provider_ids == ["mock", "salesforce"]

    @pytest.mark.asyncio
    async def test_admin_mcp_providers_rejects_bad_origin(self, client):
        response = await client.get(
            "/api/v1/admin/mcp/providers?tenantId=public",
            headers={
                **self._admin_headers(),
                "Origin": "http://evil.example.com",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"] == "Origin not allowed"

    @pytest.mark.asyncio
    async def test_admin_company_upsert_requires_idempotency_key(self, client, monkeypatch):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [],
                "defaults": {"templateId": "telecom", "companyId": ""},
            }

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )

        response = await client.post(
            "/api/v1/admin/companies?tenantId=public",
            headers=self._admin_headers(),
            json={
                "companyId": "ekaette-telecom",
                "displayName": "Ekaette Telecom",
                "industryTemplateId": "telecom",
                "status": "active",
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    @pytest.mark.asyncio
    async def test_admin_company_upsert_happy_path_and_idempotent_replay(
        self, client, admin_runtime, monkeypatch
    ):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [],
                "defaults": {"templateId": "telecom", "companyId": ""},
            }

        upsert_calls = {"count": 0}

        async def _fake_upsert(
            db,
            *,
            tenant_id: str,
            company_id: str,
            display_name: str,
            industry_template_id: str,
            status: str,
            connectors: dict[str, object],
            overview: str,
            facts: dict[str, object],
            links: list[str],
        ):
            upsert_calls["count"] += 1
            return (
                True,
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": industry_template_id,
                    "display_name": display_name,
                    "status": status,
                    "connectors": connectors,
                },
            )

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(admin_runtime, "_upsert_registry_company_doc", _fake_upsert)

        payload = {
            "companyId": "ekaette-telecom",
            "displayName": "Ekaette Telecom",
            "industryTemplateId": "telecom",
            "status": "active",
            "connectors": {"crm": {"provider": "mock"}},
        }
        headers = {
            **self._admin_headers(),
            "Idempotency-Key": f"company-create-{uuid.uuid4().hex}",
        }
        first = await client.post(
            "/api/v1/admin/companies?tenantId=public",
            headers=headers,
            json=payload,
        )
        assert first.status_code == 201
        assert first.headers.get("Idempotency-Replayed") == "false"
        first_body = first.json()
        assert first_body["companyId"] == "ekaette-telecom"
        assert first_body["created"] is True

        second = await client.post(
            "/api/v1/admin/companies?tenantId=public",
            headers=headers,
            json=payload,
        )
        assert second.status_code == 201
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert second.json() == first_body
        assert upsert_calls["count"] == 1

    @pytest.mark.asyncio
    async def test_admin_company_upsert_rejects_key_reuse_with_different_payload(
        self, client, admin_runtime, monkeypatch
    ):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [],
                "defaults": {"templateId": "telecom", "companyId": ""},
            }

        async def _fake_upsert(
            db,
            *,
            tenant_id: str,
            company_id: str,
            display_name: str,
            industry_template_id: str,
            status: str,
            connectors: dict[str, object],
            overview: str,
            facts: dict[str, object],
            links: list[str],
        ):
            return (
                True,
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": industry_template_id,
                    "display_name": display_name,
                    "status": status,
                    "connectors": connectors,
                },
            )

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(admin_runtime, "_upsert_registry_company_doc", _fake_upsert)

        headers = {
            **self._admin_headers(),
            "Idempotency-Key": "company-create-2",
        }
        first = await client.post(
            "/api/v1/admin/companies?tenantId=public",
            headers=headers,
            json={
                "companyId": "ekaette-telecom",
                "displayName": "Ekaette Telecom",
                "industryTemplateId": "telecom",
                "status": "active",
            },
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/admin/companies?tenantId=public",
            headers=headers,
            json={
                "companyId": "ekaette-telecom",
                "displayName": "Ekaette Telecom Updated",
                "industryTemplateId": "telecom",
                "status": "active",
            },
        )
        assert second.status_code == 409
        assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD"

    @pytest.mark.asyncio
    async def test_admin_company_get_returns_company(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "company_id": company_id,
                    "tenant_id": tenant_id,
                    "industry_template_id": "telecom",
                    "display_name": "Ekaette Telecom",
                    "status": "active",
                    "connectors": {"crm": {"provider": "mock"}},
                    "facts": {"rooms": 32},
                    "links": ["https://example.com"],
                },
                None,
            )

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)

        response = await client.get(
            "/api/v1/admin/companies/ekaette-telecom?tenantId=public",
            headers=self._admin_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-telecom"
        assert payload["company"]["templateId"] == "telecom"
        assert payload["company"]["displayName"] == "Ekaette Telecom"

    @pytest.mark.asyncio
    async def test_admin_company_update_is_idempotent(
        self, client, admin_runtime, monkeypatch
    ):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [{"id": "ekaette-telecom", "templateId": "telecom"}],
                "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
            }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "telecom",
                    "display_name": "Old Name",
                    "status": "active",
                    "connectors": {},
                    "facts": {},
                    "links": [],
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
                None,
            )

        save_calls = {"count": 0}

        async def _fake_save_company(*, tenant_id: str, company_id: str, payload: dict):
            save_calls["count"] += 1

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", _fake_save_company)

        headers = {**self._admin_headers(), "Idempotency-Key": "company-update-1"}
        request_payload = {
            "displayName": "Updated Telecom",
            "industryTemplateId": "telecom",
            "status": "active",
            "connectors": {},
        }
        first = await client.put(
            "/api/v1/admin/companies/ekaette-telecom?tenantId=public",
            headers=headers,
            json=request_payload,
        )
        assert first.status_code == 200
        assert first.headers.get("Idempotency-Replayed") == "false"
        assert first.json()["company"]["displayName"] == "Updated Telecom"

        second = await client.put(
            "/api/v1/admin/companies/ekaette-telecom?tenantId=public",
            headers=headers,
            json=request_payload,
        )
        assert second.status_code == 200
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert save_calls["count"] == 1

    @pytest.mark.asyncio
    async def test_admin_knowledge_import_and_list(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "telecom",
                    "display_name": "Ekaette Telecom",
                    "status": "active",
                },
                None,
            )

        writes: list[dict] = []

        async def _fake_write_knowledge(*, tenant_id: str, company_id: str, knowledge_id: str, entry: dict):
            writes.append({"tenant_id": tenant_id, "company_id": company_id, "knowledge_id": knowledge_id, "entry": entry})

        async def _fake_load_knowledge(db, company_id: str, limit: int = 12, *, tenant_id=None):
            return [
                {
                    "id": "kb-1",
                    "company_id": company_id,
                    "title": "Store Policy",
                    "text": "No refunds",
                    "tags": ["policy"],
                    "source": "text",
                    "url": "",
                }
            ]

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_write_company_knowledge_entry", _fake_write_knowledge)
        monkeypatch.setattr(admin_runtime, "load_company_knowledge", _fake_load_knowledge)

        create = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/knowledge/import-text?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "kb-import-1"},
            json={
                "title": "Availability Rules",
                "text": "Rooms open 24/7.",
                "tags": ["availability"],
            },
        )
        assert create.status_code == 201
        assert create.json()["created"] is True
        assert len(writes) == 1

        listing = await client.get(
            "/api/v1/admin/companies/ekaette-telecom/knowledge?tenantId=public",
            headers=self._admin_headers(),
        )
        assert listing.status_code == 200
        assert listing.json()["count"] == 1
        assert listing.json()["entries"][0]["title"] == "Store Policy"

    @pytest.mark.asyncio
    async def test_admin_knowledge_import_url_validates_scheme(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "telecom",
                    "display_name": "Ekaette Telecom",
                    "status": "active",
                },
                None,
            )

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/knowledge/import-url?tenantId=public",
            headers=self._admin_headers(),
            json={
                "url": "ftp://example.com/doc",
                "title": "Bad URL",
                "tags": ["url"],
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_URL"

    @pytest.mark.asyncio
    async def test_admin_knowledge_list_rejects_invalid_limit(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "telecom",
                    "display_name": "Ekaette Telecom",
                    "status": "active",
                },
                None,
            )

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)

        response = await client.get(
            "/api/v1/admin/companies/ekaette-telecom/knowledge?tenantId=public&limit=not-a-number",
            headers=self._admin_headers(),
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_LIMIT"

    @pytest.mark.asyncio
    async def test_admin_connector_create_and_test_mock(self, client, admin_runtime, monkeypatch):
        company_state = {
            "schema_version": 1,
            "tenant_id": "public",
            "company_id": "ekaette-telecom",
            "industry_template_id": "telecom",
            "display_name": "Ekaette Telecom",
            "status": "active",
            "connectors": {},
        }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return dict(company_state), None

        async def _fake_save_company(*, tenant_id: str, company_id: str, payload: dict):
            company_state.update(payload)

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", _fake_save_company)
        monkeypatch.setattr(admin_runtime, "_registry_db_client", lambda: None)
        monkeypatch.setattr(admin_runtime, "MCP_PROVIDER_ALLOWLIST", {"mock", "salesforce"})

        create = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "connector-create-1"},
            json={
                "connectorId": "crm",
                "provider": "mock",
                "enabled": True,
                "capabilities": ["read"],
                "config": {"mock_actions": {"status": "ok"}},
            },
        )
        assert create.status_code == 201
        assert create.json()["connector"]["provider"] == "mock"

        test_response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm/test?tenantId=public",
            headers=self._admin_headers(),
        )
        assert test_response.status_code == 200
        assert test_response.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_admin_connector_test_enforces_egress_host_allowlist(
        self, client, admin_runtime, monkeypatch
    ):
        company_state = {
            "schema_version": 1,
            "tenant_id": "public",
            "company_id": "ekaette-telecom",
            "industry_template_id": "telecom",
            "display_name": "Ekaette Telecom",
            "status": "active",
            "connectors": {
                "crm": {
                    "id": "crm",
                    "provider": "salesforce",
                    "enabled": True,
                    "capabilities": ["read"],
                    "secret_ref": "projects/p/secrets/salesforce",
                    "config": {"endpoint": "https://evil.example.com/api"},
                }
            },
        }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return dict(company_state), None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "MCP_PROVIDER_ALLOWLIST", {"mock", "salesforce"})
        monkeypatch.setattr(
            admin_runtime,
            "_effective_mcp_provider_catalog",
            lambda: {
                "salesforce": {
                    "id": "salesforce",
                    "label": "Salesforce",
                    "status": "preview",
                    "requiresSecretRef": True,
                    "capabilities": ["read", "write"],
                    "testPolicy": {
                        "timeoutSeconds": 2.0,
                        "maxRetries": 0,
                        "circuitOpenAfterFailures": 2,
                        "circuitOpenSeconds": 30,
                        "allowedHosts": ["api.salesforce.com"],
                    },
                }
            },
        )

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm/test?tenantId=public",
            headers=self._admin_headers(),
        )
        assert response.status_code == 400
        assert response.json()["code"] == "CONNECTOR_EGRESS_HOST_NOT_ALLOWED"

    @pytest.mark.asyncio
    async def test_admin_connector_test_opens_circuit_after_repeated_failures(
        self, client, admin_runtime, monkeypatch
    ):
        company_state = {
            "schema_version": 1,
            "tenant_id": "public",
            "company_id": "ekaette-telecom",
            "industry_template_id": "telecom",
            "display_name": "Ekaette Telecom",
            "status": "active",
            "connectors": {
                "crm": {
                    "id": "crm",
                    "provider": "salesforce",
                    "enabled": True,
                    "capabilities": ["read"],
                    "secret_ref": "projects/p/secrets/salesforce",
                    "config": {"endpoint": "https://api.salesforce.com/services/data"},
                }
            },
        }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return dict(company_state), None

        async def _slow_probe(**kwargs):
            await asyncio.sleep(0.02)
            return {"ok": True}

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_execute_connector_test_probe", _slow_probe)
        monkeypatch.setattr(admin_runtime, "MCP_PROVIDER_ALLOWLIST", {"mock", "salesforce"})
        monkeypatch.setattr(
            admin_runtime,
            "_effective_mcp_provider_catalog",
            lambda: {
                "salesforce": {
                    "id": "salesforce",
                    "label": "Salesforce",
                    "status": "preview",
                    "requiresSecretRef": True,
                    "capabilities": ["read", "write"],
                    "testPolicy": {
                        "timeoutSeconds": 0.001,
                        "maxRetries": 0,
                        "circuitOpenAfterFailures": 1,
                        "circuitOpenSeconds": 30,
                        "allowedHosts": ["api.salesforce.com"],
                    },
                }
            },
        )

        first = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm/test?tenantId=public",
            headers=self._admin_headers(),
        )
        assert first.status_code == 503
        assert first.json()["code"] == "CONNECTOR_CIRCUIT_OPEN"

        second = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm/test?tenantId=public",
            headers=self._admin_headers(),
        )
        assert second.status_code == 503
        assert second.json()["code"] == "CONNECTOR_CIRCUIT_OPEN"
        assert second.json()["retryAfterSeconds"] >= 1

    @pytest.mark.asyncio
    async def test_admin_connector_delete(self, client, admin_runtime, monkeypatch):
        company_state = {
            "schema_version": 1,
            "tenant_id": "public",
            "company_id": "ekaette-telecom",
            "industry_template_id": "telecom",
            "display_name": "Ekaette Telecom",
            "status": "active",
            "connectors": {"crm": {"provider": "mock"}},
        }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return dict(company_state), None

        async def _fake_save_company(*, tenant_id: str, company_id: str, payload: dict):
            company_state.update(payload)

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", _fake_save_company)
        monkeypatch.setattr(admin_runtime, "_registry_db_client", lambda: None)

        response = await client.delete(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "connector-delete-1"},
        )
        assert response.status_code == 200
        assert response.json()["deleted"] is True

    @pytest.mark.asyncio
    async def test_admin_knowledge_delete_not_found(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "telecom",
                    "display_name": "Ekaette Telecom",
                    "status": "active",
                },
                None,
            )

        async def _fake_delete_knowledge(*, tenant_id: str, company_id: str, knowledge_id: str):
            return False

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_delete_company_knowledge_entry", _fake_delete_knowledge)

        response = await client.delete(
            "/api/v1/admin/companies/ekaette-telecom/knowledge/kb-missing?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "knowledge-delete-1"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "KNOWLEDGE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_admin_connector_update_requires_secret_for_non_mock(
        self, client, admin_runtime, monkeypatch
    ):
        company_state = {
            "schema_version": 1,
            "tenant_id": "public",
            "company_id": "ekaette-telecom",
            "industry_template_id": "telecom",
            "display_name": "Ekaette Telecom",
            "status": "active",
            "connectors": {"crm": {"provider": "mock"}},
        }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return dict(company_state), None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "MCP_PROVIDER_ALLOWLIST", {"mock", "salesforce"})

        response = await client.put(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "connector-update-1"},
            json={
                "provider": "salesforce",
                "enabled": True,
                "capabilities": ["read"],
                "config": {},
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "CONNECTOR_SECRET_REF_REQUIRED"

    @pytest.mark.asyncio
    async def test_admin_connector_create_enforces_template_provider_policy(
        self, client, admin_runtime, monkeypatch
    ):
        company_state = {
            "schema_version": 1,
            "tenant_id": "public",
            "company_id": "ekaette-telecom",
            "industry_template_id": "telecom",
            "display_name": "Ekaette Telecom",
            "status": "active",
            "connectors": {},
        }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return dict(company_state), None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "MCP_PROVIDER_ALLOWLIST", {"mock", "salesforce"})
        monkeypatch.setattr(
            admin_runtime,
            "_template_policy_config",
            lambda template_id: {"allowed_provider_ids": ["mock"], "max_capabilities": ["read", "write"]},
        )

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "connector-policy-1"},
            json={
                "connectorId": "crm",
                "provider": "salesforce",
                "enabled": True,
                "capabilities": ["read"],
                "secretRef": "projects/p/secrets/salesforce",
                "config": {},
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "CONNECTOR_PROVIDER_NOT_ALLOWED_FOR_TEMPLATE"

    @pytest.mark.asyncio
    async def test_admin_connector_create_enforces_provider_capabilities(
        self, client, admin_runtime, monkeypatch
    ):
        company_state = {
            "schema_version": 1,
            "tenant_id": "public",
            "company_id": "ekaette-telecom",
            "industry_template_id": "telecom",
            "display_name": "Ekaette Telecom",
            "status": "active",
            "connectors": {},
        }

        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return dict(company_state), None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "MCP_PROVIDER_ALLOWLIST", {"mock"})
        monkeypatch.setattr(
            admin_runtime,
            "_effective_mcp_provider_catalog",
            lambda: {
                "mock": {
                    "id": "mock",
                    "label": "Mock Provider",
                    "status": "active",
                    "requiresSecretRef": False,
                    "capabilities": ["read"],
                }
            },
        )
        monkeypatch.setattr(admin_runtime, "_template_policy_config", lambda template_id: {})

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "connector-policy-2"},
            json={
                "connectorId": "crm",
                "provider": "mock",
                "enabled": True,
                "capabilities": ["write"],
                "config": {},
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "CONNECTOR_CAPABILITY_NOT_ALLOWED"

    @pytest.mark.asyncio
    async def test_admin_products_import_is_idempotent(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "electronics",
                    "display_name": "Ekaette Devices",
                    "status": "active",
                },
                None,
            )

        calls = {"count": 0}

        async def _fake_import_products(*, tenant_id: str, company_id: str, products: list[dict], data_tier: str):
            calls["count"] += 1
            return {
                "written": len(products),
                "operations": {"created": len(products), "updated": 0, "unchanged": 0, "failed": 0},
                "errors": [],
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_import_company_products", _fake_import_products)

        payload = {
            "products": [
                {
                    "id": "iphone-13",
                    "name": "iPhone 13",
                    "category": "phones",
                    "price": 500,
                    "currency": "USD",
                    "in_stock": True,
                }
            ],
            "data_tier": "admin",
        }
        headers = {**self._admin_headers(), "Idempotency-Key": "products-import-1"}

        first = await client.post(
            "/api/v1/admin/companies/ekaette-electronics/products/import?tenantId=public",
            headers=headers,
            json=payload,
        )
        assert first.status_code == 200
        assert first.headers.get("Idempotency-Replayed") == "false"
        assert first.json()["written"] == 1

        second = await client.post(
            "/api/v1/admin/companies/ekaette-electronics/products/import?tenantId=public",
            headers=headers,
            json=payload,
        )
        assert second.status_code == 200
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert calls["count"] == 1

    @pytest.mark.asyncio
    async def test_admin_booking_slots_import_success(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "hotel",
                    "display_name": "Ekaette Hotel",
                    "status": "active",
                },
                None,
            )

        async def _fake_import_slots(*, tenant_id: str, company_id: str, slots: list[dict], data_tier: str):
            return {
                "written": len(slots),
                "operations": {"created": len(slots), "updated": 0, "unchanged": 0, "failed": 0},
                "errors": [],
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_import_company_booking_slots", _fake_import_slots)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/booking-slots/import?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "slots-import-1"},
            json={
                "slots": [
                    {
                        "id": "slot-1",
                        "date": "2026-03-01",
                        "time": "10:00",
                        "available": True,
                    }
                ],
                "data_tier": "admin",
            },
        )
        assert response.status_code == 200
        assert response.json()["written"] == 1
        assert response.json()["collection"] == "booking_slots"

    @pytest.mark.asyncio
    async def test_admin_runtime_purge_demo_success(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "hotel",
                    "display_name": "Ekaette Hotel",
                    "status": "active",
                },
                None,
            )

        async def _fake_purge(*, tenant_id: str, company_id: str):
            return {"products": 2, "booking_slots": 1, "knowledge": 0}

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_purge_company_demo_runtime_data", _fake_purge)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/runtime/purge-demo?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "purge-demo-1"},
        )
        assert response.status_code == 200
        assert response.json()["deleted"]["products"] == 2
        assert response.json()["deleted"]["booking_slots"] == 1

    @pytest.mark.asyncio
    async def test_admin_company_export_success(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "hotel",
                    "display_name": "Ekaette Hotel",
                    "status": "active",
                },
                None,
            )

        async def _fake_export_bundle(*, tenant_id: str, company_id: str, company_doc: dict, include_runtime_data: bool):
            return {
                "company": {"id": company_id, "schemaVersion": 1},
                "collections": {"knowledge": [], "products": [], "booking_slots": []},
                "counts": {"knowledge": 0, "products": 0, "booking_slots": 0},
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_export_company_bundle", _fake_export_bundle)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/export?tenantId=public",
            headers=self._admin_headers(),
            json={"includeRuntimeData": True},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-hotel"
        assert "exportedAt" in payload

    @pytest.mark.asyncio
    async def test_admin_company_delete_is_idempotent(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "hotel",
                    "display_name": "Ekaette Hotel",
                    "status": "active",
                },
                None,
            )

        calls = {"count": 0}

        async def _fake_delete_bundle(*, tenant_id: str, company_id: str):
            calls["count"] += 1
            return {"knowledge": 1, "products": 2, "booking_slots": 3, "company": 1}

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_delete_company_bundle", _fake_delete_bundle)

        headers = {**self._admin_headers(), "Idempotency-Key": "company-delete-1"}
        first = await client.delete(
            "/api/v1/admin/companies/ekaette-hotel?tenantId=public",
            headers=headers,
        )
        assert first.status_code == 200
        assert first.headers.get("Idempotency-Replayed") == "false"

        second = await client.delete(
            "/api/v1/admin/companies/ekaette-hotel?tenantId=public",
            headers=headers,
        )
        assert second.status_code == 200
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert calls["count"] == 1

    @pytest.mark.asyncio
    async def test_admin_company_retention_purge_success(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "hotel",
                    "display_name": "Ekaette Hotel",
                    "status": "active",
                },
                None,
            )

        async def _fake_retention_purge(*, tenant_id: str, company_id: str, older_than_days: int, collections: list[str], data_tier: str | None):
            return {
                "knowledge": {"scanned": 10, "deleted": 4, "skipped": 6, "missing_timestamp": 0}
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_purge_company_retention_data", _fake_retention_purge)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/retention/purge?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "retention-purge-1"},
            json={"olderThanDays": 30, "collections": ["knowledge"], "dataTier": "demo"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-hotel"
        assert payload["report"]["knowledge"]["deleted"] == 4

    @pytest.mark.asyncio
    async def test_admin_company_retention_purge_rejects_invalid_collection(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(*, tenant_id: str, company_id: str):
            return (
                {
                    "schema_version": 1,
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "industry_template_id": "hotel",
                    "display_name": "Ekaette Hotel",
                    "status": "active",
                },
                None,
            )

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/retention/purge?tenantId=public",
            headers={**self._admin_headers(), "Idempotency-Key": "retention-purge-2"},
            json={"olderThanDays": 30, "collections": ["invalid-collection"]},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "RETENTION_COLLECTION_INVALID"


class TestUploadValidationEndpoint:
    @pytest.mark.asyncio
    async def test_upload_validation_allows_valid_image(self, client):
        response = await client.post(
            "/api/upload/validate",
            headers={"Origin": "http://localhost:5173"},
            files={"file": ("phone.jpg", b"\xFF\xD8\xFF" + b"a" * 512, "image/jpeg")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mimeType"] == "image/jpeg"
        assert data["sizeBytes"] > 0

    @pytest.mark.asyncio
    async def test_upload_validation_allows_heic_image(self, client):
        response = await client.post(
            "/api/upload/validate",
            headers={"Origin": "http://localhost:5173"},
            files={"file": ("phone.heic", b"heic-bytes", "image/heic")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mimeType"] == "image/heic"
        assert data["sizeBytes"] > 0

    @pytest.mark.asyncio
    async def test_upload_validation_rejects_invalid_origin(self, client):
        response = await client.post(
            "/api/upload/validate",
            headers={"Origin": "http://evil.example.com"},
            files={"file": ("x.jpg", b"abc", "image/jpeg")},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_upload_validation_rejects_mime_type(self, client):
        response = await client.post(
            "/api/upload/validate",
            headers={"Origin": "http://localhost:5173"},
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 415
        assert response.json()["error"] == "MIME type not allowed"

    @pytest.mark.asyncio
    async def test_upload_validation_rejects_oversized_file(self, client, main_module, monkeypatch):
        monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 4)
        response = await client.post(
            "/api/upload/validate",
            headers={"Origin": "http://localhost:5173"},
            files={"file": ("big.jpg", b"12345", "image/jpeg")},
        )
        assert response.status_code == 413
        assert response.json()["error"] == "Upload exceeds max size"

    @pytest.mark.asyncio
    async def test_upload_validation_rate_limit_returns_429(self, client, main_module, monkeypatch):
        monkeypatch.setattr(main_module, "UPLOAD_RATE_LIMIT", 1)

        headers = {"Origin": "http://localhost:5173"}
        files = {"file": ("phone.jpg", b"\xFF\xD8\xFFabc", "image/jpeg")}
        first = await client.post("/api/upload/validate", headers=headers, files=files)
        second = await client.post("/api/upload/validate", headers=headers, files=files)

        assert first.status_code == 200
        assert second.status_code == 429


# ── Transcription helpers ──


def _empty_event(**overrides):
    """Build a minimal ADK-like event with sensible defaults."""
    defaults = dict(
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
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _transcription(text, finished=False):
    """Mock google.genai.types.Transcription with .text and .finished."""
    return SimpleNamespace(text=text, finished=finished)


class _NewSessionService:
    """Fake session service that always creates a new session."""
    async def get_session(self, *, app_name, user_id, session_id):
        return None

    async def create_session(self, *, app_name, user_id, session_id, state=None):
        return SimpleNamespace(id=session_id, state=state or {})


async def _stub_industry_config_loader(_db, industry: str):
    return {
        "name": industry.title(),
        "voice": "Aoede",
        "greeting": f"Welcome to {industry}.",
    }


async def _stub_company_profile_loader(_db, company_id: str, *, tenant_id=None):
    return {
        "name": company_id,
        "overview": "Test company",
        "system_connectors": {"catalog": {"provider": "mock"}},
    }


async def _stub_company_knowledge_loader(_db, company_id: str, *, tenant_id=None):
    return []


def _run_ws_with_events(events, app, main_module, monkeypatch):
    """Connect a test WS, yield events via fake run_live, return JSON messages.

    Appends a turn_complete sentinel so the reader can stop after receiving
    the corresponding agent_status/idle message.

    Safety: some code paths may end the Live loop and close the websocket
    without emitting the idle sentinel (for example, session_ending / error).
    The helper treats those terminal messages, or a websocket disconnect, as
    valid end conditions to avoid hanging the suite.
    """
    # Append sentinel turn_complete so downstream sends agent_status idle
    sentinel = _empty_event(turn_complete=True, author="ekaette_router")
    full_events = list(events) + [sentinel]

    async def _fake_run_live(*args, **kwargs):
        for ev in full_events:
            yield ev
        await asyncio.sleep(0)

    monkeypatch.setattr(main_module, "session_service", _NewSessionService())
    monkeypatch.setattr(type(main_module.runner), "run_live", _fake_run_live)
    monkeypatch.setattr(main_module, "_registry_enabled", lambda: False)
    monkeypatch.setattr(main_module, "load_industry_config", _stub_industry_config_loader)
    monkeypatch.setattr(main_module, "load_company_profile", _stub_company_profile_loader)
    monkeypatch.setattr(main_module, "load_company_knowledge", _stub_company_knowledge_loader)

    messages: list[dict] = []
    with TestClient(app) as tc:
        with tc.websocket_connect(
            "/ws/user_1/sess_1?industry=electronics&companyId=ekaette-electronics",
            headers={"origin": "http://localhost:5173"},
        ) as ws:
            # Read until we see the sentinel's agent_status idle
            while True:
                try:
                    raw = ws.receive_text()
                except Exception:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "session_started":
                    continue
                messages.append(msg)
                # Stop after the sentinel turn_complete's idle status
                if (
                    msg.get("type") == "agent_status"
                    and msg.get("status") == "idle"
                ):
                    break
                if msg.get("type") in {"session_ending", "error"}:
                    break
            ws.close(code=1000)
    return messages


class TestTranscriptionFinishedFlag:
    """Test that the `finished` flag from ADK events controls `partial` on the wire."""

    def test_input_finished_true_sends_partial_false(self, app, main_module, monkeypatch):
        events = [_empty_event(input_transcription=_transcription("Hello world", finished=True))]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        assert len(ts) == 1
        assert ts[0]["role"] == "user"
        assert ts[0]["text"] == "Hello world"
        assert ts[0]["partial"] is False

    def test_input_finished_false_sends_partial_true(self, app, main_module, monkeypatch):
        events = [_empty_event(input_transcription=_transcription("Hel", finished=False))]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        # First message is the partial; sentinel turn_complete may add a finalize
        assert ts[0]["partial"] is True
        assert ts[0]["text"] == "Hel"

    def test_output_finished_true_sends_partial_false(self, app, main_module, monkeypatch):
        events = [_empty_event(output_transcription=_transcription("Welcome!", finished=True))]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        assert len(ts) == 1
        assert ts[0]["role"] == "agent"
        assert ts[0]["text"] == "Welcome!"
        assert ts[0]["partial"] is False

    def test_partial_then_finished_sequence(self, app, main_module, monkeypatch):
        events = [
            _empty_event(input_transcription=_transcription("Hel", finished=False)),
            _empty_event(input_transcription=_transcription("Hello world", finished=True)),
        ]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        assert len(ts) == 2
        assert ts[0]["partial"] is True
        assert ts[0]["text"] == "Hel"
        assert ts[1]["partial"] is False
        assert ts[1]["text"] == "Hello world"


class TestLatePartialSuppression:
    """Late-arriving partials after finalization are dropped."""

    def test_late_input_partial_after_finished_is_suppressed(self, app, main_module, monkeypatch):
        events = [
            _empty_event(input_transcription=_transcription("Hello", finished=True)),
            _empty_event(input_transcription=_transcription("Hel", finished=False)),  # stale
        ]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        assert len(ts) == 1
        assert ts[0]["text"] == "Hello"
        assert ts[0]["partial"] is False

    def test_late_output_partial_after_finished_is_suppressed(self, app, main_module, monkeypatch):
        events = [
            _empty_event(output_transcription=_transcription("Welcome!", finished=True)),
            _empty_event(output_transcription=_transcription("Welc", finished=False)),  # stale
        ]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        assert len(ts) == 1
        assert ts[0]["text"] == "Welcome!"
        assert ts[0]["partial"] is False

    def test_new_finished_after_suppression_resets_flag(self, app, main_module, monkeypatch):
        events = [
            _empty_event(input_transcription=_transcription("Hello", finished=True)),
            _empty_event(input_transcription=_transcription("Hel", finished=False)),  # suppressed
            _empty_event(input_transcription=_transcription("How are you", finished=True)),  # new
        ]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        assert len(ts) == 2
        assert ts[0]["text"] == "Hello"
        assert ts[1]["text"] == "How are you"
        assert all(t["partial"] is False for t in ts)


class TestTurnCompleteResetsFlags:
    """turn_complete resets suppression so the next turn works normally."""

    def test_turn_complete_allows_new_partials(self, app, main_module, monkeypatch):
        """After turn_complete resets flags, a new partial should not be suppressed.

        The event sequence includes its own turn_complete, then a new partial.
        The sentinel turn_complete added by _run_ws_with_events will finalize
        that partial, so we expect it to appear (possibly finalized).
        """
        events = [
            _empty_event(input_transcription=_transcription("Hello", finished=True)),
            _empty_event(output_transcription=_transcription("Hi!", finished=True)),
            _empty_event(turn_complete=True, author="ekaette_router"),
            # New turn — partial should go through (not suppressed)
            _empty_event(input_transcription=_transcription("What", finished=False)),
        ]

        # Use custom reader that collects past the first idle sentinel
        async def _fake_run_live(*args, **kwargs):
            sentinel = _empty_event(turn_complete=True, author="ekaette_router")
            for ev in list(events) + [sentinel]:
                yield ev
            await asyncio.sleep(0)

        monkeypatch.setattr(main_module, "session_service", _NewSessionService())
        monkeypatch.setattr(type(main_module.runner), "run_live", _fake_run_live)
        monkeypatch.setattr(main_module, "_registry_enabled", lambda: False)
        monkeypatch.setattr(main_module, "load_industry_config", _stub_industry_config_loader)
        monkeypatch.setattr(main_module, "load_company_profile", _stub_company_profile_loader)
        monkeypatch.setattr(main_module, "load_company_knowledge", _stub_company_knowledge_loader)

        messages: list[dict] = []
        idle_count = 0
        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_1/sess_1?industry=electronics&companyId=ekaette-electronics",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                while True:
                    try:
                        raw = ws.receive_text()
                    except Exception:
                        break
                    msg = json.loads(raw)
                    if msg.get("type") == "session_started":
                        continue
                    messages.append(msg)
                    if msg.get("type") == "agent_status" and msg.get("status") == "idle":
                        idle_count += 1
                        if idle_count >= 2:
                            break
                    if msg.get("type") in {"session_ending", "error"}:
                        break
                ws.close(code=1000)

        ts = [m for m in messages if m.get("type") == "transcription"]
        user_msgs = [t for t in ts if t["role"] == "user"]
        # "Hello" (finished) + "What" (partial, then finalized by sentinel)
        assert len(user_msgs) >= 2
        assert user_msgs[0]["text"] == "Hello"
        assert user_msgs[0]["partial"] is False
        # "What" was not suppressed — it made it through
        what_msgs = [t for t in user_msgs if t["text"] == "What"]
        assert len(what_msgs) >= 1


class TestOutputFinalizesInput:
    """Output transcription force-finalizes any active user input."""

    def test_output_transcription_finalizes_active_input(self, app, main_module, monkeypatch):
        events = [
            _empty_event(input_transcription=_transcription("I want to", finished=False)),
            _empty_event(input_transcription=_transcription("I want to book", finished=False)),
            _empty_event(output_transcription=_transcription("Sure,", finished=False)),
        ]
        messages = _run_ws_with_events(events, app, main_module, monkeypatch)

        ts = [m for m in messages if m.get("type") == "transcription"]
        user_msgs = [t for t in ts if t["role"] == "user"]
        agent_msgs = [t for t in ts if t["role"] == "agent"]

        # Last user message should be force-finalized (partial=False)
        assert user_msgs[-1]["partial"] is False
        assert user_msgs[-1]["text"] == "I want to book"
        # Agent partial came through
        assert len(agent_msgs) >= 1
        assert agent_msgs[0]["text"] == "Sure,"
