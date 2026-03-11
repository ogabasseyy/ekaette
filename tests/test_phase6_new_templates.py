"""Phase 6 — Telecom + Aviation template tests (TDD Red).

Config-first, no code changes. These tests verify that:
1. Telecom template resolves with correct capability set
2. Aviation template resolves with support/status-only capabilities
3. Aviation create_booking tool call → blocked by capability guard
4. Aviation search_company_knowledge → allowed
5. Telecom search_catalog → allowed (plan catalog)
6. Onboarding config endpoint returns telecom + aviation-support for seeded tenants
7. Template metadata is normalized for the new templates

Data fixtures live in tests/fixtures/registry/ as JSON files consumed by scripts.registry CLI.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══ Helpers (shared with Phase 5 tests) ═══


def _mock_firestore_doc(data: dict[str, Any] | None, doc_id: str = "") -> MagicMock:
    doc = MagicMock()
    doc.exists = data is not None
    doc.id = doc_id
    doc.to_dict = MagicMock(return_value=data if data else {})
    return doc


def _mock_db_with_docs(docs: dict[str, dict[str, Any] | None]) -> MagicMock:
    """Create a mock Firestore client supporting nested paths + collection streaming."""
    db = MagicMock()
    store = dict(docs)

    def _collection(name: str) -> MagicMock:
        col = MagicMock()

        def _document(doc_id: str) -> MagicMock:
            ref = MagicMock()
            key = f"{name}/{doc_id}"
            mock_doc = _mock_firestore_doc(store.get(key), doc_id)
            ref.get = AsyncMock(return_value=mock_doc)

            def _subcollection(sub_name: str) -> MagicMock:
                sub_col = MagicMock()

                def _sub_document(sub_doc_id: str) -> MagicMock:
                    sub_ref = MagicMock()
                    sub_key = f"{name}/{doc_id}/{sub_name}/{sub_doc_id}"
                    sub_mock_doc = _mock_firestore_doc(store.get(sub_key), sub_doc_id)
                    sub_ref.get = AsyncMock(return_value=sub_mock_doc)

                    def _sub_subcollection(ssub_name: str) -> MagicMock:
                        ssub_col = MagicMock()
                        ssub_col.document = lambda ssub_doc_id: MagicMock(
                            get=AsyncMock(
                                return_value=_mock_firestore_doc(
                                    store.get(f"{name}/{doc_id}/{sub_name}/{sub_doc_id}/{ssub_name}/{ssub_doc_id}"),
                                    ssub_doc_id,
                                )
                            )
                        )
                        return ssub_col

                    sub_ref.collection = _sub_subcollection
                    return sub_ref

                sub_col.document = _sub_document

                # Stream support for subcollection
                def _sub_stream():
                    prefix = f"{name}/{doc_id}/{sub_name}/"
                    results = []
                    for k, v in store.items():
                        if k.startswith(prefix):
                            remainder = k[len(prefix):]
                            if "/" not in remainder and v is not None:
                                results.append(_mock_firestore_doc(v, remainder))
                    return results

                sub_col.stream = _sub_stream
                return sub_col

            ref.collection = _subcollection
            return ref

        col.document = _document

        # Stream support for top-level collection
        def _stream():
            prefix = f"{name}/"
            results = []
            for k, v in store.items():
                if k.startswith(prefix):
                    remainder = k[len(prefix):]
                    if "/" not in remainder and v is not None:
                        results.append(_mock_firestore_doc(v, remainder))
            return results

        col.stream = _stream
        return col

    db.collection = _collection
    return db


def _make_tool_context(state: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(state=dict(state), agent_name="test_agent")


# ═══ Template Fixtures ═══


TELECOM_TEMPLATE = {
    "schema_version": 1,
    "id": "telecom",
    "label": "Telecom",
    "category": "telecom",
    "description": "Data plan comparison, policy support, and store visit booking.",
    "default_voice": "Fenrir",
    "greeting_policy": "Welcome to Telecom Support. How can I help with your plan today?",
    "theme": {
        "accent": "oklch(72% 0.16 220)",
        "accentSoft": "oklch(64% 0.12 235)",
        "title": "Telecom Support Center",
        "hint": "Plan comparison, policy lookup, and store visit scheduling.",
    },
    "capabilities": [
        "policy_qa",
        "public_search_fallback",
        "catalog_lookup",
        "device_comparison_support",
        "outbound_messaging",
    ],
    "enabled_agents": ["support_agent", "catalog_agent"],
    "connectors_supported": [],
    "status": "active",
}

AVIATION_TEMPLATE = {
    "schema_version": 1,
    "id": "aviation-support",
    "label": "Aviation",
    "category": "aviation",
    "description": "Flight status, schedule lookup, and customer support.",
    "default_voice": "Puck",
    "greeting_policy": "Welcome to Aviation Support. I can help with flight status and policies.",
    "theme": {
        "accent": "oklch(68% 0.12 260)",
        "accentSoft": "oklch(60% 0.09 275)",
        "title": "Aviation Customer Support",
        "hint": "Flight status, schedule lookup, and travel policy assistance.",
    },
    "capabilities": [
        "policy_qa",
        "public_search_fallback",
        "flight_status_lookup",
        "schedule_lookup",
        "escalation_handoff",
        "outbound_messaging",
    ],
    "enabled_agents": ["support_agent"],
    "connectors_supported": [],
    "status": "active",
}

EKAETTE_TELECOM_COMPANY = {
    "schema_version": 1,
    "company_id": "ekaette-telecom",
    "tenant_id": "public",
    "industry_template_id": "telecom",
    "display_name": "Ekaette Telecom",
    "overview": "Mobile plans, data comparison, and account support.",
    "facts": {"support_hours": "24/7", "coverage": "nationwide"},
    "links": [],
    "connectors": {},
    "capability_overrides": {},
    "ui_overrides": {},
    "status": "active",
}

EKAETTE_AVIATION_COMPANY = {
    "schema_version": 1,
    "company_id": "ekaette-aviation",
    "tenant_id": "public",
    "industry_template_id": "aviation-support",
    "display_name": "Ekaette Airways",
    "overview": "Customer support for flight status, policies, and escalation.",
    "facts": {"hub": "Murtala Muhammed International Airport", "fleet_size": 12},
    "links": [],
    "connectors": {},
    "capability_overrides": {},
    "ui_overrides": {},
    "status": "active",
}

# Registry subset for onboarding tests (seeded templates + companies)
ELECTRONICS_TEMPLATE = {
    "schema_version": 1,
    "id": "electronics",
    "label": "Electronics & Gadgets",
    "category": "retail",
    "description": "Trade-ins, valuation, negotiation, pickup booking.",
    "default_voice": "Aoede",
    "greeting_policy": "Welcome! I can help with device trade-ins.",
    "theme": {
        "accent": "oklch(74% 0.21 158)",
        "accentSoft": "oklch(74% 0.21 158 / 0.15)",
        "title": "Electronics Trade Desk",
        "hint": "Inspect. Value. Negotiate. Book pickup.",
    },
    "capabilities": ["catalog_lookup", "valuation_tradein", "booking_reservations", "policy_qa", "connector_dispatch", "outbound_messaging"],
    "enabled_agents": ["vision_agent", "valuation_agent", "booking_agent"],
    "connectors_supported": ["crm"],
    "status": "active",
}


# ═══ 1. Telecom template resolves with correct capability set ═══


class TestTelecomTemplateResolution:

    @pytest.mark.asyncio
    async def test_telecom_resolves_with_correct_capabilities(self):
        """Telecom template includes policy_qa, catalog_lookup but NOT booking_reservations."""
        from app.configs.registry_loader import resolve_registry_config

        db = _mock_db_with_docs({
            "industry_templates/telecom": TELECOM_TEMPLATE,
            "tenants/public/companies/ekaette-telecom": EKAETTE_TELECOM_COMPANY,
        })
        config = await resolve_registry_config(db, "public", "ekaette-telecom")

        assert config is not None
        assert config.industry_template_id == "telecom"
        assert config.template_category == "telecom"
        assert config.voice == "Fenrir"
        assert "policy_qa" in config.capabilities
        assert "catalog_lookup" in config.capabilities
        assert "public_search_fallback" in config.capabilities
        assert "device_comparison_support" in config.capabilities
        # Telecom should NOT have booking/valuation by default
        assert "booking_reservations" not in config.capabilities
        assert "valuation_tradein" not in config.capabilities

    @pytest.mark.asyncio
    async def test_telecom_session_state_has_canonical_keys(self):
        """Telecom config produces correct session state with all canonical keys."""
        from app.configs.registry_loader import (
            build_session_state_from_registry,
            resolve_registry_config,
        )

        db = _mock_db_with_docs({
            "industry_templates/telecom": TELECOM_TEMPLATE,
            "tenants/public/companies/ekaette-telecom": EKAETTE_TELECOM_COMPANY,
        })
        config = await resolve_registry_config(db, "public", "ekaette-telecom")
        state = build_session_state_from_registry(config)

        assert state["app:industry"] == "telecom"
        assert state["app:industry_template_id"] == "telecom"
        assert state["app:tenant_id"] == "public"
        assert state["app:voice"] == "Fenrir"
        assert "catalog_lookup" in state["app:capabilities"]
        assert state["app:ui_theme"]["title"] == "Telecom Support Center"


# ═══ 2. Aviation template resolves with support/status-only capabilities ═══


class TestAviationTemplateResolution:

    @pytest.mark.asyncio
    async def test_aviation_resolves_with_support_status_capabilities(self):
        """Aviation template has flight_status + policy but NOT booking/valuation."""
        from app.configs.registry_loader import resolve_registry_config

        db = _mock_db_with_docs({
            "industry_templates/aviation-support": AVIATION_TEMPLATE,
            "tenants/public/companies/ekaette-aviation": EKAETTE_AVIATION_COMPANY,
        })
        config = await resolve_registry_config(db, "public", "ekaette-aviation")

        assert config is not None
        assert config.industry_template_id == "aviation-support"
        assert config.template_category == "aviation"
        assert config.voice == "Puck"
        assert "policy_qa" in config.capabilities
        assert "flight_status_lookup" in config.capabilities
        assert "schedule_lookup" in config.capabilities
        assert "escalation_handoff" in config.capabilities
        # Aviation MUST NOT have transactional capabilities
        assert "booking_reservations" not in config.capabilities
        assert "valuation_tradein" not in config.capabilities
        assert "catalog_lookup" not in config.capabilities


# ═══ 3. Aviation create_booking → blocked by capability guard ═══


class TestAviationCapabilityGuard:

    @pytest.mark.asyncio
    async def test_aviation_create_booking_blocked(self):
        """Aviation session cannot use create_booking (no booking_reservations cap)."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="create_booking")
        ctx = _make_tool_context({
            "app:capabilities": list(AVIATION_TEMPLATE["capabilities"]),
        })
        result = await before_tool_capability_guard(tool, {}, ctx)

        assert isinstance(result, dict)
        assert result["error"] == "capability_not_enabled"
        assert result["tool"] == "create_booking"
        assert result["required"] == "booking_reservations"

    @pytest.mark.asyncio
    async def test_aviation_grade_and_value_blocked(self):
        """Aviation session cannot use grade_and_value_tool (no valuation_tradein cap)."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = _make_tool_context({
            "app:capabilities": list(AVIATION_TEMPLATE["capabilities"]),
        })
        result = await before_tool_capability_guard(tool, {}, ctx)

        assert isinstance(result, dict)
        assert result["error"] == "capability_not_enabled"
        assert result["required"] == "valuation_tradein"


# ═══ 4. Aviation search_company_knowledge → allowed ═══


class TestAviationAllowedTools:

    @pytest.mark.asyncio
    async def test_aviation_search_knowledge_allowed(self):
        """Aviation session CAN use search_company_knowledge (policy_qa cap)."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="search_company_knowledge")
        ctx = _make_tool_context({
            "app:capabilities": list(AVIATION_TEMPLATE["capabilities"]),
        })
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result is None  # Allowed

    @pytest.mark.asyncio
    async def test_aviation_get_company_profile_fact_allowed(self):
        """Aviation session CAN use get_company_profile_fact (policy_qa cap)."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="get_company_profile_fact")
        ctx = _make_tool_context({
            "app:capabilities": list(AVIATION_TEMPLATE["capabilities"]),
        })
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result is None


# ═══ 5. Telecom search_catalog → allowed ═══


class TestTelecomAllowedTools:

    @pytest.mark.asyncio
    async def test_telecom_search_catalog_allowed(self):
        """Telecom session CAN use search_catalog (catalog_lookup for plan catalog)."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="search_catalog")
        ctx = _make_tool_context({
            "app:capabilities": list(TELECOM_TEMPLATE["capabilities"]),
        })
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_telecom_create_booking_blocked(self):
        """Telecom base template does NOT include booking_reservations."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="create_booking")
        ctx = _make_tool_context({
            "app:capabilities": list(TELECOM_TEMPLATE["capabilities"]),
        })
        result = await before_tool_capability_guard(tool, {}, ctx)

        assert isinstance(result, dict)
        assert result["error"] == "capability_not_enabled"


# ═══ 6. Onboarding config returns telecom + aviation ═══


class TestOnboardingConfigWithNewTemplates:

    @pytest.fixture(autouse=True)
    def _enable_registry(self, monkeypatch):
        monkeypatch.setenv("REGISTRY_ENABLED", "true")

    @pytest.mark.asyncio
    async def test_onboarding_config_includes_telecom_and_aviation(self):
        """When registry includes telecom + aviation-support, onboarding includes both."""
        from app.configs.registry_loader import build_onboarding_config

        db = _mock_db_with_docs({
            "industry_templates/electronics": ELECTRONICS_TEMPLATE,
            "industry_templates/telecom": TELECOM_TEMPLATE,
            "industry_templates/aviation-support": AVIATION_TEMPLATE,
            "tenants/public/companies/ekaette-electronics": {
                "schema_version": 1,
                "company_id": "ekaette-electronics",
                "tenant_id": "public",
                "industry_template_id": "electronics",
                "display_name": "Ogabassey Gadgets",
                "spoken_name": "Awgabassey Gadgets",
            },
            "tenants/public/companies/ekaette-telecom": EKAETTE_TELECOM_COMPANY,
            "tenants/public/companies/ekaette-aviation": EKAETTE_AVIATION_COMPANY,
        })
        config = await build_onboarding_config(db, "public")

        template_ids = [t["id"] for t in config["templates"]]
        assert "telecom" in template_ids
        assert "aviation-support" in template_ids
        assert "electronics" in template_ids

        company_ids = [c["id"] for c in config["companies"]]
        assert "ekaette-telecom" in company_ids
        assert "ekaette-aviation" in company_ids

    @pytest.mark.asyncio
    async def test_telecom_template_metadata_in_onboarding(self):
        """Telecom template has correct label, voice, theme in onboarding payload."""
        from app.configs.registry_loader import build_onboarding_config

        db = _mock_db_with_docs({
            "industry_templates/telecom": TELECOM_TEMPLATE,
            "tenants/public/companies/ekaette-telecom": EKAETTE_TELECOM_COMPANY,
        })
        config = await build_onboarding_config(db, "public")

        telecom = next(t for t in config["templates"] if t["id"] == "telecom")
        assert telecom["label"] == "Telecom"
        assert telecom["defaultVoice"] == "Fenrir"
        assert telecom["theme"]["accent"] == "oklch(72% 0.16 220)"
        assert telecom["status"] == "active"
        assert "catalog_lookup" in telecom["capabilities"]

    @pytest.mark.asyncio
    async def test_aviation_template_metadata_in_onboarding(self):
        """Aviation template has correct label, voice, theme in onboarding payload."""
        from app.configs.registry_loader import build_onboarding_config

        db = _mock_db_with_docs({
            "industry_templates/aviation-support": AVIATION_TEMPLATE,
            "tenants/public/companies/ekaette-aviation": EKAETTE_AVIATION_COMPANY,
        })
        config = await build_onboarding_config(db, "public")

        aviation = next(t for t in config["templates"] if t["id"] == "aviation-support")
        assert aviation["label"] == "Aviation"
        assert aviation["defaultVoice"] == "Puck"
        assert aviation["theme"]["accent"] == "oklch(68% 0.12 260)"
        assert aviation["status"] == "active"
        assert "flight_status_lookup" in aviation["capabilities"]
        assert "booking_reservations" not in aviation["capabilities"]


# ═══ 7. Template data validates through registry schema ═══


class TestNewTemplateSchemaValidity:

    def test_telecom_template_passes_schema_validation(self):
        """Telecom template fixture passes validate_template."""
        from app.configs.registry_schema import validate_template

        errors = validate_template(TELECOM_TEMPLATE)
        assert errors == [], f"Telecom template validation failed: {errors}"

    def test_aviation_template_passes_schema_validation(self):
        """Aviation template fixture passes validate_template."""
        from app.configs.registry_schema import validate_template

        errors = validate_template(AVIATION_TEMPLATE)
        assert errors == [], f"Aviation template validation failed: {errors}"

    def test_telecom_company_passes_schema_validation(self):
        """Telecom company fixture passes validate_company."""
        from app.configs.registry_schema import validate_company

        errors = validate_company(EKAETTE_TELECOM_COMPANY)
        assert errors == [], f"Telecom company validation failed: {errors}"

    def test_aviation_company_passes_schema_validation(self):
        """Aviation company fixture passes validate_company."""
        from app.configs.registry_schema import validate_company

        errors = validate_company(EKAETTE_AVIATION_COMPANY)
        assert errors == [], f"Aviation company validation failed: {errors}"

    def test_seed_templates_accepts_new_templates(self):
        """Phase 5 CLI seed_templates writes telecom + aviation without errors."""
        from tests.test_phase5_provisioning import FakeFirestoreDB

        from scripts.registry import seed_templates

        db = FakeFirestoreDB()
        result = seed_templates(db, [TELECOM_TEMPLATE, AVIATION_TEMPLATE])

        assert result["written"] == 2
        assert result["errors"] == []
        assert db.get_doc("industry_templates/telecom") is not None
        assert db.get_doc("industry_templates/aviation-support") is not None

    def test_provision_company_accepts_new_companies(self):
        """Phase 5 CLI provision_company writes telecom + aviation companies."""
        from tests.test_phase5_provisioning import FakeFirestoreDB

        from scripts.registry import provision_company, seed_templates

        db = FakeFirestoreDB()
        # Seed templates first (provision_company validates template exists)
        seed_templates(db, [TELECOM_TEMPLATE, AVIATION_TEMPLATE])

        result_t = provision_company(db, EKAETTE_TELECOM_COMPANY)
        result_a = provision_company(db, EKAETTE_AVIATION_COMPANY)

        assert result_t["success"] is True, f"Telecom: {result_t['errors']}"
        assert result_a["success"] is True, f"Aviation: {result_a['errors']}"

        assert db.get_doc("tenants/public/companies/ekaette-telecom") is not None
        assert db.get_doc("tenants/public/companies/ekaette-aviation") is not None


# ═══ 8. Data fixtures exist on disk ═══


class TestDataFixturesExist:

    def test_telecom_template_json_exists(self):
        """Data fixture for telecom template exists at tests/fixtures/registry/templates/telecom.json."""
        import json
        from pathlib import Path

        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "registry"
            / "templates"
            / "telecom.json"
        )
        assert fixture_path.exists(), f"Missing fixture: {fixture_path}"

        data = json.loads(fixture_path.read_text())
        assert data["id"] == "telecom"
        assert "capabilities" in data

    def test_aviation_template_json_exists(self):
        """Data fixture for aviation template exists at tests/fixtures/registry/templates/aviation-support.json."""
        import json
        from pathlib import Path

        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "registry"
            / "templates"
            / "aviation-support.json"
        )
        assert fixture_path.exists(), f"Missing fixture: {fixture_path}"

        data = json.loads(fixture_path.read_text())
        assert data["id"] == "aviation-support"
        assert "capabilities" in data

    def test_telecom_company_json_exists(self):
        """Data fixture for telecom company exists at tests/fixtures/registry/companies/ekaette-telecom.json."""
        import json
        from pathlib import Path

        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "registry"
            / "companies"
            / "ekaette-telecom.json"
        )
        assert fixture_path.exists(), f"Missing fixture: {fixture_path}"

        data = json.loads(fixture_path.read_text())
        assert data["company_id"] == "ekaette-telecom"

    def test_aviation_company_json_exists(self):
        """Data fixture for aviation company exists at tests/fixtures/registry/companies/ekaette-aviation.json."""
        import json
        from pathlib import Path

        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "registry"
            / "companies"
            / "ekaette-aviation.json"
        )
        assert fixture_path.exists(), f"Missing fixture: {fixture_path}"

        data = json.loads(fixture_path.read_text())
        assert data["company_id"] == "ekaette-aviation"
