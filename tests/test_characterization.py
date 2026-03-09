"""Phase 0 — Baseline characterization tests.

Captures exact current behavior BEFORE the registry migration.
These tests are the regression safety net for all subsequent phases.
Do NOT modify these tests during migration — they document the pre-migration contract.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient


@pytest.fixture
def app():
    """Import the FastAPI app."""
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def main_module():
    """Import main module for helper/runtime characterization tests."""
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

# ═══ Industry + Company Config Characterization ═══


class TestBuildSessionStateCharacterization:
    """Characterize build_session_state() — exact keys and value shapes."""

    def test_returns_all_expected_app_keys(self, sample_electronics_config):
        from app.configs.industry_loader import build_session_state

        state = build_session_state(sample_electronics_config, "electronics")
        expected_keys = {"app:industry", "app:industry_config", "app:voice", "app:greeting"}
        assert expected_keys == set(state.keys()), (
            f"build_session_state keys changed. Expected {expected_keys}, got {set(state.keys())}"
        )

    def test_industry_value_preserves_casing_from_caller(self, sample_electronics_config):
        from app.configs.industry_loader import build_session_state

        # build_session_state stores the industry string as-is.
        # main.py normalizes to lowercase BEFORE calling build_session_state.
        state = build_session_state(sample_electronics_config, "Electronics")
        assert state["app:industry"] == "Electronics"

        state2 = build_session_state(sample_electronics_config, "electronics")
        assert state2["app:industry"] == "electronics"

    def test_industry_config_is_full_dict(self, sample_electronics_config):
        from app.configs.industry_loader import build_session_state

        state = build_session_state(sample_electronics_config, "electronics")
        config = state["app:industry_config"]
        assert isinstance(config, dict)
        assert config["name"] == "Electronics & Gadgets"
        assert config["voice"] == "Aoede"

    def test_voice_defaults_to_aoede(self):
        from app.configs.industry_loader import build_session_state

        state = build_session_state({"name": "Unknown"}, "unknown")
        assert state["app:voice"] == "Aoede"

    @pytest.mark.parametrize("industry,expected_voice", [
        ("electronics", "Aoede"),
        ("hotel", "Puck"),
        ("automotive", "Charon"),
        ("fashion", "Kore"),
    ])
    def test_voice_per_industry(self, industry, expected_voice):
        from app.configs.industry_loader import build_session_state, LOCAL_INDUSTRY_CONFIGS

        config = LOCAL_INDUSTRY_CONFIGS[industry]
        state = build_session_state(config, industry)
        assert state["app:voice"] == expected_voice


class TestBuildCompanySessionStateCharacterization:
    """Characterize build_company_session_state() — exact keys and value shapes."""

    def test_returns_all_expected_company_keys(self):
        from app.configs.company_loader import build_company_session_state

        state = build_company_session_state(
            company_id="ekaette-electronics",
            profile={"name": "Test Store", "overview": "A store"},
            knowledge=[{"id": "k1", "title": "FAQ", "text": "Help text", "tags": ["faq"]}],
        )
        expected_keys = {"app:company_id", "app:company_name", "app:company_profile", "app:company_knowledge"}
        assert expected_keys == set(state.keys()), (
            f"build_company_session_state keys changed. Expected {expected_keys}, got {set(state.keys())}"
        )
        assert state["app:company_name"] == "Test Store"

    def test_company_id_is_normalized_lowercase(self):
        from app.configs.company_loader import build_company_session_state

        state = build_company_session_state(
            company_id="Ekaette-Electronics",
            profile={"name": "Store"},
            knowledge=[],
        )
        assert state["app:company_id"] == "ekaette-electronics"

    def test_profile_is_dict(self):
        from app.configs.company_loader import build_company_session_state

        profile = {"name": "Store", "overview": "About", "facts": {"hours": "9-5"}}
        state = build_company_session_state("test-co", profile, [])
        assert isinstance(state["app:company_profile"], dict)
        assert state["app:company_profile"]["name"] == "Store"

    def test_knowledge_is_list(self):
        from app.configs.company_loader import build_company_session_state

        knowledge = [
            {"id": "k1", "title": "FAQ", "text": "Help", "tags": ["faq"]},
            {"id": "k2", "title": "Policy", "text": "Return policy", "tags": ["policy"]},
        ]
        state = build_company_session_state("test-co", {}, knowledge)
        assert isinstance(state["app:company_knowledge"], list)
        assert len(state["app:company_knowledge"]) == 2


# ═══ Local Config Fallback Characterization ═══


class TestLocalIndustryConfigsCharacterization:
    """Characterize LOCAL_INDUSTRY_CONFIGS — the 4 hardcoded industries."""

    def test_exactly_four_industries(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        assert set(LOCAL_INDUSTRY_CONFIGS.keys()) == {
            "electronics", "hotel", "automotive", "fashion"
        }

    @pytest.mark.parametrize("industry,expected_name,expected_voice", [
        ("electronics", "Electronics & Gadgets", "Aoede"),
        ("hotel", "Hotels & Hospitality", "Puck"),
        ("automotive", "Automotive", "Charon"),
        ("fashion", "Fashion & Retail", "Kore"),
    ])
    def test_each_industry_has_name_voice_greeting(self, industry, expected_name, expected_voice):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        config = LOCAL_INDUSTRY_CONFIGS[industry]
        assert config["name"] == expected_name
        assert config["voice"] == expected_voice
        assert "greeting" in config and isinstance(config["greeting"], str)


class TestLocalCompanyProfilesCharacterization:
    """Characterize LOCAL_COMPANY_PROFILES — the 5 hardcoded companies."""

    def test_exactly_five_companies(self):
        from app.configs.company_loader import LOCAL_COMPANY_PROFILES

        assert set(LOCAL_COMPANY_PROFILES.keys()) == {
            "ekaette-electronics",
            "ekaette-hotel",
            "ekaette-automotive",
            "ekaette-fashion",
            "acme-hotel",
        }

    @pytest.mark.parametrize("company_id", [
        "ekaette-electronics",
        "ekaette-hotel",
        "ekaette-automotive",
        "ekaette-fashion",
        "acme-hotel",
    ])
    def test_each_company_has_required_fields(self, company_id):
        from app.configs.company_loader import LOCAL_COMPANY_PROFILES

        profile = LOCAL_COMPANY_PROFILES[company_id]
        assert isinstance(profile.get("name"), str)
        assert isinstance(profile.get("overview"), str)
        assert isinstance(profile.get("facts"), dict)


# ═══ Voice Selection Characterization ═══


class TestVoiceForIndustryCharacterization:
    """Characterize _voice_for_industry — the hardcoded voice map."""

    @pytest.mark.parametrize("industry,expected", [
        ("electronics", "Aoede"),
        ("hotel", "Puck"),
        ("automotive", "Charon"),
        ("fashion", "Kore"),
        ("unknown", "Aoede"),
        ("", "Aoede"),
        ("ELECTRONICS", "Aoede"),
        ("  Hotel  ", "Puck"),
    ])
    def test_voice_mapping(self, industry, expected):
        # Import from main module where _voice_for_industry lives
        import importlib
        main_mod = importlib.import_module("main")
        voice_fn = getattr(main_mod, "_voice_for_industry")
        assert voice_fn(industry) == expected


# ═══ Callback Injection Characterization ═══


class TestBeforeModelInjectConfigCharacterization:
    """Characterize before_model_inject_config — runtime instruction injection."""

    @pytest.mark.asyncio
    async def test_injects_industry_name_into_system_instruction(self):
        from app.agents.callbacks import before_model_inject_config
        from google.adk.models.llm_request import LlmRequest

        ctx = SimpleNamespace(
            state={
                "app:industry_config": {"name": "Electronics & Gadgets", "greeting": "Welcome!"},
            }
        )
        req = LlmRequest(model="test", contents=[])
        await before_model_inject_config(ctx, req)
        assert "Electronics & Gadgets" in req.config.system_instruction

    @pytest.mark.asyncio
    async def test_injects_company_context(self):
        from app.agents.callbacks import before_model_inject_config
        from google.adk.models.llm_request import LlmRequest

        ctx = SimpleNamespace(
            state={
                "app:industry_config": {"name": "Hotels"},
                "app:company_id": "acme-hotel",
                "app:company_profile": {"name": "Acme Grand Hotel", "facts": {"rooms": "120"}},
                "app:company_knowledge": [
                    {"title": "Late checkout", "text": "Available until 1 PM for premium guests."}
                ],
            }
        )
        req = LlmRequest(model="test", contents=[])
        await before_model_inject_config(ctx, req)
        instruction = req.config.system_instruction
        assert "acme-hotel" in instruction
        assert "Acme Grand Hotel" in instruction
        assert "rooms" in instruction

    @pytest.mark.asyncio
    async def test_greeting_only_injected_on_first_turn(self):
        from app.agents.callbacks import before_model_inject_config
        from google.adk.models.llm_request import LlmRequest

        # First turn — no temp:greeted
        ctx = SimpleNamespace(
            state={"app:industry_config": {"name": "Test", "greeting": "Hello!"}}
        )
        req = LlmRequest(model="test", contents=[])
        await before_model_inject_config(ctx, req)
        assert "Hello!" in req.config.system_instruction

        # Second turn — temp:greeted is True
        ctx2 = SimpleNamespace(
            state={
                "app:industry_config": {"name": "Test", "greeting": "Hello!"},
                "temp:greeted": True,
            }
        )
        req2 = LlmRequest(model="test", contents=[])
        await before_model_inject_config(ctx2, req2)
        assert "Hello!" not in req2.config.system_instruction
        assert "Do NOT greet again" in req2.config.system_instruction

    @pytest.mark.asyncio
    async def test_no_op_when_no_config(self):
        from app.agents.callbacks import before_model_inject_config
        from google.adk.models.llm_request import LlmRequest

        ctx = SimpleNamespace(state={})
        req = LlmRequest(model="test", contents=[])
        result = await before_model_inject_config(ctx, req)
        assert result is None
        # LlmRequest initializes with a default empty GenerateContentConfig.
        # When no industry/company config exists, system_instruction stays None.
        assert req.config.system_instruction is None


# ═══ Token Endpoint Response Shape Characterization ═══


class TestTokenEndpointResponseShape:
    """Characterize the POST /api/token response — exact fields returned."""

    @pytest.mark.asyncio
    async def test_response_contains_expected_fields(self, client, main_module, monkeypatch):
        """Document the current token response shape.

        The response must contain these exact keys (pre-migration baseline).
        """
        expected_fields = {
            "token",
            "expiresAt",
            "maxUses",
            "industry",
            "companyId",
            "tenantId",
            "userId",
            "model",
            "fallbackModelUsed",
            "manualVadActive",
            "vadMode",
            "voice",  # Added in Phase 2
        }
        fake_client = _FakeTokenClient()
        monkeypatch.setattr(main_module, "TOKEN_CLIENT", fake_client)
        monkeypatch.setattr(main_module, "TOKEN_ALLOWED_TENANTS", {"public"})

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
        assert set(payload.keys()) == expected_fields
        assert isinstance(payload["token"], str)
        assert isinstance(payload["expiresAt"], str)
        assert isinstance(payload["maxUses"], int)
        assert isinstance(payload["industry"], str)
        assert isinstance(payload["companyId"], str)
        assert isinstance(payload["tenantId"], str)
        assert isinstance(payload["userId"], str)
        assert isinstance(payload["model"], str)
        assert isinstance(payload["fallbackModelUsed"], bool)
        assert isinstance(payload["manualVadActive"], bool)
        assert payload["vadMode"] in {"auto", "manual"}


# ═══ Session Started Message Shape Characterization ═══


class TestSessionStartedMessageShape:
    """Characterize the session_started WebSocket message — exact fields."""

    def test_session_started_fields(self, app, main_module, monkeypatch, hotel_session_state):
        """Document the current session_started message shape.

        Fields sent at main.py:716-724.
        """
        expected_fields = {
            "type",
            "sessionId",
            "industry",
            "companyId",
            "tenantId",
            "voice",
            "manualVadActive",
            "vadMode",
        }

        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return SimpleNamespace(id=session_id, state=dict(hotel_session_state))

        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))

        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=electronics&companyId=ekaette-electronics",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["type"] == "session_started"
                assert set(payload.keys()) == expected_fields
                assert isinstance(payload["sessionId"], str) and payload["sessionId"]
                assert isinstance(payload["industry"], str) and payload["industry"]
                assert isinstance(payload["companyId"], str) and payload["companyId"]
                assert isinstance(payload["voice"], str) and payload["voice"]
                assert isinstance(payload["manualVadActive"], bool)
                assert payload["vadMode"] in {"auto", "manual"}
                ws.close(code=1000)


# ═══ Session Resumption Lock Characterization ═══


class TestSessionResumptionLockCharacterization:
    """Characterize session resumption — industry/company lock from state."""

    def test_resumed_industry_overrides_query_param(
        self,
        app,
        main_module,
        monkeypatch,
        hotel_session_state,
    ):
        """When session exists with app:industry, query param is ignored.

        Runtime websocket characterization of main.py lock behavior.
        """
        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return SimpleNamespace(id=session_id, state=dict(hotel_session_state))

        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))

        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=electronics&companyId=ekaette-electronics",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["industry"] == "hotel"
                assert payload["companyId"] == "ekaette-hotel"
                ws.close(code=1000)

    def test_no_resumed_state_uses_query_param(self, app, main_module, monkeypatch):
        """When session has no industry/company, query params are used."""
        class _FakeSessionService:
            async def get_session(self, *, app_name, user_id, session_id):
                return None

            async def create_session(self, **kwargs):
                return SimpleNamespace(
                    id=kwargs.get("session_id", "sess_1"),
                    state=kwargs.get("state", {}),
                )

        async def _fake_load_industry_config(_db, industry):
            return {
                "name": f"{industry.title()}",
                "voice": "Aoede",
                "greeting": f"Welcome to {industry}.",
            }

        async def _fake_load_company_profile(_db, company_id):
            return {
                "company_id": company_id,
                "name": f"Company {company_id}",
                "overview": "",
                "facts": {},
                "links": [],
                "system_connectors": {},
            }

        async def _fake_load_company_knowledge(_db, company_id):
            return []

        monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
        monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))
        monkeypatch.setattr(main_module, "load_industry_config", _fake_load_industry_config)
        monkeypatch.setattr(main_module, "load_company_profile", _fake_load_company_profile)
        monkeypatch.setattr(main_module, "load_company_knowledge", _fake_load_company_knowledge)

        with TestClient(app) as tc:
            with tc.websocket_connect(
                "/ws/user_123/session_abc?industry=automotive&companyId=ekaette-automotive",
                headers={"origin": "http://localhost:5173"},
            ) as ws:
                payload = json.loads(ws.receive_text())
                assert payload["industry"] == "automotive"
                assert payload["companyId"] == "ekaette-automotive"
                ws.close(code=1000)


# ═══ Tool Behavior Characterization (Pre-Scoping Baseline) ═══


@pytest.mark.xfail(
    reason="Legacy baseline only: booking tools are now tenant/company scoped in Phase 3+.",
    strict=True,
)
class TestBookingToolsNoCompanyScopingBaseline:
    """Document that booking tools currently do NOT filter by company.

    This is the behavior we are migrating AWAY from in Phase 3.
    These tests should FAIL after Phase 3 (company scoping is added).
    """

    @pytest.mark.asyncio
    async def test_check_availability_has_no_company_filter(self):
        """check_availability queries booking_slots without company_id filter."""
        from app.tools import booking_tools

        query = MagicMock()
        query.where.return_value = query
        query.stream.return_value = [
            SimpleNamespace(
                id="slot-1",
                to_dict=MagicMock(
                    return_value={
                        "date": "2026-03-01",
                        "time": "10:00",
                        "location": "Lagos - Ikeja",
                        "available": True,
                    }
                ),
            )
        ]
        db = MagicMock()
        db.collection.return_value = query

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(booking_tools, "_get_firestore_db", lambda: db)
            result = await booking_tools.check_availability(
                date="2026-03-01",
                location="Lagos - Ikeja",
            )

        assert "error" not in result
        db.collection.assert_called_once_with("booking_slots")
        where_calls = [call.args for call in query.where.call_args_list]
        assert ("date", "==", "2026-03-01") in where_calls
        assert ("location", "==", "Lagos - Ikeja") in where_calls
        assert not any(args and args[0] == "company_id" for args in where_calls), (
            "Phase 0 baseline: check_availability should NOT filter by company_id yet. "
            "This should fail once Phase 3 company scoping is implemented."
        )

    @pytest.mark.asyncio
    async def test_create_booking_has_no_company_field(self):
        """create_booking does NOT store company_id on the booking document."""
        from app.tools import booking_tools

        slot_ref = MagicMock()
        slot_doc = MagicMock()
        slot_doc.exists = True
        slot_doc.to_dict.return_value = {
            "date": "2026-03-01",
            "time": "10:00",
            "location": "Lagos - Ikeja",
            "available": True,
        }
        slot_ref.get.return_value = slot_doc

        booking_ref = MagicMock()
        batch = MagicMock()
        batch.commit = MagicMock(return_value=None)

        slot_collection = MagicMock()
        slot_collection.document.return_value = slot_ref
        booking_collection = MagicMock()
        booking_collection.document.return_value = booking_ref

        db = MagicMock()
        db.batch.return_value = batch
        db.collection.side_effect = lambda name: (
            slot_collection if name == "booking_slots" else booking_collection
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(booking_tools, "_get_firestore_db", lambda: db)
            result = await booking_tools.create_booking(
                slot_id="slot-1",
                user_id="user-1",
                user_name="Test User",
                device_name="iPhone 15",
                service_type="booking",
            )

        assert "error" not in result
        _, booking_data = batch.set.call_args.args
        assert isinstance(booking_data, dict)
        assert "company_id" not in booking_data, (
            "Phase 0 baseline: create_booking booking document should NOT include company_id yet. "
            "This should fail once Phase 3 company scoping is implemented."
        )


@pytest.mark.xfail(
    reason="Legacy baseline only: catalog tools are now tenant/company scoped in Phase 3+.",
    strict=True,
)
class TestCatalogToolsNoCompanyScopingBaseline:
    """Document that catalog tools currently do NOT filter by company."""

    @pytest.mark.asyncio
    async def test_search_catalog_has_no_company_filter(self):
        """search_catalog queries products collection globally."""
        from app.tools import catalog_tools

        query = MagicMock()
        query.where.return_value = query
        query.limit.return_value = query
        query.stream.return_value = [
            SimpleNamespace(
                id="prod-1",
                to_dict=MagicMock(
                    return_value={
                        "name": "iPhone 15 Pro",
                        "brand": "Apple",
                        "category": "smartphones",
                        "description": "Flagship phone",
                        "in_stock": True,
                        "features": ["48MP camera"],
                    }
                ),
            )
        ]
        db = MagicMock()
        db.collection.return_value = query

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(catalog_tools, "_get_firestore_db", lambda: db)
            result = await catalog_tools.search_catalog(query="iphone", category="smartphones")

        assert "error" not in result
        db.collection.assert_called_once_with("products")
        where_calls = [call.args for call in query.where.call_args_list]
        # Category filtering moved to client-side via _product_matches_category
        # (supports fuzzy alias matching), so .where("category", ...) is no
        # longer called on the Firestore query.
        assert not any(args and args[0] == "category" for args in where_calls)
        assert not any(args and args[0] == "company_id" for args in where_calls), (
            "Phase 0 baseline: search_catalog should NOT filter by company_id yet. "
            "This should fail once Phase 3 company scoping is implemented."
        )


# ═══ Industry-Company Mapping Characterization ═══


class TestIndustryCompanyMappingCharacterization:
    """Characterize the expected industry-to-company mapping."""

    @pytest.mark.parametrize("industry,expected_company", [
        ("electronics", "ekaette-electronics"),
        ("hotel", "ekaette-hotel"),
        ("automotive", "ekaette-automotive"),
        ("fashion", "ekaette-fashion"),
    ])
    def test_default_company_per_industry(self, industry, expected_company):
        """Each industry maps to a default company profile.

        Frontend uses INDUSTRY_COMPANY_MAP for this mapping.
        Backend local configs have matching company profiles.
        """
        from app.configs.company_loader import LOCAL_COMPANY_PROFILES

        assert expected_company in LOCAL_COMPANY_PROFILES
