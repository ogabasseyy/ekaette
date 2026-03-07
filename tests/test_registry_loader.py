"""Phase 1 — Registry loader tests (TDD Red).

Tests for app/configs/registry_loader.py which introduces:
- ResolvedRegistryConfig dataclass
- load_industry_template(db, template_id) — Firestore lookup + fallback
- load_tenant_company(db, tenant_id, company_id) — Firestore lookup + fallback
- resolve_registry_config(db, tenant_id, company_id) — merge template + company
- build_session_state_from_registry(config) — both legacy + canonical keys
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══ Helpers ═══


def _mock_firestore_doc(data: dict[str, Any] | None) -> MagicMock:
    """Create a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.exists = data is not None
    doc.to_dict = MagicMock(return_value=data if data else {})
    return doc


def _mock_db_with_docs(docs: dict[str, dict[str, Any] | None]) -> MagicMock:
    """Create a mock Firestore client that returns specific docs by path.

    docs: mapping of "collection/doc_id" -> data (or None for missing)
    """
    db = MagicMock()

    def _collection(name: str) -> MagicMock:
        col = MagicMock()

        def _document(doc_id: str) -> MagicMock:
            ref = MagicMock()
            key = f"{name}/{doc_id}"
            mock_doc = _mock_firestore_doc(docs.get(key))
            ref.get = AsyncMock(return_value=mock_doc)

            # Support subcollection chaining for tenant-scoped paths
            def _subcollection(sub_name: str) -> MagicMock:
                sub_col = MagicMock()

                def _sub_document(sub_doc_id: str) -> MagicMock:
                    sub_ref = MagicMock()
                    sub_key = f"{name}/{doc_id}/{sub_name}/{sub_doc_id}"
                    sub_mock_doc = _mock_firestore_doc(docs.get(sub_key))
                    sub_ref.get = AsyncMock(return_value=sub_mock_doc)
                    return sub_ref

                sub_col.document = _sub_document
                return sub_col

            ref.collection = _subcollection
            return ref

        col.document = _document
        return col

    db.collection = _collection
    return db


ELECTRONICS_TEMPLATE = {
    "schema_version": 1,
    "id": "electronics",
    "label": "Electronics & Gadgets",
    "category": "retail",
    "description": "Trade-ins, valuation, negotiation, pickup booking.",
    "default_voice": "Aoede",
    "greeting_policy": "Welcome! I can help you with device trade-ins, swaps, and purchases.",
    "theme": {
        "accent": "oklch(74% 0.21 158)",
        "accentSoft": "oklch(74% 0.21 158 / 0.15)",
        "title": "Electronics Trade Desk",
        "hint": "Inspect. Value. Negotiate. Book pickup.",
    },
    "capabilities": ["catalog_lookup", "valuation_tradein", "booking_reservations", "outbound_messaging"],
    "enabled_agents": ["vision_agent", "valuation_agent", "booking_agent", "catalog_agent", "support_agent"],
    "tool_policies": {},
    "prompt_overrides": {},
    "connectors_supported": ["crm"],
    "status": "active",
}

HOTEL_TEMPLATE = {
    "schema_version": 1,
    "id": "hotel",
    "label": "Hotels & Hospitality",
    "category": "hospitality",
    "description": "Reservations, room search, and guest support.",
    "default_voice": "Puck",
    "greeting_policy": "Good day! Welcome to our hotel. How can I make your stay perfect?",
    "theme": {
        "accent": "oklch(78% 0.15 55)",
        "accentSoft": "oklch(78% 0.15 55 / 0.15)",
        "title": "Hospitality Concierge",
        "hint": "Real-time booking and guest support voice assistant.",
    },
    "capabilities": ["booking_reservations", "policy_qa", "outbound_messaging"],
    "enabled_agents": ["booking_agent", "support_agent"],
    "tool_policies": {},
    "prompt_overrides": {},
    "connectors_supported": ["pms"],
    "status": "active",
}

EKAETTE_ELECTRONICS_COMPANY = {
    "schema_version": 1,
    "company_id": "ekaette-electronics",
    "tenant_id": "public",
    "industry_template_id": "electronics",
    "display_name": "Ekaette Devices Hub",
    "overview": "Trade-in focused electronics store serving Lagos and Abuja.",
    "facts": {"primary_location": "Lagos - Ikeja"},
    "links": [],
    "connectors": {"crm": {"provider": "mock"}},
    "capability_overrides": {},
    "ui_overrides": {},
    "status": "active",
}

ACME_HOTEL_COMPANY = {
    "schema_version": 1,
    "company_id": "acme-hotel",
    "tenant_id": "public",
    "industry_template_id": "hotel",
    "display_name": "Acme Grand Hotel",
    "overview": "Luxury hospitality with smart concierge service.",
    "facts": {"rooms": 120},
    "links": [],
    "connectors": {"pms": {"provider": "mock"}},
    "capability_overrides": {},
    "ui_overrides": {"voice": "Charon"},  # Company overrides template voice
    "status": "active",
}


# ═══ load_industry_template tests ═══


class TestLoadIndustryTemplate:
    """Test loading industry templates from Firestore."""

    @pytest.mark.asyncio
    async def test_loads_template_from_firestore(self):
        from app.configs.registry_loader import load_industry_template

        db = _mock_db_with_docs({
            "industry_templates/electronics": ELECTRONICS_TEMPLATE,
        })
        result = await load_industry_template(db, "electronics")
        assert result["id"] == "electronics"
        assert result["label"] == "Electronics & Gadgets"
        assert result["default_voice"] == "Aoede"
        assert "catalog_lookup" in result["capabilities"]

    @pytest.mark.asyncio
    async def test_returns_none_when_template_missing(self):
        from app.configs.registry_loader import load_industry_template

        db = _mock_db_with_docs({})
        result = await load_industry_template(db, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_db_is_none(self):
        from app.configs.registry_loader import load_industry_template

        result = await load_industry_template(None, "electronics")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_firestore_error(self):
        from app.configs.registry_loader import load_industry_template

        db = MagicMock()
        db.collection.side_effect = Exception("Firestore unavailable")
        result = await load_industry_template(db, "electronics")
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_unsupported_template_schema_version(self):
        from app.configs import RegistrySchemaVersionError
        from app.configs.registry_loader import load_industry_template

        bad_template = dict(ELECTRONICS_TEMPLATE)
        bad_template["schema_version"] = 99
        db = _mock_db_with_docs({"industry_templates/electronics": bad_template})

        with pytest.raises(RegistrySchemaVersionError, match="unsupported schema_version"):
            await load_industry_template(db, "electronics")


# ═══ load_tenant_company tests ═══


class TestLoadTenantCompany:
    """Test loading tenant-scoped company profiles."""

    @pytest.mark.asyncio
    async def test_loads_company_from_tenant_path(self):
        from app.configs.registry_loader import load_tenant_company

        db = _mock_db_with_docs({
            "tenants/public/companies/ekaette-electronics": EKAETTE_ELECTRONICS_COMPANY,
        })
        result = await load_tenant_company(db, "public", "ekaette-electronics")
        assert result["company_id"] == "ekaette-electronics"
        assert result["tenant_id"] == "public"
        assert result["industry_template_id"] == "electronics"

    @pytest.mark.asyncio
    async def test_returns_none_when_company_missing(self):
        from app.configs.registry_loader import load_tenant_company

        db = _mock_db_with_docs({})
        result = await load_tenant_company(db, "public", "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_db_is_none(self):
        from app.configs.registry_loader import load_tenant_company

        result = await load_tenant_company(None, "public", "ekaette-electronics")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_firestore_error(self):
        from app.configs.registry_loader import load_tenant_company

        db = MagicMock()
        db.collection.side_effect = Exception("Firestore unavailable")
        result = await load_tenant_company(db, "public", "ekaette-electronics")
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_company_missing_schema_version(self):
        from app.configs import RegistrySchemaVersionError
        from app.configs.registry_loader import load_tenant_company

        bad_company = dict(EKAETTE_ELECTRONICS_COMPANY)
        bad_company.pop("schema_version", None)
        db = _mock_db_with_docs({"tenants/public/companies/ekaette-electronics": bad_company})

        with pytest.raises(RegistrySchemaVersionError, match="missing integer schema_version"):
            await load_tenant_company(db, "public", "ekaette-electronics")


# ═══ resolve_registry_config tests ═══


class TestResolveRegistryConfig:
    """Test merging template + company into ResolvedRegistryConfig."""

    @pytest.mark.asyncio
    async def test_resolves_full_config(self):
        from app.configs.registry_loader import resolve_registry_config

        db = _mock_db_with_docs({
            "industry_templates/electronics": ELECTRONICS_TEMPLATE,
            "tenants/public/companies/ekaette-electronics": EKAETTE_ELECTRONICS_COMPANY,
        })
        config = await resolve_registry_config(db, "public", "ekaette-electronics")

        assert config.tenant_id == "public"
        assert config.company_id == "ekaette-electronics"
        assert config.industry_template_id == "electronics"
        assert config.template_category == "retail"
        assert config.template_label == "Electronics & Gadgets"
        assert "catalog_lookup" in config.capabilities
        assert config.voice == "Aoede"
        assert config.theme["title"] == "Electronics Trade Desk"
        assert config.greeting == ELECTRONICS_TEMPLATE["greeting_policy"]
        assert isinstance(config.registry_version, str)

    @pytest.mark.asyncio
    async def test_company_voice_override(self):
        """Company ui_overrides.voice takes precedence over template default_voice."""
        from app.configs.registry_loader import resolve_registry_config

        db = _mock_db_with_docs({
            "industry_templates/hotel": HOTEL_TEMPLATE,
            "tenants/public/companies/acme-hotel": ACME_HOTEL_COMPANY,
        })
        config = await resolve_registry_config(db, "public", "acme-hotel")
        assert config.voice == "Charon"  # From company ui_overrides, not template's Puck

    @pytest.mark.asyncio
    async def test_returns_none_when_template_missing(self):
        """Cannot resolve config without a template."""
        from app.configs.registry_loader import resolve_registry_config

        db = _mock_db_with_docs({
            "tenants/public/companies/ekaette-electronics": EKAETTE_ELECTRONICS_COMPANY,
        })
        config = await resolve_registry_config(db, "public", "ekaette-electronics")
        assert config is None

    @pytest.mark.asyncio
    async def test_returns_none_when_company_missing(self):
        """Cannot resolve config without a company."""
        from app.configs.registry_loader import resolve_registry_config

        db = _mock_db_with_docs({
            "industry_templates/electronics": ELECTRONICS_TEMPLATE,
        })
        config = await resolve_registry_config(db, "public", "nonexistent")
        assert config is None

    @pytest.mark.asyncio
    async def test_returns_none_when_db_is_none(self):
        from app.configs.registry_loader import resolve_registry_config

        config = await resolve_registry_config(None, "public", "ekaette-electronics")
        assert config is None

    @pytest.mark.asyncio
    async def test_raises_on_template_mismatch(self):
        """Template doc's id field must match the Firestore doc key."""
        from app.configs.registry_loader import (
            RegistryMismatchError,
            resolve_registry_config,
        )

        # Company says template is "fashion", and the doc at industry_templates/fashion
        # exists but its id field says "electronics" — a data integrity error.
        mismatched_template = dict(ELECTRONICS_TEMPLATE)
        mismatched_template["id"] = "electronics"  # Doesn't match the "fashion" key

        mismatched_company = dict(EKAETTE_ELECTRONICS_COMPANY)
        mismatched_company["industry_template_id"] = "fashion"

        db = _mock_db_with_docs({
            "industry_templates/fashion": mismatched_template,
            "tenants/public/companies/ekaette-electronics": mismatched_company,
        })
        with pytest.raises(RegistryMismatchError):
            await resolve_registry_config(db, "public", "ekaette-electronics")

    @pytest.mark.asyncio
    async def test_capability_overrides_merge(self):
        """Company capability_overrides can add/remove capabilities."""
        from app.configs.registry_loader import resolve_registry_config

        company_with_overrides = dict(EKAETTE_ELECTRONICS_COMPANY)
        company_with_overrides["capability_overrides"] = {
            "add": ["custom_tool"],
            "remove": ["booking_reservations"],
        }

        db = _mock_db_with_docs({
            "industry_templates/electronics": ELECTRONICS_TEMPLATE,
            "tenants/public/companies/ekaette-electronics": company_with_overrides,
        })
        config = await resolve_registry_config(db, "public", "ekaette-electronics")
        assert "custom_tool" in config.capabilities
        assert "booking_reservations" not in config.capabilities
        assert "catalog_lookup" in config.capabilities  # Unchanged

    @pytest.mark.asyncio
    async def test_malformed_shapes_fail_safe_defaults(self):
        from app.configs.registry_loader import resolve_registry_config

        malformed_template = {
            "schema_version": 1,
            "id": "electronics",
            "category": None,
            "label": 123,
            "default_voice": None,
            "greeting_policy": None,
            "theme": "not-a-dict",
            "capabilities": "catalog_lookup",  # bad shape
            "status": "active",
        }
        malformed_company = {
            "schema_version": 1,
            "company_id": "ekaette-electronics",
            "tenant_id": "public",
            "industry_template_id": "electronics",
            "connectors": "bad",
            "ui_overrides": "bad",
            "capability_overrides": {
                "add": "also-bad",
                "remove": ["catalog_lookup"],  # should have no effect because base is empty
            },
        }
        db = _mock_db_with_docs({
            "industry_templates/electronics": malformed_template,
            "tenants/public/companies/ekaette-electronics": malformed_company,
        })

        config = await resolve_registry_config(db, "public", "ekaette-electronics")
        assert config is not None
        assert config.voice == "Aoede"
        assert config.theme == {}
        assert config.greeting == ""
        assert config.capabilities == []
        assert config.connector_manifest == {}
        assert config.template_category == "electronics"
        assert config.template_label == "Electronics"

    @pytest.mark.asyncio
    async def test_registry_version_changes_when_config_changes(self):
        from app.configs.registry_loader import resolve_registry_config

        db1 = _mock_db_with_docs({
            "industry_templates/electronics": ELECTRONICS_TEMPLATE,
            "tenants/public/companies/ekaette-electronics": EKAETTE_ELECTRONICS_COMPANY,
        })
        config1 = await resolve_registry_config(db1, "public", "ekaette-electronics")

        changed_company = dict(EKAETTE_ELECTRONICS_COMPANY)
        changed_company["ui_overrides"] = {"voice": "Puck"}
        db2 = _mock_db_with_docs({
            "industry_templates/electronics": ELECTRONICS_TEMPLATE,
            "tenants/public/companies/ekaette-electronics": changed_company,
        })
        config2 = await resolve_registry_config(db2, "public", "ekaette-electronics")

        assert config1 is not None and config2 is not None
        assert config1.registry_version != config2.registry_version


# ═══ ResolvedRegistryConfig tests ═══


class TestResolvedRegistryConfig:
    """Test the ResolvedRegistryConfig dataclass."""

    def test_has_all_required_fields(self):
        from app.configs.registry_loader import ResolvedRegistryConfig

        config = ResolvedRegistryConfig(
            tenant_id="public",
            company_id="ekaette-electronics",
            industry_template_id="electronics",
            template_category="retail",
            template_label="Electronics & Gadgets",
            capabilities=["catalog_lookup"],
            voice="Aoede",
            theme={"accent": "oklch(74% 0.21 158)", "title": "Test"},
            greeting="Welcome!",
            connector_manifest={"crm": {"provider": "mock"}},
            registry_version="v1-abc",
        )
        assert config.tenant_id == "public"
        assert config.company_id == "ekaette-electronics"
        assert config.industry_template_id == "electronics"
        assert config.template_category == "retail"
        assert config.template_label == "Electronics & Gadgets"
        assert config.capabilities == ["catalog_lookup"]
        assert config.voice == "Aoede"
        assert config.theme["accent"] == "oklch(74% 0.21 158)"
        assert config.greeting == "Welcome!"
        assert config.connector_manifest == {"crm": {"provider": "mock"}}
        assert config.registry_version == "v1-abc"


# ═══ build_session_state_from_registry tests ═══


class TestBuildSessionStateFromRegistry:
    """Test that registry config produces both legacy and canonical session keys."""

    def test_returns_legacy_keys(self):
        from app.configs.registry_loader import (
            ResolvedRegistryConfig,
            build_session_state_from_registry,
        )

        config = ResolvedRegistryConfig(
            tenant_id="public",
            company_id="ekaette-electronics",
            industry_template_id="electronics",
            template_category="electronics",
            template_label="Electronics & Gadgets",
            capabilities=["catalog_lookup", "valuation_tradein"],
            voice="Aoede",
            theme={"accent": "oklch(74% 0.21 158)", "title": "Electronics Trade Desk"},
            greeting="Welcome!",
            connector_manifest={},
            registry_version="v1-test",
        )
        state = build_session_state_from_registry(config)

        # Legacy keys (backward compat with Phase 0)
        assert state["app:industry"] == "electronics"
        assert isinstance(state["app:industry_config"], dict)
        assert state["app:company_id"] == "ekaette-electronics"
        assert state["app:voice"] == "Aoede"
        assert state["app:greeting"] == "Welcome!"
        assert state["app:industry_config"]["name"] == "Electronics & Gadgets"

    def test_returns_canonical_keys(self):
        from app.configs.registry_loader import (
            ResolvedRegistryConfig,
            build_session_state_from_registry,
        )

        config = ResolvedRegistryConfig(
            tenant_id="public",
            company_id="ekaette-electronics",
            industry_template_id="electronics",
            template_category="electronics",
            template_label="Electronics & Gadgets",
            capabilities=["catalog_lookup", "valuation_tradein"],
            voice="Aoede",
            theme={"accent": "oklch(74% 0.21 158)", "title": "Electronics Trade Desk"},
            greeting="Welcome!",
            connector_manifest={"crm": {"provider": "mock"}},
            registry_version="v1-test",
        )
        state = build_session_state_from_registry(config)

        # Canonical keys (new in Phase 1)
        assert state["app:tenant_id"] == "public"
        assert state["app:industry_template_id"] == "electronics"
        assert state["app:capabilities"] == ["catalog_lookup", "valuation_tradein"]
        assert state["app:enabled_agents"] == []
        assert state["app:ui_theme"]["accent"] == "oklch(74% 0.21 158)"
        assert state["app:connector_manifest"] == {"crm": {"provider": "mock"}}
        assert state["app:registry_version"] == "v1-test"

    def test_persists_enabled_agents_when_present(self):
        from app.configs.registry_loader import (
            ResolvedRegistryConfig,
            build_session_state_from_registry,
        )

        config = ResolvedRegistryConfig(
            tenant_id="public",
            company_id="acme-hotel",
            industry_template_id="hotel",
            template_category="hospitality",
            template_label="Hotels & Hospitality",
            capabilities=["booking_reservations", "policy_qa"],
            voice="Puck",
            theme={"accent": "oklch(78% 0.15 55)", "title": "Hospitality Concierge"},
            greeting="Welcome to our hotel!",
            connector_manifest={},
            registry_version="v1-test",
            enabled_agents=["booking_agent", "support_agent"],
        )
        state = build_session_state_from_registry(config)

        assert state["app:enabled_agents"] == ["booking_agent", "support_agent"]

    def test_industry_config_has_expected_shape(self):
        """The legacy app:industry_config should mirror LOCAL_INDUSTRY_CONFIGS shape."""
        from app.configs.registry_loader import (
            ResolvedRegistryConfig,
            build_session_state_from_registry,
        )

        config = ResolvedRegistryConfig(
            tenant_id="public",
            company_id="ekaette-electronics",
            industry_template_id="electronics",
            template_category="electronics",
            template_label="Electronics & Gadgets",
            capabilities=["catalog_lookup"],
            voice="Aoede",
            theme={"title": "Electronics Trade Desk"},
            greeting="Welcome!",
            connector_manifest={},
            registry_version="v1-test",
        )
        state = build_session_state_from_registry(config)
        ic = state["app:industry_config"]

        # Must have name, voice, greeting — matching LOCAL_INDUSTRY_CONFIGS shape
        assert "name" in ic
        assert "voice" in ic
        assert "greeting" in ic

    def test_legacy_alias_uses_template_category_not_full_template_id(self):
        """Future templates may be more specific than the legacy industry alias."""
        from app.configs.registry_loader import (
            ResolvedRegistryConfig,
            build_session_state_from_registry,
        )

        config = ResolvedRegistryConfig(
            tenant_id="public",
            company_id="airline-1",
            industry_template_id="aviation-support",
            template_category="aviation",
            template_label="Aviation Customer Support",
            capabilities=["policy_qa", "flight_status_lookup"],
            voice="Puck",
            theme={"title": "Aviation Desk"},
            greeting="Welcome aboard.",
            connector_manifest={},
            registry_version="v1-test",
        )
        state = build_session_state_from_registry(config)

        assert state["app:industry"] == "aviation"
        assert state["app:industry_template_id"] == "aviation-support"


# ═══ Compatibility: load_industry_config fallback behavior ═══


class TestIndustryLoaderCompatibility:
    """Verify industry_loader still works identically when registry is absent."""

    @pytest.mark.asyncio
    async def test_fallback_to_local_configs_when_no_registry(self):
        """When Firestore has no industry_templates collection, use LOCAL_INDUSTRY_CONFIGS."""
        from app.configs.industry_loader import load_industry_config

        # db=None triggers local fallback
        config = await load_industry_config(None, "electronics")
        assert config["name"] == "Electronics & Gadgets"
        assert config["voice"] == "Aoede"

    @pytest.mark.asyncio
    async def test_unknown_industry_gets_default(self):
        from app.configs.industry_loader import load_industry_config

        config = await load_industry_config(None, "telecom")
        assert config["name"] == "General"
        assert config["voice"] == "Aoede"
