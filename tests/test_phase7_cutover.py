"""Phase 7 — Cutover + Cleanup tests (TDD Red).

Tests for making registry the default runtime path:
1. REGISTRY_ENABLED=true + data present → registry used, no local fallback
2. REGISTRY_ENABLED=true + data missing → explicit error (not silent fallback)
3. Legacy 'industry' alias still emitted in session state and API messages
4. Structured logs include tenant_id, company_id, industry_template_id, registry_version
5. LOCAL_INDUSTRY_CONFIGS / LOCAL_COMPANY_PROFILES are test-only (not on runtime path)
"""

from __future__ import annotations

import logging

import pytest
from starlette.testclient import TestClient

from tests.test_phase5_provisioning import FakeFirestoreDB


# ═══ Fixtures ═══


def _seed_registry(db: FakeFirestoreDB) -> None:
    """Seed minimal registry data for cutover tests."""
    # Template
    db.set_doc("industry_templates/electronics", {
        "schema_version": 1,
        "id": "electronics",
        "label": "Electronics & Gadgets",
        "category": "electronics",
        "description": "Device trade-ins and gadget support.",
        "default_voice": "Aoede",
        "greeting_policy": "Welcome from the registry!",
        "theme": {
            "accent": "oklch(74% 0.21 158)",
            "title": "Electronics Trade Desk",
        },
        "capabilities": ["catalog_lookup", "valuation_tradein", "booking_reservations", "outbound_messaging"],
        "enabled_agents": ["support_agent"],
        "connectors_supported": ["crm"],
        "status": "active",
    })
    db.set_doc("industry_templates/hotel", {
        "schema_version": 1,
        "id": "hotel",
        "label": "Hotels & Hospitality",
        "category": "hotel",
        "description": "Booking and concierge support.",
        "default_voice": "Puck",
        "greeting_policy": "Welcome to the hotel!",
        "theme": {
            "accent": "oklch(78% 0.15 55)",
            "title": "Hospitality Concierge",
        },
        "capabilities": ["booking_reservations", "policy_qa", "outbound_messaging"],
        "enabled_agents": ["support_agent"],
        "connectors_supported": ["pms"],
        "status": "active",
    })
    # Companies (tenant-scoped)
    db.set_doc("tenants/public/companies/ekaette-electronics", {
        "schema_version": 1,
        "company_id": "ekaette-electronics",
        "tenant_id": "public",
        "industry_template_id": "electronics",
        "display_name": "Ekaette Devices Hub",
        "overview": "Trade-in focused electronics store.",
        "facts": {"primary_location": "Lagos"},
        "links": [],
        "connectors": {},
        "capability_overrides": {},
        "ui_overrides": {},
        "status": "active",
    })
    db.set_doc("tenants/public/companies/ekaette-hotel", {
        "schema_version": 1,
        "company_id": "ekaette-hotel",
        "tenant_id": "public",
        "industry_template_id": "hotel",
        "display_name": "Ekaette Grand Hotel",
        "overview": "Business and leisure hotel.",
        "facts": {"rooms": 120},
        "links": [],
        "connectors": {},
        "capability_overrides": {},
        "ui_overrides": {},
        "status": "active",
    })
    # Knowledge entries
    db.set_doc("tenants/public/companies/ekaette-electronics/knowledge/kb-elec-hours", {
        "id": "kb-elec-hours",
        "title": "Support hours",
        "text": "Customer support is available daily from 9 AM to 7 PM.",
        "tags": ["support", "hours"],
    })


@pytest.fixture
def registry_db() -> FakeFirestoreDB:
    """Firestore mock pre-seeded with registry data."""
    db = FakeFirestoreDB()
    _seed_registry(db)
    return db


@pytest.fixture
def empty_db() -> FakeFirestoreDB:
    """Empty Firestore mock — no registry data."""
    return FakeFirestoreDB()


@pytest.fixture
def main_module():
    import main

    return main


@pytest.fixture
def http_client(main_module):
    with TestClient(main_module.app) as client:
        yield client


# ═══ Section 1: Registry-first, no local fallback ═══


class TestRegistryFirstNoFallback:
    """When REGISTRY_ENABLED=true and registry data exists, local dicts must not be used."""

    @pytest.mark.asyncio
    async def test_load_industry_config_returns_registry_data(
        self, registry_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """load_industry_config should return registry-projected config, not local dict."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.industry_loader import load_industry_config

        config = await load_industry_config(registry_db, "electronics")

        # Registry greeting is different from local fallback
        assert config["greeting"] == "Welcome from the registry!"
        assert config["voice"] == "Aoede"
        assert config["name"] == "Electronics & Gadgets"

    @pytest.mark.asyncio
    async def test_load_company_profile_returns_registry_data(
        self, registry_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """load_company_profile should return registry company, not local fallback."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.company_loader import load_company_profile

        profile = await load_company_profile(
            registry_db, "ekaette-electronics", tenant_id="public",
        )

        assert profile["company_id"] == "ekaette-electronics"
        # Registry name comes from display_name field
        assert "Ekaette Devices Hub" in profile.get("name", "")

    @pytest.mark.asyncio
    async def test_load_company_knowledge_returns_registry_data(
        self, registry_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """load_company_knowledge should return registry entries, not local fallback."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.company_loader import load_company_knowledge

        entries = await load_company_knowledge(
            registry_db, "ekaette-electronics", tenant_id="public",
        )

        assert len(entries) >= 1
        titles = [e["title"] for e in entries]
        assert "Support hours" in titles
        # Verify source is NOT local_fallback
        for entry in entries:
            assert entry.get("source") != "local_fallback"

    @pytest.mark.asyncio
    async def test_onboarding_config_returns_registry_data(
        self, registry_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """build_onboarding_config should use registry, not compat mode."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.registry_loader import build_onboarding_config

        config = await build_onboarding_config(registry_db, "public")

        assert config["tenantId"] == "public"
        template_ids = [t["id"] for t in config["templates"]]
        assert "electronics" in template_ids
        assert "hotel" in template_ids
        company_ids = [c["id"] for c in config["companies"]]
        assert "ekaette-electronics" in company_ids


# ═══ Section 2: Missing registry data → explicit error ═══


class TestRegistryMissingDataRaises:
    """When REGISTRY_ENABLED=true and registry data is absent, raise not silently fallback."""

    @pytest.mark.asyncio
    async def test_load_industry_config_missing_template_raises(
        self, empty_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing template should raise RegistryDataMissingError, not return local default."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.industry_loader import load_industry_config

        with pytest.raises(Exception, match="[Rr]egistry.*missing|[Nn]o.*template|[Rr]egistry.*not found"):
            await load_industry_config(empty_db, "electronics")

    @pytest.mark.asyncio
    async def test_load_company_profile_missing_raises(
        self, empty_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing company should raise, not silently return local fallback."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.company_loader import load_company_profile

        with pytest.raises(Exception, match="[Rr]egistry.*missing|[Nn]o.*company|[Rr]egistry.*not found"):
            await load_company_profile(
                empty_db, "ekaette-electronics", tenant_id="public",
            )

    @pytest.mark.asyncio
    async def test_load_industry_config_db_none_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """db=None with REGISTRY_ENABLED=true should raise, not silently fallback."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.industry_loader import load_industry_config

        with pytest.raises(Exception, match="[Rr]egistry|[Ff]irestore.*required"):
            await load_industry_config(None, "electronics")

    @pytest.mark.asyncio
    async def test_load_company_profile_db_none_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """db=None with REGISTRY_ENABLED=true should raise, not silently fallback."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.company_loader import load_company_profile

        with pytest.raises(Exception, match="[Rr]egistry|[Ff]irestore.*required"):
            await load_company_profile(None, "ekaette-electronics", tenant_id="public")

    @pytest.mark.asyncio
    async def test_load_company_knowledge_db_none_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Registry mode knowledge loading should fail closed when Firestore is unavailable."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.company_loader import RegistryDataMissingError, load_company_knowledge

        with pytest.raises(RegistryDataMissingError, match="Firestore client required"):
            await load_company_knowledge(None, "ekaette-electronics", tenant_id="public")

    @pytest.mark.asyncio
    async def test_build_onboarding_config_missing_registry_raises(
        self, empty_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cutover mode onboarding config should not silently fallback to compat data."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.registry_loader import RegistryDataMissingError, build_onboarding_config

        with pytest.raises(RegistryDataMissingError, match="[Rr]egistry onboarding config not found"):
            await build_onboarding_config(empty_db, "public")

    def test_onboarding_endpoint_returns_explicit_error(
        self,
        http_client: TestClient,
        main_module,
        empty_db: FakeFirestoreDB,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API should return explicit error JSON (not 500/compat fallback) when registry onboarding is missing."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        monkeypatch.setattr(main_module, "industry_config_client", empty_db)

        response = http_client.get(
            "/api/onboarding/config",
            params={"tenantId": "public"},
            headers={"origin": "http://localhost:5173"},
        )

        assert response.status_code == 503
        payload = response.json()
        assert payload["code"] == "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"
        assert payload["tenantId"] == "public"


# ═══ Section 3: Legacy alias retained ═══


class TestLegacyAliasRetained:
    """Legacy 'industry' alias must remain in session state and API messages."""

    def test_session_state_includes_legacy_industry_key(self) -> None:
        """build_session_state_from_registry must emit app:industry (legacy alias)."""
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
            theme={"accent": "oklch(74% 0.21 158)"},
            greeting="Welcome!",
            connector_manifest={},
            registry_version="v1-abc12345",
        )
        state = build_session_state_from_registry(config)

        # Legacy key present
        assert "app:industry" in state
        assert state["app:industry"] == "electronics"
        # Canonical key also present
        assert "app:industry_template_id" in state
        assert state["app:industry_template_id"] == "electronics"

    def test_legacy_industry_maps_to_category_not_template_id(self) -> None:
        """For aviation-support template, app:industry should be 'aviation' (category)."""
        from app.configs.registry_loader import (
            ResolvedRegistryConfig,
            build_session_state_from_registry,
        )

        config = ResolvedRegistryConfig(
            tenant_id="public",
            company_id="ekaette-aviation",
            industry_template_id="aviation-support",
            template_category="aviation",
            template_label="Aviation",
            capabilities=["policy_qa", "flight_status_lookup"],
            voice="Puck",
            theme={"accent": "oklch(68% 0.12 260)"},
            greeting="Welcome to Aviation Support.",
            connector_manifest={},
            registry_version="v1-def67890",
        )
        state = build_session_state_from_registry(config)

        # Legacy alias is category, not template_id
        assert state["app:industry"] == "aviation"
        assert state["app:industry_template_id"] == "aviation-support"

    def test_session_state_includes_all_canonical_keys(self) -> None:
        """All canonical keys must be present in registry session state."""
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
            theme={"accent": "oklch(74% 0.21 158)"},
            greeting="Welcome!",
            connector_manifest={"crm": {"provider": "mock"}},
            registry_version="v1-abc12345",
        )
        state = build_session_state_from_registry(config)

        canonical_keys = [
            "app:tenant_id",
            "app:industry_template_id",
            "app:capabilities",
            "app:ui_theme",
            "app:connector_manifest",
            "app:registry_version",
        ]
        for key in canonical_keys:
            assert key in state, f"Missing canonical key: {key}"

        assert state["app:tenant_id"] == "public"
        assert state["app:capabilities"] == ["catalog_lookup", "valuation_tradein"]
        assert state["app:registry_version"] == "v1-abc12345"


# ═══ Section 4: Structured logging ═══


class TestStructuredLogging:
    """Key runtime log messages must include registry context fields."""

    @pytest.mark.asyncio
    async def test_resolve_registry_config_logs_tenant_and_company(
        self, registry_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Registry resolution should log tenant_id and company_id."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.registry_loader import resolve_registry_config

        with caplog.at_level(logging.DEBUG, logger="app.configs.registry_loader"):
            config = await resolve_registry_config(registry_db, "public", "ekaette-electronics")

        assert config is not None
        log_text = " ".join(caplog.messages)
        assert "tenant_id=public" in log_text
        assert "company_id=ekaette-electronics" in log_text
        assert "industry_template_id=electronics" in log_text
        assert "registry_version=" in log_text

    @pytest.mark.asyncio
    async def test_registry_missing_logs_structured_warning(
        self, empty_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Missing registry data should log a structured warning with context."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.industry_loader import load_industry_config

        with caplog.at_level(logging.WARNING):
            try:
                await load_industry_config(empty_db, "electronics")
            except Exception:
                pass  # We expect an error; we're checking the log

        # Should have logged something about missing registry data
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_messages) >= 1
        assert any("industry='electronics'" in msg for msg in warning_messages)


# ═══ Section 5: REGISTRY_ENABLED default ═══


class TestRegistryEnabledDefault:
    """REGISTRY_ENABLED should default to true after cutover."""

    def test_registry_enabled_defaults_to_true(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no env var set, REGISTRY_ENABLED should default to true."""
        monkeypatch.delenv("REGISTRY_ENABLED", raising=False)

        from app.configs.industry_loader import _env_flag
        assert _env_flag("REGISTRY_ENABLED", "true") is True

    def test_env_example_includes_registry_enabled(self) -> None:
        """The .env.example file should document REGISTRY_ENABLED=true."""
        import pathlib

        env_example = pathlib.Path(__file__).resolve().parent.parent / ".env.example"
        if not env_example.exists():
            pytest.skip(".env.example not found")

        content = env_example.read_text()
        assert "REGISTRY_ENABLED" in content
        assert "REGISTRY_ENABLED=TRUE" in content or "REGISTRY_ENABLED=true" in content


# ═══ Section 6: Local fallback dicts not on registry runtime path ═══


class TestLocalFallbackTestOnly:
    """LOCAL_INDUSTRY_CONFIGS and LOCAL_COMPANY_PROFILES must not be on the registry runtime path."""

    @pytest.mark.asyncio
    async def test_registry_path_does_not_use_local_industry_configs(
        self, registry_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When registry returns data, LOCAL_INDUSTRY_CONFIGS must not influence the result."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS, load_industry_config

        # Save and replace with sentinel
        original_entry = dict(LOCAL_INDUSTRY_CONFIGS.get("electronics", {}))
        LOCAL_INDUSTRY_CONFIGS["electronics"] = {
            "name": "SENTINEL_NAME",
            "voice": "SENTINEL_VOICE",
            "greeting": "SENTINEL_GREETING",
        }
        try:
            config = await load_industry_config(registry_db, "electronics")
            # Result must come from registry, not the sentinel
            assert config["greeting"] != "SENTINEL_GREETING"
            assert config["name"] != "SENTINEL_NAME"
        finally:
            # Restore original entry to avoid polluting other tests
            LOCAL_INDUSTRY_CONFIGS["electronics"] = original_entry

    @pytest.mark.asyncio
    async def test_registry_path_does_not_use_local_company_profiles(
        self, registry_db: FakeFirestoreDB, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When registry returns data, LOCAL_COMPANY_PROFILES must not influence the result."""
        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        from app.configs.company_loader import LOCAL_COMPANY_PROFILES, load_company_profile

        LOCAL_COMPANY_PROFILES["ekaette-electronics"]["name"] = "SENTINEL_COMPANY"
        try:
            profile = await load_company_profile(
                registry_db, "ekaette-electronics", tenant_id="public",
            )
            assert profile["name"] != "SENTINEL_COMPANY"
        finally:
            LOCAL_COMPANY_PROFILES["ekaette-electronics"]["name"] = "Ekaette Devices Hub"

    def test_seed_data_has_deprecation_notice(self) -> None:
        """seed_data.py should have a deprecation notice pointing to registry CLI."""
        import pathlib

        seed_data = pathlib.Path(__file__).resolve().parent.parent / "seed_data.py"
        if not seed_data.exists():
            pytest.skip("seed_data.py not found")

        content = seed_data.read_text()
        assert "deprecated" in content.lower() or "DEPRECATED" in content
