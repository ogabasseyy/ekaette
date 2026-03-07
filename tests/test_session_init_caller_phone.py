"""Focused tests for realtime websocket session initialization."""

from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.public import ws_auth
from app.api.v1.realtime import session_init


class _FakeWebSocket:
    def __init__(self, *, query_params: dict[str, str] | None = None, headers: dict[str, str] | None = None):
        self.query_params = query_params or {}
        self.headers = headers or {"origin": "http://localhost:5173"}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.accepted = False
        self.closed: list[tuple[int, str | None]] = []
        self.sent_texts: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int, reason: str | None = None) -> None:
        self.closed.append((code, reason))

    async def send_text(self, text: str) -> None:
        self.sent_texts.append(text)


class _FakeRunConfig:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeStreamingMode:
    BIDI = "BIDI"


class _FakeSessionResumptionConfig:
    def __init__(self, handle: str | None = None):
        self.handle = handle


class _FakeSessionService:
    def __init__(self, *, session=None, created_session_id: str = "created-session"):
        self.get_session = AsyncMock(return_value=session)
        self.create_session = AsyncMock(return_value=SimpleNamespace(id=created_session_id))


@pytest.fixture
def session_init_runtime(monkeypatch: pytest.MonkeyPatch):
    def _install(*, existing_session=None, ws_secret: str = "", token_claims=None):
        session_service = _FakeSessionService(session=existing_session)
        async_save_session_state = AsyncMock()

        runtime_values = (
            re.compile(r"^[A-Za-z0-9._:-]{1,128}$"),
            lambda origin: origin == "http://localhost:5173",
            lambda value: value,
            SimpleNamespace(model="gemini-2.5-flash-native-audio-preview"),
            lambda value: value,
            lambda value, default="public": (value or default).strip().lower(),
            lambda tenant_id: tenant_id != "blocked",
            lambda **kwargs: str(kwargs),
            lambda: False,
            lambda value: (value or "default-company").strip().lower(),
            "default-company",
            session_service,
            "test-app",
            AsyncMock(return_value={"name": "Electronics & Gadgets", "voice": "Aoede"}),
            object(),
            lambda industry_config, industry: {
                "app:industry": industry,
                "app:industry_config": industry_config,
                "app:voice": industry_config.get("voice", "Aoede"),
            },
            AsyncMock(return_value={"name": "Acme"}),
            object(),
            AsyncMock(return_value=[{"id": "kb-1", "text": "Knowledge"}]),
            lambda *, company_id, profile, knowledge: {
                "app:company_id": company_id,
                "app:company_profile": profile,
                "app:company_knowledge": knowledge,
            },
            AsyncMock(return_value=None),
            RuntimeError,
            lambda registry_config: {},
            async_save_session_state,
            lambda **kwargs: None,
            lambda industry: "Aoede",
            False,
            None,
            lambda industry, voice_override=None: {"speech_config": {"voice": voice_override or "Aoede"}},
            _FakeRunConfig,
            _FakeStreamingMode,
            SimpleNamespace(SessionResumptionConfig=_FakeSessionResumptionConfig),
            lambda *, session_id, industry, company_id, voice, manual_vad_active, session_state: {
                "type": "session_started",
                "sessionId": session_id,
                "industry": industry,
                "companyId": company_id,
                "voice": voice,
                "manualVadActive": manual_vad_active,
                "sessionState": session_state,
            },
        )

        monkeypatch.setattr(session_init, "bind_runtime_values", lambda *names: runtime_values)
        monkeypatch.setattr(
            session_init,
            "get_runtime_value_safe",
            lambda name, default=None: ws_secret if name == "WS_TOKEN_SECRET" else default,
        )
        monkeypatch.setattr(
            ws_auth,
            "validate_ws_token",
            lambda token, expected_user_id: token_claims,
        )

        return SimpleNamespace(
            session_service=session_service,
            async_save_session_state=async_save_session_state,
        )

    return _install


class TestInitializeSession:
    @pytest.mark.asyncio
    async def test_new_session_injects_caller_phone_into_real_initialize_session(
        self, session_init_runtime
    ):
        runtime = session_init_runtime()
        websocket = _FakeWebSocket(
            query_params={
                "industry": "electronics",
                "companyId": "Acme-Co",
                "caller_phone": "+2348012345678",
            }
        )

        ctx = await session_init.initialize_session(websocket, "sip-user-123", "session-abc")

        assert ctx is not None
        assert websocket.accepted is True
        assert ctx.session_state["user:caller_phone"] == "+2348012345678"
        assert ctx.company_id == "acme-co"
        runtime.session_service.create_session.assert_awaited_once()
        create_kwargs = runtime.session_service.create_session.await_args.kwargs
        assert create_kwargs["state"]["user:caller_phone"] == "+2348012345678"
        payload = json.loads(websocket.sent_texts[-1])
        assert payload["type"] == "session_started"
        assert payload["sessionState"]["user:caller_phone"] == "+2348012345678"

    @pytest.mark.asyncio
    async def test_resumed_session_adds_missing_caller_phone_via_state_save(
        self, session_init_runtime
    ):
        existing_session = SimpleNamespace(
            id="session-abc",
            state={
                "app:industry": "electronics",
                "app:industry_config": {"name": "Electronics & Gadgets", "voice": "Aoede"},
                "app:voice": "Aoede",
                "app:company_id": "acme-co",
                "app:company_profile": {"name": "Acme"},
                "app:company_knowledge": [],
            },
        )
        runtime = session_init_runtime(existing_session=existing_session)
        websocket = _FakeWebSocket(
            query_params={
                "industry": "electronics",
                "companyId": "Acme-Co",
                "callerPhone": "+2348012345678",
            }
        )

        ctx = await session_init.initialize_session(websocket, "sip-user-123", "session-abc")

        assert ctx is not None
        runtime.session_service.create_session.assert_not_awaited()
        runtime.async_save_session_state.assert_awaited_once()
        save_kwargs = runtime.async_save_session_state.await_args.kwargs
        assert save_kwargs["state_updates"] == {"user:caller_phone": "+2348012345678"}

    @pytest.mark.asyncio
    async def test_resumption_token_flows_into_run_config(
        self, session_init_runtime
    ):
        session_init_runtime()
        websocket = _FakeWebSocket(
            query_params={
                "industry": "electronics",
                "companyId": "Acme-Co",
                "resumption_token": "resume-123",
            }
        )

        ctx = await session_init.initialize_session(websocket, "sip-user-123", "session-abc")

        assert ctx is not None
        assert ctx.run_config.session_resumption.handle == "resume-123"

    @pytest.mark.asyncio
    async def test_token_claims_override_query_param_tenant_and_company(
        self, session_init_runtime
    ):
        claims = ws_auth.WsTokenClaims(
            sub="sip-user-123",
            tenant_id="tenant-from-token",
            company_id="company-from-token",
            exp=time.time() + 60,
            jti="jti-123",
        )
        runtime = session_init_runtime(ws_secret="test-secret", token_claims=claims)
        websocket = _FakeWebSocket(
            query_params={
                "industry": "electronics",
                "tenantId": "tenant-from-query",
                "companyId": "company-from-query",
                "token": "signed-token",
            }
        )

        ctx = await session_init.initialize_session(websocket, "sip-user-123", "session-abc")

        assert ctx is not None
        assert ctx.tenant_id == "tenant-from-token"
        assert ctx.company_id == "company-from-token"
        create_kwargs = runtime.session_service.create_session.await_args.kwargs
        assert create_kwargs["state"]["app:company_id"] == "company-from-token"


class TestOriginPolicy:
    def test_allow_missing_origin_default_false(self):
        from app.api.v1.public.settings import PublicRuntimeSettings

        settings = PublicRuntimeSettings()
        assert settings.allow_missing_ws_origin is False

    def test_allow_missing_origin_can_be_enabled(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"ALLOW_MISSING_WS_ORIGIN": "true"}):
            from app.api.v1.public.settings import PublicRuntimeSettings

            settings = PublicRuntimeSettings()
            assert settings.allow_missing_ws_origin is True
