"""Phase 5 — Provisioning CLI + Data Migration tests (TDD Red).

Tests for:
- app/configs/registry_schema.py (shared schema validation)
- scripts/registry.py (provisioning CLI subcommands)
- scripts/migrate_to_tenant_scoped.py (one-time data migration)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══ Helpers ═══


def _mock_firestore_doc(data: dict[str, Any] | None, doc_id: str = "") -> MagicMock:
    """Create a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.exists = data is not None
    doc.id = doc_id
    doc.to_dict = MagicMock(return_value=data if data else {})
    return doc


class FakeFirestoreDB:
    """In-memory Firestore mock that supports set/get/stream on nested paths.

    Supports paths like:
      db.collection("X").document("Y").set(data)
      db.collection("X").document("Y").get()
      db.collection("X").document("Y").collection("Z").document("W").set(data)
      db.collection("X").stream()
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def _resolve_path(self, *parts: str) -> str:
        return "/".join(parts)

    def collection(self, name: str) -> "_FakeCollection":
        return _FakeCollection(self, name)

    def set_doc(self, path: str, data: dict[str, Any]) -> None:
        self._store[path] = dict(data)

    def get_doc(self, path: str) -> dict[str, Any] | None:
        return self._store.get(path)

    def list_docs(self, collection_path: str) -> list[tuple[str, dict[str, Any]]]:
        """Return (doc_id, data) for all docs under a collection path."""
        prefix = collection_path + "/"
        results = []
        for key, data in self._store.items():
            if key.startswith(prefix):
                remainder = key[len(prefix):]
                # Only direct children (no nested subcollections)
                if "/" not in remainder:
                    results.append((remainder, data))
        return results


class _FakeCollection:
    def __init__(self, db: FakeFirestoreDB, path: str) -> None:
        self._db = db
        self._path = path

    def document(self, doc_id: str) -> "_FakeDocRef":
        return _FakeDocRef(self._db, f"{self._path}/{doc_id}")

    def stream(self) -> list[MagicMock]:
        docs = self._db.list_docs(self._path)
        result = []
        for doc_id, data in docs:
            mock_doc = _mock_firestore_doc(data, doc_id)
            result.append(mock_doc)
        return result

    def where(self, field: str, op: str, value: Any) -> "_FakeQuery":
        return _FakeQuery(self._db, self._path, field, op, value)

    def limit(self, n: int) -> "_FakeCollection":
        return self  # simplification for tests


class _FakeQuery:
    def __init__(self, db: FakeFirestoreDB, path: str, field: str, op: str, value: Any) -> None:
        self._db = db
        self._path = path
        self._field = field
        self._op = op
        self._value = value

    def limit(self, n: int) -> "_FakeQuery":
        return self

    def stream(self) -> list[MagicMock]:
        docs = self._db.list_docs(self._path)
        result = []
        for doc_id, data in docs:
            if self._op == "==" and data.get(self._field) == self._value:
                result.append(_mock_firestore_doc(data, doc_id))
        return result


class _FakeDocRef:
    def __init__(self, db: FakeFirestoreDB, path: str) -> None:
        self._db = db
        self._path = path

    def set(self, data: dict[str, Any]) -> None:
        self._db.set_doc(self._path, data)

    def get(self) -> MagicMock:
        data = self._db.get_doc(self._path)
        doc_id = self._path.rsplit("/", 1)[-1]
        return _mock_firestore_doc(data, doc_id)

    def collection(self, name: str) -> "_FakeCollection":
        return _FakeCollection(self._db, f"{self._path}/{name}")


# ═══ Test Data ═══


ELECTRONICS_TEMPLATE = {
    "id": "electronics",
    "label": "Electronics & Gadgets",
    "category": "retail",
    "description": "Trade-ins, valuation, negotiation, pickup booking.",
    "default_voice": "Aoede",
    "greeting_policy": "Welcome! I can help you with device trade-ins.",
    "theme": {
        "accent": "oklch(74% 0.21 158)",
        "accentSoft": "oklch(74% 0.21 158 / 0.15)",
        "title": "Electronics Trade Desk",
        "hint": "Inspect. Value. Negotiate. Book pickup.",
    },
    "capabilities": ["catalog_lookup", "valuation_tradein", "booking_reservations"],
    "enabled_agents": ["vision_agent", "valuation_agent", "booking_agent"],
    "connectors_supported": ["crm"],
    "status": "active",
}

HOTEL_TEMPLATE = {
    "id": "hotel",
    "label": "Hotels & Hospitality",
    "category": "hospitality",
    "description": "Reservations, room search, and guest support.",
    "default_voice": "Puck",
    "greeting_policy": "Good day! Welcome to our hotel.",
    "theme": {
        "accent": "oklch(78% 0.15 55)",
        "accentSoft": "oklch(78% 0.15 55 / 0.15)",
        "title": "Hospitality Concierge",
        "hint": "Real-time booking and guest support voice assistant.",
    },
    "capabilities": ["booking_reservations", "policy_qa"],
    "enabled_agents": ["booking_agent", "support_agent"],
    "connectors_supported": ["pms"],
    "status": "active",
}

EKAETTE_ELECTRONICS_COMPANY = {
    "company_id": "ekaette-electronics",
    "tenant_id": "public",
    "industry_template_id": "electronics",
    "display_name": "Ekaette Devices Hub",
    "overview": "Trade-in focused electronics store.",
    "facts": {"primary_location": "Lagos - Ikeja"},
    "links": [],
    "connectors": {"crm": {"provider": "mock"}},
    "capability_overrides": {},
    "ui_overrides": {},
    "status": "active",
}


# ═══ 1. Schema Validation ═══


class TestRegistrySchemaValidation:
    """Tests for app/configs/registry_schema.py — shared schema validation."""

    def test_validate_template_schema_accepts_valid(self):
        """seed-templates validates template schema before writing."""
        from app.configs.registry_schema import validate_template

        errors = validate_template(ELECTRONICS_TEMPLATE)
        assert errors == []

    def test_validate_template_schema_rejects_missing_required_fields(self):
        """Template must have id, label, category, capabilities, status."""
        from app.configs.registry_schema import validate_template

        errors = validate_template({"description": "incomplete"})
        assert len(errors) > 0
        # Must flag missing id, label, category, capabilities
        error_text = " ".join(errors)
        assert "id" in error_text
        assert "label" in error_text
        assert "category" in error_text
        assert "capabilities" in error_text
        assert "status" in error_text
        assert "theme" in error_text

    def test_validate_company_enforces_required_fields(self):
        """provision-company enforces tenant_id, company_id, industry_template_id."""
        from app.configs.registry_schema import validate_company

        errors = validate_company({"display_name": "Incomplete Corp"})
        assert len(errors) > 0
        error_text = " ".join(errors)
        assert "company_id" in error_text
        assert "tenant_id" in error_text
        assert "industry_template_id" in error_text

    def test_validate_company_accepts_valid(self):
        """Valid company doc passes validation."""
        from app.configs.registry_schema import validate_company

        errors = validate_company(EKAETTE_ELECTRONICS_COMPANY)
        assert errors == []

    def test_validate_company_capability_overrides_against_template(self):
        """Capability overrides must reference capabilities the template knows about."""
        from app.configs.registry_schema import validate_capability_overrides

        template_capabilities = ["catalog_lookup", "valuation_tradein", "booking_reservations"]
        overrides = {
            "add": ["custom_tool"],  # OK — adding new is always valid
            "remove": ["nonexistent_capability"],  # BAD — not in template
        }
        errors = validate_capability_overrides(overrides, template_capabilities)
        assert len(errors) > 0
        assert "nonexistent_capability" in " ".join(errors)

    def test_validate_knowledge_entry_normalizes_required_fields(self):
        """Knowledge entries require id, title, text, tags."""
        from app.configs.registry_schema import validate_knowledge_entry

        errors = validate_knowledge_entry({"text": "only text"})
        assert len(errors) > 0
        error_text = " ".join(errors)
        assert "id" in error_text
        assert "title" in error_text
        assert "tags" in error_text

    def test_validate_knowledge_entry_accepts_valid(self):
        """Valid knowledge entry passes."""
        from app.configs.registry_schema import validate_knowledge_entry

        errors = validate_knowledge_entry({
            "id": "kb-test",
            "title": "Test",
            "text": "Some content",
            "tags": ["test"],
        })
        assert errors == []

    def test_validate_theme_rejects_invalid_shape(self):
        """Theme must be a dict with at least accent and title string keys."""
        from app.configs.registry_schema import validate_theme

        errors = validate_theme("not-a-dict")
        assert len(errors) > 0

        errors = validate_theme({"accent": 123, "title": None})
        assert len(errors) > 0

    def test_validate_theme_requires_accent_and_title(self):
        """Theme must include non-empty accent and title."""
        from app.configs.registry_schema import validate_theme

        errors = validate_theme({})
        error_text = " ".join(errors)
        assert "theme.accent" in error_text
        assert "theme.title" in error_text


# ═══ 2. Provisioning CLI ═══


class TestSeedTemplates:
    """Test seed-templates subcommand."""

    def test_seed_templates_writes_valid_templates(self):
        """seed-templates writes validated templates to industry_templates/{id}."""
        from scripts.registry import seed_templates

        db = FakeFirestoreDB()
        templates = [ELECTRONICS_TEMPLATE, HOTEL_TEMPLATE]

        result = seed_templates(db, templates)

        assert result["written"] == 2
        assert result["errors"] == []
        # Verify docs written to Firestore
        elec = db.get_doc("industry_templates/electronics")
        assert elec is not None
        assert elec["label"] == "Electronics & Gadgets"

    def test_seed_templates_rejects_invalid_templates(self):
        """seed-templates skips invalid templates and reports errors."""
        from scripts.registry import seed_templates

        db = FakeFirestoreDB()
        invalid_template = {"description": "no id or label"}

        result = seed_templates(db, [invalid_template, ELECTRONICS_TEMPLATE])

        assert result["written"] == 1
        assert len(result["errors"]) > 0


class TestProvisionCompany:
    """Test provision-company subcommand."""

    def test_provision_company_writes_to_tenant_path(self):
        """provision-company writes to tenants/{tenant}/companies/{company}."""
        from scripts.registry import provision_company

        db = FakeFirestoreDB()
        # Pre-seed the template so validation can check it
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)

        result = provision_company(db, EKAETTE_ELECTRONICS_COMPANY)

        assert result["success"] is True
        company = db.get_doc("tenants/public/companies/ekaette-electronics")
        assert company is not None
        assert company["display_name"] == "Ekaette Devices Hub"

    def test_provision_company_rejects_missing_required_fields(self):
        """provision-company rejects companies missing required fields."""
        from scripts.registry import provision_company

        db = FakeFirestoreDB()
        result = provision_company(db, {"display_name": "Bad Company"})

        assert result["success"] is False
        assert len(result["errors"]) > 0

    def test_provision_company_applies_defaults_for_minimal_payload(self):
        """provision-company writes normalized defaults even when caller passes only required fields."""
        from scripts.registry import provision_company

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)

        result = provision_company(db, {
            "company_id": "minimal-company",
            "tenant_id": "public",
            "industry_template_id": "electronics",
        })

        assert result["success"] is True
        written = db.get_doc("tenants/public/companies/minimal-company")
        assert written is not None
        assert written["display_name"] == "minimal-company"
        assert written["status"] == "active"
        assert written["connectors"] == {}


class TestImportKnowledge:
    """Test import-knowledge subcommand."""

    def test_import_knowledge_writes_entries(self):
        """import-knowledge writes normalized entries under tenant/company/knowledge."""
        from scripts.registry import import_knowledge

        db = FakeFirestoreDB()
        entries = [
            {"id": "kb-1", "title": "Hours", "text": "Open 9-5.", "tags": ["support"]},
            {"id": "kb-2", "title": "Returns", "text": "14 day returns.", "tags": ["policy"]},
        ]

        result = import_knowledge(
            db,
            tenant_id="public",
            company_id="ekaette-electronics",
            entries=entries,
        )

        assert result["written"] == 2
        assert result["errors"] == []
        kb1 = db.get_doc("tenants/public/companies/ekaette-electronics/knowledge/kb-1")
        assert kb1 is not None
        assert kb1["title"] == "Hours"

    def test_import_knowledge_rejects_invalid_entries(self):
        """import-knowledge skips entries missing required fields."""
        from scripts.registry import import_knowledge

        db = FakeFirestoreDB()
        entries = [
            {"text": "no id or title"},  # invalid
            {"id": "kb-ok", "title": "Valid", "text": "Content.", "tags": ["ok"]},
        ]

        result = import_knowledge(
            db,
            tenant_id="public",
            company_id="ekaette-electronics",
            entries=entries,
        )

        assert result["written"] == 1
        assert len(result["errors"]) > 0


class TestValidateCommand:
    """Test validate subcommand."""

    def test_validate_catches_template_company_mismatch(self):
        """validate detects when a company references a non-existent template."""
        from scripts.registry import validate_registry

        db = FakeFirestoreDB()
        # Company references 'electronics' template, but no template seeded
        db.set_doc(
            "tenants/public/companies/ekaette-electronics",
            EKAETTE_ELECTRONICS_COMPANY,
        )

        result = validate_registry(db, tenant_id="public")

        assert len(result["errors"]) > 0
        assert "electronics" in " ".join(result["errors"])

    def test_validate_passes_for_consistent_data(self):
        """validate passes when templates and companies are consistent."""
        from scripts.registry import validate_registry

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)
        db.set_doc(
            "tenants/public/companies/ekaette-electronics",
            EKAETTE_ELECTRONICS_COMPANY,
        )

        result = validate_registry(db, tenant_id="public")
        assert result["errors"] == []

    def test_validate_catches_unsupported_connectors(self):
        """validate flags company connectors not in template's connectors_supported."""
        from scripts.registry import validate_registry

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)

        bad_company = dict(EKAETTE_ELECTRONICS_COMPANY)
        bad_company["connectors"] = {"erp": {"provider": "sap"}}  # Not in connectors_supported

        db.set_doc("tenants/public/companies/ekaette-electronics", bad_company)

        result = validate_registry(db, tenant_id="public")
        assert len(result["errors"]) > 0
        assert "erp" in " ".join(result["errors"])

    def test_validate_catches_malformed_template_schema(self):
        """validate also runs template schema validation, not only cross-reference checks."""
        from scripts.registry import validate_registry

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/bad-template", {
            "id": "bad-template",
            "label": "Bad",
            "category": "retail",
            "capabilities": [],
            # missing status/theme
        })

        result = validate_registry(db, tenant_id="public")
        error_text = " ".join(result["errors"])
        assert "template 'bad-template'" in error_text
        assert "status" in error_text
        assert "theme" in error_text

    def test_validate_catches_malformed_company_schema(self):
        """validate runs company schema validation on tenant-scoped company docs."""
        from scripts.registry import validate_registry

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)
        db.set_doc("tenants/public/companies/bad-company", {
            "tenant_id": "public",
            "industry_template_id": "electronics",
            # missing company_id
        })

        result = validate_registry(db, tenant_id="public")
        error_text = " ".join(result["errors"])
        assert "company 'bad-company'" in error_text
        assert "company_id" in error_text


class TestSmokeCommand:
    """Test smoke subcommand."""

    @pytest.mark.asyncio
    async def test_smoke_resolves_config_end_to_end(self):
        """smoke runs resolve config → verify capabilities → verify voice → verify state keys."""
        from scripts.registry import smoke_test

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)
        db.set_doc(
            "tenants/public/companies/ekaette-electronics",
            EKAETTE_ELECTRONICS_COMPANY,
        )

        result = await smoke_test(
            db,
            tenant_id="public",
            company_id="ekaette-electronics",
        )

        assert result["success"] is True
        assert "capabilities" in result
        assert "voice" in result
        assert "state_keys" in result
        assert "app:industry" in result["state_keys"]
        assert "app:tenant_id" in result["state_keys"]


# ═══ 3. Data Migration ═══


class TestMigrateTenantScoped:
    """Tests for scripts/migrate_to_tenant_scoped.py — one-time migration."""

    def test_migrates_company_profiles(self):
        """Reads company_profiles/{id} → writes tenants/{tenant}/companies/{id}."""
        from scripts.migrate_to_tenant_scoped import migrate_company_profiles

        db = FakeFirestoreDB()
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
            "overview": "Trade-in focused electronics store.",
            "facts": {"primary_location": "Lagos - Ikeja"},
            "links": [],
            "system_connectors": {"crm": {"provider": "mock"}},
        })

        result = migrate_company_profiles(db, tenant_id="public")

        assert result["migrated"] == 1
        company = db.get_doc("tenants/public/companies/ekaette-electronics")
        assert company is not None
        assert company["display_name"] == "Ekaette Devices Hub"
        assert company["industry_template_id"] == "electronics"
        assert company["tenant_id"] == "public"

    def test_migrates_company_knowledge(self):
        """Reads company_knowledge (flat) → writes tenants/{t}/companies/{c}/knowledge/{id}."""
        from scripts.migrate_to_tenant_scoped import migrate_company_knowledge

        db = FakeFirestoreDB()
        db.set_doc("company_knowledge/kb-elec-hours", {
            "company_id": "ekaette-electronics",
            "title": "Support hours",
            "text": "Customer support 9 AM to 7 PM.",
            "tags": ["support"],
            "source": "seed",
        })

        result = migrate_company_knowledge(db, tenant_id="public")

        assert result["migrated"] == 1
        kb = db.get_doc("tenants/public/companies/ekaette-electronics/knowledge/kb-elec-hours")
        assert kb is not None
        assert kb["title"] == "Support hours"

    def test_migrates_products_to_catalog_items(self):
        """Reads products (flat) → writes tenants/{t}/companies/{c}/catalog_items/{id}."""
        from scripts.migrate_to_tenant_scoped import migrate_products

        db = FakeFirestoreDB()
        # Need a company mapping to know which company owns the product
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
        })
        db.set_doc("products/prod-iphone-15-pro", {
            "name": "iPhone 15 Pro",
            "price": 850000,
            "currency": "NGN",
            "category": "smartphones",
            "brand": "Apple",
            "in_stock": True,
        })

        result = migrate_products(
            db,
            tenant_id="public",
            company_id="ekaette-electronics",
        )

        assert result["migrated"] == 1
        product = db.get_doc(
            "tenants/public/companies/ekaette-electronics/catalog_items/prod-iphone-15-pro"
        )
        assert product is not None
        assert product["name"] == "iPhone 15 Pro"

    def test_migrates_booking_slots(self):
        """Reads booking_slots (flat) → writes tenants/{t}/companies/{c}/booking_slots/{id}."""
        from scripts.migrate_to_tenant_scoped import migrate_booking_slots

        db = FakeFirestoreDB()
        db.set_doc("booking_slots/slot-2026-03-01-10-ikeja", {
            "date": "2026-03-01",
            "time": "10:00",
            "location": "Lagos - Ikeja",
            "available": True,
        })

        result = migrate_booking_slots(
            db,
            tenant_id="public",
            company_id="ekaette-electronics",
        )

        assert result["migrated"] == 1
        slot = db.get_doc(
            "tenants/public/companies/ekaette-electronics/booking_slots/slot-2026-03-01-10-ikeja"
        )
        assert slot is not None
        assert slot["location"] == "Lagos - Ikeja"

    def test_migration_is_idempotent(self):
        """Running migration twice doesn't create duplicates."""
        from scripts.migrate_to_tenant_scoped import migrate_company_profiles

        db = FakeFirestoreDB()
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
            "overview": "Trade-in focused electronics store.",
            "facts": {},
            "links": [],
            "system_connectors": {},
        })

        result1 = migrate_company_profiles(db, tenant_id="public")
        result2 = migrate_company_profiles(db, tenant_id="public")

        assert result1["migrated"] == 1
        assert result2["migrated"] == 0  # Already exists, skip
        assert result2.get("skipped", 0) == 1

    def test_migration_validates_tenant_company_mapping(self):
        """Migration validates source data before writing."""
        from scripts.migrate_to_tenant_scoped import migrate_company_profiles

        db = FakeFirestoreDB()
        # Company without 'industry' field — can't determine template
        db.set_doc("company_profiles/bad-company", {
            "name": "Bad Company",
            "overview": "No industry.",
        })

        result = migrate_company_profiles(db, tenant_id="public")
        assert result["migrated"] == 0
        assert len(result["errors"]) > 0

    def test_migration_dry_run_does_not_write_company_profiles(self):
        """--dry-run mode must not mutate Firestore when migrating company profiles."""
        from scripts.migrate_to_tenant_scoped import migrate_company_profiles

        db = FakeFirestoreDB()
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
            "overview": "Trade-in focused electronics store.",
            "facts": {},
            "links": [],
            "system_connectors": {},
        })

        result = migrate_company_profiles(db, tenant_id="public", dry_run=True)

        assert result["migrated"] == 1
        assert db.get_doc("tenants/public/companies/ekaette-electronics") is None

    def test_migration_dry_run_does_not_write_products(self):
        """--dry-run mode must not write migrated catalog items."""
        from scripts.migrate_to_tenant_scoped import migrate_products

        db = FakeFirestoreDB()
        db.set_doc("products/prod-iphone-15-pro", {
            "name": "iPhone 15 Pro",
            "price": 850000,
        })

        result = migrate_products(
            db,
            tenant_id="public",
            company_id="ekaette-electronics",
            dry_run=True,
        )

        assert result["migrated"] == 1
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/catalog_items/prod-iphone-15-pro"
        ) is None
