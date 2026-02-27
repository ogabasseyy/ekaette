"""Tests for the FastAPI application (main.py)."""

import asyncio
import json
import logging
from types import SimpleNamespace

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


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client for testing FastAPI endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_rate_limit_state(main_module):
    """Keep in-memory rate-limit state isolated between tests."""
    main_module._rate_limit_buckets.clear()
    yield
    main_module._rate_limit_buckets.clear()


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
