"""Phase 5 — Provisioning CLI + Data Migration tests (TDD Red).

Tests for:
- app/configs/registry_schema.py (shared schema validation)
- scripts/registry.py (provisioning CLI subcommands)
- scripts/migrate_to_tenant_scoped.py (one-time data migration)
"""

from __future__ import annotations

import json
import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest


# ═══ Helpers ═══


def _mock_firestore_doc(
    data: dict[str, Any] | None,
    doc_id: str = "",
    reference: Any = None,
) -> MagicMock:
    """Create a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.exists = data is not None
    doc.id = doc_id
    doc.to_dict = MagicMock(return_value=data if data else {})
    if reference is not None:
        doc.reference = reference
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
            ref = _FakeDocRef(self._db, f"{self._path}/{doc_id}")
            mock_doc = _mock_firestore_doc(data, doc_id, reference=ref)
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
                ref = _FakeDocRef(self._db, f"{self._path}/{doc_id}")
                result.append(_mock_firestore_doc(data, doc_id, reference=ref))
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

    def delete(self) -> None:
        self._db._store.pop(self._path, None)

    def collection(self, name: str) -> "_FakeCollection":
        return _FakeCollection(self._db, f"{self._path}/{name}")


def _install_fake_cli_modules(monkeypatch: pytest.MonkeyPatch, db: FakeFirestoreDB) -> None:
    """Install fake google.cloud.firestore and dotenv modules for CLI tests."""
    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")
    firestore_mod = types.ModuleType("google.cloud.firestore")
    dotenv_mod = types.ModuleType("dotenv")

    class _FakeClient:
        def __new__(cls, *args: Any, **kwargs: Any) -> FakeFirestoreDB:  # type: ignore[override]
            return db

    firestore_mod.Client = _FakeClient  # type: ignore[attr-defined]
    cloud_mod.firestore = firestore_mod  # type: ignore[attr-defined]
    google_mod.cloud = cloud_mod  # type: ignore[attr-defined]
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", firestore_mod)
    monkeypatch.setitem(sys.modules, "dotenv", dotenv_mod)


# ═══ Test Data ═══


ELECTRONICS_TEMPLATE = {
    "schema_version": 1,
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
    "schema_version": 1,
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
    "schema_version": 1,
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

    def test_validate_template_rejects_unsupported_schema_version(self):
        """Templates with unsupported schema_version fail validation."""
        from app.configs.registry_schema import validate_template

        invalid = dict(ELECTRONICS_TEMPLATE)
        invalid["schema_version"] = 99
        errors = validate_template(invalid)

        assert any("unsupported schema_version" in err for err in errors)

    def test_validate_company_rejects_missing_schema_version(self):
        """Companies must include schema_version."""
        from app.configs.registry_schema import validate_company

        invalid = dict(EKAETTE_ELECTRONICS_COMPANY)
        invalid.pop("schema_version", None)
        errors = validate_company(invalid)

        assert any("schema_version" in err for err in errors)

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
        assert result["operations"]["created"] == 2
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
        assert result["operations"]["failed"] == 1

    def test_seed_templates_reports_unchanged_on_idempotent_rerun(self):
        """Re-seeding identical template payloads should report unchanged, not rewrite."""
        from scripts.registry import seed_templates

        db = FakeFirestoreDB()
        first = seed_templates(db, [ELECTRONICS_TEMPLATE])
        second = seed_templates(db, [ELECTRONICS_TEMPLATE])

        assert first["operations"]["created"] == 1
        assert second["written"] == 0
        assert second["operations"]["unchanged"] == 1


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
        assert result["operation"] == "created"
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
        assert result["operation"] == "failed"

    def test_provision_company_applies_defaults_for_minimal_payload(self):
        """provision-company writes normalized defaults even when caller passes only required fields."""
        from scripts.registry import provision_company

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)

        result = provision_company(db, {
            "schema_version": 1,
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

    def test_provision_company_reports_unchanged_on_idempotent_rerun(self):
        """Provisioning the same normalized payload twice should report unchanged."""
        from scripts.registry import provision_company

        db = FakeFirestoreDB()
        db.set_doc("industry_templates/electronics", ELECTRONICS_TEMPLATE)

        first = provision_company(db, EKAETTE_ELECTRONICS_COMPANY)
        second = provision_company(db, EKAETTE_ELECTRONICS_COMPANY)

        assert first["success"] is True and first["operation"] == "created"
        assert second["success"] is True and second["operation"] == "unchanged"


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
        assert result["operations"]["created"] == 2
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
        assert result["operations"]["failed"] == 1

    def test_import_knowledge_reports_unchanged_on_idempotent_rerun(self):
        """Importing identical entries twice should report unchanged on the second run."""
        from scripts.registry import import_knowledge

        db = FakeFirestoreDB()
        entries = [{"id": "kb-1", "title": "Hours", "text": "Open 9-5.", "tags": ["support"]}]

        first = import_knowledge(db, tenant_id="public", company_id="ekaette-electronics", entries=entries)
        second = import_knowledge(db, tenant_id="public", company_id="ekaette-electronics", entries=entries)

        assert first["operations"]["created"] == 1
        assert second["written"] == 0
        assert second["operations"]["unchanged"] == 1


class TestImportProducts:
    """Test import-products functionality."""

    def test_import_products_writes_entries(self, product_factory):
        from scripts.registry import import_products

        db = FakeFirestoreDB()
        products = [product_factory(id="prod-phone", name="Phone")]
        result = import_products(
            db, tenant_id="public", company_id="ekaette-electronics", products=products,
        )

        assert result["written"] == 1
        assert result["errors"] == []
        assert result["operations"]["created"] == 1
        doc = db.get_doc("tenants/public/companies/ekaette-electronics/products/prod-phone")
        assert doc is not None
        assert doc["name"] == "Phone"

    def test_import_products_rejects_invalid(self, product_factory):
        from scripts.registry import import_products

        db = FakeFirestoreDB()
        products = [
            {"name": "no id"},  # invalid — missing id, price, currency, category, in_stock
            product_factory(id="prod-ok", name="OK"),
        ]
        result = import_products(
            db, tenant_id="public", company_id="ekaette-electronics", products=products,
        )

        assert result["written"] == 1
        assert len(result["errors"]) > 0
        assert result["operations"]["failed"] == 1

    def test_import_products_idempotent(self, product_factory):
        from scripts.registry import import_products

        db = FakeFirestoreDB()
        products = [product_factory(id="prod-1")]
        first = import_products(db, tenant_id="public", company_id="test", products=products)
        second = import_products(db, tenant_id="public", company_id="test", products=products)

        assert first["operations"]["created"] == 1
        assert second["operations"]["unchanged"] == 1
        assert second["written"] == 0

    def test_import_products_updates_changed_entries(self, product_factory):
        from scripts.registry import import_products

        db = FakeFirestoreDB()
        v1 = [product_factory(id="prod-1", price=100)]
        v2 = [product_factory(id="prod-1", price=200)]
        import_products(db, tenant_id="public", company_id="test", products=v1)
        result = import_products(db, tenant_id="public", company_id="test", products=v2)

        assert result["operations"]["updated"] == 1
        doc = db.get_doc("tenants/public/companies/test/products/prod-1")
        assert doc["price"] == 200


class TestImportBookingSlots:
    """Test import-booking-slots functionality."""

    def test_import_slots_writes_entries(self, booking_slot_factory):
        from scripts.registry import import_booking_slots

        db = FakeFirestoreDB()
        slots = [booking_slot_factory(id="slot-1")]
        result = import_booking_slots(
            db, tenant_id="public", company_id="ekaette-electronics", slots=slots,
        )

        assert result["written"] == 1
        assert result["errors"] == []
        doc = db.get_doc("tenants/public/companies/ekaette-electronics/booking_slots/slot-1")
        assert doc is not None

    def test_import_slots_rejects_invalid(self, booking_slot_factory):
        from scripts.registry import import_booking_slots

        db = FakeFirestoreDB()
        slots = [
            {"time": "10:00"},  # missing id, date, available
            booking_slot_factory(id="slot-ok"),
        ]
        result = import_booking_slots(
            db, tenant_id="public", company_id="test", slots=slots,
        )

        assert result["written"] == 1
        assert len(result["errors"]) > 0
        assert result["operations"]["failed"] == 1

    def test_import_slots_idempotent(self, booking_slot_factory):
        from scripts.registry import import_booking_slots

        db = FakeFirestoreDB()
        slots = [booking_slot_factory(id="slot-1")]
        first = import_booking_slots(db, tenant_id="public", company_id="test", slots=slots)
        second = import_booking_slots(db, tenant_id="public", company_id="test", slots=slots)

        assert first["operations"]["created"] == 1
        assert second["operations"]["unchanged"] == 1

    def test_import_slots_updates_changed_entries(self, booking_slot_factory):
        from scripts.registry import import_booking_slots

        db = FakeFirestoreDB()
        v1 = [booking_slot_factory(id="slot-1", available=True)]
        v2 = [booking_slot_factory(id="slot-1", available=False)]
        import_booking_slots(db, tenant_id="public", company_id="test", slots=v1)
        result = import_booking_slots(db, tenant_id="public", company_id="test", slots=v2)

        assert result["operations"]["updated"] == 1


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


class TestSeedAll:
    """Test seed-all subcommand."""

    def test_seed_all_seeds_templates_and_companies(self, tmp_path):
        """seed-all discovers and writes all templates then companies."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()

        # Create minimal fixture data in tmp_path
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        companies_dir = tmp_path / "companies"
        companies_dir.mkdir()

        (templates_dir / "electronics.json").write_text(json.dumps(ELECTRONICS_TEMPLATE))
        (templates_dir / "hotel.json").write_text(json.dumps(HOTEL_TEMPLATE))
        (companies_dir / "ekaette-electronics.json").write_text(
            json.dumps(EKAETTE_ELECTRONICS_COMPANY)
        )

        result = seed_all(db, data_dir=tmp_path)

        assert result["errors"] == []
        assert result["templates"]["created"] == 2
        assert result["companies"]["created"] == 1
        assert db.get_doc("industry_templates/electronics") is not None
        assert db.get_doc("industry_templates/hotel") is not None
        assert db.get_doc("tenants/public/companies/ekaette-electronics") is not None

    def test_seed_all_is_idempotent(self, tmp_path):
        """Running seed-all twice reports unchanged on second run."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        companies_dir = tmp_path / "companies"
        companies_dir.mkdir()

        (templates_dir / "electronics.json").write_text(json.dumps(ELECTRONICS_TEMPLATE))
        (companies_dir / "ekaette-electronics.json").write_text(
            json.dumps(EKAETTE_ELECTRONICS_COMPANY)
        )

        first = seed_all(db, data_dir=tmp_path)
        second = seed_all(db, data_dir=tmp_path)

        assert first["templates"]["created"] == 1
        assert first["companies"]["created"] == 1
        assert second["templates"]["unchanged"] == 1
        assert second["companies"]["unchanged"] == 1
        assert second["errors"] == []

    def test_seed_all_reports_missing_directories(self, tmp_path):
        """seed-all reports errors when template/company directories don't exist."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()
        result = seed_all(db, data_dir=tmp_path / "nonexistent")

        assert len(result["errors"]) == 2
        assert any("templates directory" in e for e in result["errors"])
        assert any("companies directory" in e for e in result["errors"])


class TestDataRegistryFixtures:
    """Validate the tracked tests/fixtures/registry JSON fixture files.

    Uses the git-tracked path (tests/fixtures/registry/) so CI and clean clones
    work without .data/registry/ being present.
    """

    FIXTURES_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "fixtures",
        "registry",
    )

    def _load_fixture(self, rel_path: str) -> dict[str, Any]:
        full_path = os.path.join(self.FIXTURES_DIR, rel_path)
        with open(full_path) as f:
            return json.load(f)

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-hotel.json",
        "ekaette-automotive.json",
        "ekaette-fashion.json",
        "ekaette-telecom.json",
        "ekaette-aviation.json",
    ])
    def test_company_fixture_passes_schema_validation(self, filename):
        """Each company fixture must include schema_version and pass validation."""
        from app.configs.registry_schema import validate_company

        company = self._load_fixture(f"companies/{filename}")
        assert "schema_version" in company, f"{filename} missing schema_version"
        errors = validate_company(company)
        assert errors == [], f"{filename} validation errors: {errors}"

    @pytest.mark.parametrize("filename", [
        "electronics.json",
        "hotel.json",
        "automotive.json",
        "fashion.json",
        "telecom.json",
        "aviation-support.json",
    ])
    def test_template_fixture_passes_schema_validation(self, filename):
        """Each template fixture must include schema_version and pass validation."""
        from app.configs.registry_schema import validate_template

        template = self._load_fixture(f"templates/{filename}")
        assert "schema_version" in template, f"{filename} missing schema_version"
        errors = validate_template(template)
        assert errors == [], f"{filename} validation errors: {errors}"

    def test_every_company_references_existing_template(self):
        """Each company's industry_template_id matches an existing template fixture."""
        registry_dir = self.FIXTURES_DIR

        template_ids = set()
        for fn in os.listdir(os.path.join(registry_dir, "templates")):
            if fn.endswith(".json"):
                with open(os.path.join(registry_dir, "templates", fn)) as f:
                    template_ids.add(json.load(f)["id"])

        for fn in os.listdir(os.path.join(registry_dir, "companies")):
            if fn.endswith(".json"):
                with open(os.path.join(registry_dir, "companies", fn)) as f:
                    company = json.load(f)
                assert company["industry_template_id"] in template_ids, (
                    f"{fn} references template '{company['industry_template_id']}' "
                    f"which does not exist in templates/"
                )

    def test_seed_all_with_tracked_fixtures(self):
        """seed-all works end-to-end with the tracked tests/fixtures/registry directory."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()
        result = seed_all(db, data_dir=self.FIXTURES_DIR)

        assert result["errors"] == [], f"seed-all errors: {result['errors']}"
        assert result["templates"]["created"] == 6
        assert result["companies"]["created"] == 6

    # --- Product fixture validation ---

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-automotive.json",
        "ekaette-fashion.json",
        "ekaette-telecom.json",
    ])
    def test_product_fixture_passes_schema_validation(self, filename):
        """Every product in each fixture file must pass validate_product()."""
        from app.configs.registry_schema import validate_product

        full_path = os.path.join(self.FIXTURES_DIR, "products", filename)
        with open(full_path) as f:
            products = json.load(f)
        assert isinstance(products, list), f"{filename} must be a JSON array"
        assert len(products) >= 4, f"{filename} must have at least 4 products"
        for product in products:
            errors = validate_product(product)
            assert errors == [], f"{filename} product '{product.get('id')}': {errors}"

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-automotive.json",
        "ekaette-fashion.json",
        "ekaette-telecom.json",
    ])
    def test_product_fixture_has_demo_tier(self, filename):
        """Every product fixture must be tagged with data_tier='demo'."""
        full_path = os.path.join(self.FIXTURES_DIR, "products", filename)
        with open(full_path) as f:
            products = json.load(f)
        for product in products:
            assert product.get("data_tier") == "demo", (
                f"{filename} product '{product.get('id')}' missing data_tier='demo'"
            )

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-automotive.json",
        "ekaette-fashion.json",
        "ekaette-telecom.json",
    ])
    def test_product_fixture_has_unique_ids(self, filename):
        """All product IDs within a fixture file must be unique."""
        full_path = os.path.join(self.FIXTURES_DIR, "products", filename)
        with open(full_path) as f:
            products = json.load(f)
        ids = [p["id"] for p in products]
        assert len(ids) == len(set(ids)), f"{filename} has duplicate product IDs"

    def test_product_fixtures_include_out_of_stock_items(self):
        """At least one product across all fixtures should be out of stock."""
        out_of_stock_count = 0
        products_dir = os.path.join(self.FIXTURES_DIR, "products")
        for fn in os.listdir(products_dir):
            if fn.endswith(".json"):
                with open(os.path.join(products_dir, fn)) as f:
                    products = json.load(f)
                out_of_stock_count += sum(1 for p in products if not p["in_stock"])
        assert out_of_stock_count >= 4, (
            f"Expected at least 4 out-of-stock products across fixtures, got {out_of_stock_count}"
        )

    # --- Booking slot fixture validation ---

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-hotel.json",
        "ekaette-automotive.json",
    ])
    def test_booking_slot_fixture_passes_schema_validation(self, filename):
        """Every slot in each fixture file must pass validate_booking_slot()."""
        from app.configs.registry_schema import validate_booking_slot

        full_path = os.path.join(self.FIXTURES_DIR, "booking_slots", filename)
        with open(full_path) as f:
            slots = json.load(f)
        assert isinstance(slots, list), f"{filename} must be a JSON array"
        assert len(slots) >= 4, f"{filename} must have at least 4 slots"
        for slot in slots:
            errors = validate_booking_slot(slot)
            assert errors == [], f"{filename} slot '{slot.get('id')}': {errors}"

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-hotel.json",
        "ekaette-automotive.json",
    ])
    def test_booking_slot_fixture_has_demo_tier(self, filename):
        """Every booking slot fixture must be tagged with data_tier='demo'."""
        full_path = os.path.join(self.FIXTURES_DIR, "booking_slots", filename)
        with open(full_path) as f:
            slots = json.load(f)
        for slot in slots:
            assert slot.get("data_tier") == "demo", (
                f"{filename} slot '{slot.get('id')}' missing data_tier='demo'"
            )

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-hotel.json",
        "ekaette-automotive.json",
    ])
    def test_booking_slot_fixture_has_unique_ids(self, filename):
        """All slot IDs within a fixture file must be unique."""
        full_path = os.path.join(self.FIXTURES_DIR, "booking_slots", filename)
        with open(full_path) as f:
            slots = json.load(f)
        ids = [s["id"] for s in slots]
        assert len(ids) == len(set(ids)), f"{filename} has duplicate slot IDs"

    def test_booking_slot_fixtures_include_unavailable(self):
        """At least one booking slot across all fixtures should be unavailable."""
        unavailable_count = 0
        slots_dir = os.path.join(self.FIXTURES_DIR, "booking_slots")
        for fn in os.listdir(slots_dir):
            if fn.endswith(".json"):
                with open(os.path.join(slots_dir, fn)) as f:
                    slots = json.load(f)
                unavailable_count += sum(1 for s in slots if not s["available"])
        assert unavailable_count >= 3, (
            f"Expected at least 3 unavailable slots across fixtures, got {unavailable_count}"
        )

    # --- Knowledge fixture validation ---

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-hotel.json",
        "ekaette-automotive.json",
        "ekaette-fashion.json",
        "ekaette-telecom.json",
        "ekaette-aviation.json",
    ])
    def test_knowledge_fixture_passes_schema_validation(self, filename):
        """Every knowledge entry in each fixture must pass validate_knowledge_entry()."""
        from app.configs.registry_schema import validate_knowledge_entry

        full_path = os.path.join(self.FIXTURES_DIR, "knowledge", filename)
        with open(full_path) as f:
            entries = json.load(f)
        assert isinstance(entries, list), f"{filename} must be a JSON array"
        assert len(entries) >= 4, f"{filename} must have at least 4 knowledge entries"
        for entry in entries:
            errors = validate_knowledge_entry(entry)
            assert errors == [], f"{filename} entry '{entry.get('id')}': {errors}"

    @pytest.mark.parametrize("filename", [
        "ekaette-electronics.json",
        "ekaette-hotel.json",
        "ekaette-automotive.json",
        "ekaette-fashion.json",
        "ekaette-telecom.json",
        "ekaette-aviation.json",
    ])
    def test_knowledge_fixture_has_unique_ids(self, filename):
        """All knowledge entry IDs within a fixture file must be unique."""
        full_path = os.path.join(self.FIXTURES_DIR, "knowledge", filename)
        with open(full_path) as f:
            entries = json.load(f)
        ids = [e["id"] for e in entries]
        assert len(ids) == len(set(ids)), f"{filename} has duplicate knowledge IDs"


class TestSeedAllRuntimeData:
    """Tests for seed-all --include-runtime-data flag."""

    FIXTURES_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "fixtures",
        "registry",
    )

    def test_seed_all_without_flag_skips_runtime_data(self, tmp_path):
        """seed-all without include_runtime_data does NOT seed products/slots/knowledge."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()

        # Create minimal config fixtures
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        companies_dir = tmp_path / "companies"
        companies_dir.mkdir()
        products_dir = tmp_path / "products"
        products_dir.mkdir()

        (templates_dir / "electronics.json").write_text(json.dumps(ELECTRONICS_TEMPLATE))
        (companies_dir / "ekaette-electronics.json").write_text(
            json.dumps(EKAETTE_ELECTRONICS_COMPANY)
        )
        (products_dir / "ekaette-electronics.json").write_text(json.dumps([
            {"id": "prod-x", "name": "X", "price": 100, "currency": "NGN",
             "category": "test", "in_stock": True, "data_tier": "demo"},
        ]))

        result = seed_all(db, data_dir=tmp_path)

        assert result["templates"]["created"] == 1
        assert result["companies"]["created"] == 1
        # Runtime data should NOT be seeded
        assert result["products"]["created"] == 0
        assert result["booking_slots"]["created"] == 0
        assert result["knowledge"]["created"] == 0
        # Verify no product docs exist
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-x"
        ) is None

    def test_seed_all_with_flag_seeds_runtime_data(self, tmp_path):
        """seed-all with include_runtime_data=True seeds products, slots, and knowledge."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()

        # Config fixtures
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        companies_dir = tmp_path / "companies"
        companies_dir.mkdir()
        products_dir = tmp_path / "products"
        products_dir.mkdir()
        slots_dir = tmp_path / "booking_slots"
        slots_dir.mkdir()
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()

        (templates_dir / "electronics.json").write_text(json.dumps(ELECTRONICS_TEMPLATE))
        (companies_dir / "ekaette-electronics.json").write_text(
            json.dumps(EKAETTE_ELECTRONICS_COMPANY)
        )
        (products_dir / "ekaette-electronics.json").write_text(json.dumps([
            {"id": "prod-a", "name": "Product A", "price": 1000, "currency": "NGN",
             "category": "test", "in_stock": True, "data_tier": "demo"},
            {"id": "prod-b", "name": "Product B", "price": 2000, "currency": "NGN",
             "category": "test", "in_stock": False, "data_tier": "demo"},
        ]))
        (slots_dir / "ekaette-electronics.json").write_text(json.dumps([
            {"id": "slot-a", "date": "2026-03-15", "time": "10:00",
             "available": True, "data_tier": "demo"},
        ]))
        (knowledge_dir / "ekaette-electronics.json").write_text(json.dumps([
            {"id": "kb-hours", "title": "Hours", "text": "9 AM to 7 PM.",
             "tags": ["hours"], "source": "seed"},
        ]))

        result = seed_all(db, data_dir=tmp_path, include_runtime_data=True)

        assert result["errors"] == [], f"errors: {result['errors']}"
        assert result["templates"]["created"] == 1
        assert result["companies"]["created"] == 1
        assert result["products"]["created"] == 2
        assert result["booking_slots"]["created"] == 1
        assert result["knowledge"]["created"] == 1

        # Verify actual data in DB
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-a"
        ) is not None
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-b"
        ) is not None
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/booking_slots/slot-a"
        ) is not None
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/knowledge/kb-hours"
        ) is not None

    def test_seed_all_with_runtime_data_and_tracked_fixtures(self):
        """seed-all --include-runtime-data with real tracked fixtures seeds all data."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()
        result = seed_all(db, data_dir=self.FIXTURES_DIR, include_runtime_data=True)

        assert result["errors"] == [], f"seed-all errors: {result['errors']}"
        assert result["templates"]["created"] == 6
        assert result["companies"]["created"] == 6
        # 12 + 10 + 12 + 10 = 44 products
        assert result["products"]["created"] == 44
        # 8 + 8 + 10 = 26 booking slots
        assert result["booking_slots"]["created"] == 26
        # 4 * 6 = 24 knowledge entries
        assert result["knowledge"]["created"] == 24

    def test_seed_all_with_runtime_data_is_idempotent(self, tmp_path):
        """Running seed-all --include-runtime-data twice reports unchanged on second run."""
        from scripts.registry import seed_all

        db = FakeFirestoreDB()

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        companies_dir = tmp_path / "companies"
        companies_dir.mkdir()
        products_dir = tmp_path / "products"
        products_dir.mkdir()

        (templates_dir / "electronics.json").write_text(json.dumps(ELECTRONICS_TEMPLATE))
        (companies_dir / "ekaette-electronics.json").write_text(
            json.dumps(EKAETTE_ELECTRONICS_COMPANY)
        )
        (products_dir / "ekaette-electronics.json").write_text(json.dumps([
            {"id": "prod-x", "name": "X", "price": 100, "currency": "NGN",
             "category": "test", "in_stock": True, "data_tier": "demo"},
        ]))

        first = seed_all(db, data_dir=tmp_path, include_runtime_data=True)
        second = seed_all(db, data_dir=tmp_path, include_runtime_data=True)

        assert first["products"]["created"] == 1
        assert second["products"]["unchanged"] == 1
        assert second["products"]["created"] == 0


class TestPurgeDemoData:
    """Tests for purge_demo_data() — removes only data_tier='demo' documents."""

    def test_purge_removes_demo_tagged_documents(self):
        """purge_demo_data() deletes all docs with data_tier='demo'."""
        from scripts.registry import purge_demo_data

        db = FakeFirestoreDB()
        # Set up a company doc (needed for company discovery via stream)
        db.set_doc("tenants/public/companies/ekaette-electronics", {
            "company_id": "ekaette-electronics",
            "tenant_id": "public",
        })
        # Demo products
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-demo-1",
            {"id": "prod-demo-1", "name": "Demo 1", "data_tier": "demo"},
        )
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-demo-2",
            {"id": "prod-demo-2", "name": "Demo 2", "data_tier": "demo"},
        )
        # Demo booking slot
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/booking_slots/slot-demo-1",
            {"id": "slot-demo-1", "date": "2026-03-15", "data_tier": "demo"},
        )
        # Demo knowledge entry
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/knowledge/kb-demo-1",
            {"id": "kb-demo-1", "title": "Demo KB", "data_tier": "demo"},
        )

        result = purge_demo_data(db, tenant_id="public")

        assert result["tenant_id"] == "public"
        assert result["deleted"]["products"] == 2
        assert result["deleted"]["booking_slots"] == 1
        assert result["deleted"]["knowledge"] == 1
        # Verify docs are actually gone
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-demo-1"
        ) is None
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-demo-2"
        ) is None
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/booking_slots/slot-demo-1"
        ) is None
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/knowledge/kb-demo-1"
        ) is None

    def test_purge_leaves_non_demo_documents_intact(self):
        """purge_demo_data() does not delete documents without data_tier='demo'."""
        from scripts.registry import purge_demo_data

        db = FakeFirestoreDB()
        db.set_doc("tenants/public/companies/ekaette-electronics", {
            "company_id": "ekaette-electronics",
            "tenant_id": "public",
        })
        # Production product (no data_tier)
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-real-1",
            {"id": "prod-real-1", "name": "Real Product"},
        )
        # Demo product
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-demo-1",
            {"id": "prod-demo-1", "name": "Demo Product", "data_tier": "demo"},
        )
        # Production knowledge (data_tier != "demo")
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/knowledge/kb-real-1",
            {"id": "kb-real-1", "title": "Real KB", "data_tier": "production"},
        )

        result = purge_demo_data(db, tenant_id="public")

        assert result["deleted"]["products"] == 1
        assert result["deleted"]["knowledge"] == 0
        # Real product still exists
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-real-1"
        ) is not None
        # Real knowledge still exists
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/knowledge/kb-real-1"
        ) is not None
        # Demo product is gone
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-demo-1"
        ) is None

    def test_purge_handles_empty_tenant(self):
        """purge_demo_data() on a tenant with no companies returns zero counts."""
        from scripts.registry import purge_demo_data

        db = FakeFirestoreDB()

        result = purge_demo_data(db, tenant_id="public")

        assert result["tenant_id"] == "public"
        assert result["deleted"]["products"] == 0
        assert result["deleted"]["booking_slots"] == 0
        assert result["deleted"]["knowledge"] == 0

    def test_purge_across_multiple_companies(self):
        """purge_demo_data() purges demo data from all companies in the tenant."""
        from scripts.registry import purge_demo_data

        db = FakeFirestoreDB()
        # Two companies
        db.set_doc("tenants/public/companies/ekaette-electronics", {
            "company_id": "ekaette-electronics",
        })
        db.set_doc("tenants/public/companies/ekaette-hotel", {
            "company_id": "ekaette-hotel",
        })
        # Demo products in both
        db.set_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-e1",
            {"id": "prod-e1", "data_tier": "demo"},
        )
        db.set_doc(
            "tenants/public/companies/ekaette-hotel/booking_slots/slot-h1",
            {"id": "slot-h1", "data_tier": "demo"},
        )

        result = purge_demo_data(db, tenant_id="public")

        assert result["deleted"]["products"] == 1
        assert result["deleted"]["booking_slots"] == 1
        assert db.get_doc(
            "tenants/public/companies/ekaette-electronics/products/prod-e1"
        ) is None
        assert db.get_doc(
            "tenants/public/companies/ekaette-hotel/booking_slots/slot-h1"
        ) is None


# ═══ 2b. Product & Booking Slot Schema Validation ═══


class TestProductSchemaValidation:
    """Tests for validate_product() — product/catalog item validation."""

    def test_validate_product_accepts_valid(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({
            "id": "prod-iphone-15",
            "name": "iPhone 15 Pro",
            "price": 850000,
            "currency": "NGN",
            "category": "smartphones",
            "brand": "Apple",
            "in_stock": True,
            "features": ["A17 Pro chip", "48MP camera"],
            "data_tier": "demo",
        })
        assert errors == []

    def test_validate_product_rejects_missing_fields(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({"brand": "Apple"})
        error_text = " ".join(errors)
        assert "id" in error_text
        assert "name" in error_text
        assert "price" in error_text
        assert "currency" in error_text
        assert "category" in error_text
        assert "in_stock" in error_text

    def test_validate_product_rejects_negative_price(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({
            "id": "prod-x", "name": "X", "price": -100,
            "currency": "NGN", "category": "x", "in_stock": True,
        })
        assert any("price" in e for e in errors)

    def test_validate_product_accepts_zero_price(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({
            "id": "prod-free", "name": "Free Item", "price": 0,
            "currency": "NGN", "category": "promo", "in_stock": True,
        })
        assert errors == []

    def test_validate_product_accepts_out_of_stock(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({
            "id": "prod-oos", "name": "Sold Out", "price": 50000,
            "currency": "NGN", "category": "phones", "in_stock": False,
            "data_tier": "demo",
        })
        assert errors == []

    def test_validate_product_rejects_non_dict(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product("not a dict")
        assert errors == ["product must be a dict"]

    def test_validate_product_rejects_non_bool_in_stock(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({
            "id": "prod-x", "name": "X", "price": 100,
            "currency": "NGN", "category": "x", "in_stock": "yes",
        })
        assert any("in_stock" in e for e in errors)

    def test_validate_product_optional_data_tier_must_be_string(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({
            "id": "prod-x", "name": "X", "price": 100,
            "currency": "NGN", "category": "x", "in_stock": True,
            "data_tier": 123,
        })
        assert any("data_tier" in e for e in errors)

    def test_validate_product_accepts_float_price(self):
        from app.configs.registry_schema import validate_product

        errors = validate_product({
            "id": "prod-x", "name": "X", "price": 99.99,
            "currency": "USD", "category": "x", "in_stock": True,
        })
        assert errors == []


class TestBookingSlotSchemaValidation:
    """Tests for validate_booking_slot() — booking slot validation."""

    def test_validate_slot_accepts_valid(self):
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot({
            "id": "slot-2026-03-15-10-ikeja",
            "date": "2026-03-15",
            "time": "10:00",
            "location": "Lagos - Ikeja",
            "available": True,
            "data_tier": "demo",
        })
        assert errors == []

    def test_validate_slot_rejects_missing_fields(self):
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot({"location": "Lagos"})
        error_text = " ".join(errors)
        assert "id" in error_text
        assert "date" in error_text
        assert "time" in error_text
        assert "available" in error_text

    def test_validate_slot_rejects_bad_date_format(self):
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot({
            "id": "slot-x", "date": "15/03/2026", "time": "10:00",
            "available": True,
        })
        assert any("YYYY-MM-DD" in e for e in errors)

    def test_validate_slot_accepts_unavailable(self):
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot({
            "id": "slot-booked", "date": "2026-03-15", "time": "14:00",
            "available": False, "data_tier": "demo",
        })
        assert errors == []

    def test_validate_slot_rejects_non_dict(self):
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot([1, 2, 3])
        assert errors == ["booking_slot must be a dict"]

    def test_validate_slot_rejects_non_bool_available(self):
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot({
            "id": "slot-x", "date": "2026-03-15", "time": "10:00",
            "available": "yes",
        })
        assert any("available" in e for e in errors)

    def test_validate_slot_optional_data_tier_must_be_string(self):
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot({
            "id": "slot-x", "date": "2026-03-15", "time": "10:00",
            "available": True, "data_tier": 42,
        })
        assert any("data_tier" in e for e in errors)

    def test_validate_slot_accepts_various_date_formats_within_iso(self):
        """Accepts dates that match YYYY-MM-DD pattern."""
        from app.configs.registry_schema import validate_booking_slot

        errors = validate_booking_slot({
            "id": "slot-x", "date": "2026-12-31", "time": "09:30",
            "available": True,
        })
        assert errors == []


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

    def test_verify_migration_detects_missing_targets(self):
        """verify_migration must detect corruption/missing destination docs."""
        from scripts.migrate_to_tenant_scoped import verify_migration

        db = FakeFirestoreDB()
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
        })
        # Intentionally do not write target company doc.

        result = verify_migration(db, tenant_id="public", collections=["profiles"])

        assert result["success"] is False
        assert result["checked"] == 1
        assert any("missing target tenants/public/companies/ekaette-electronics" in e for e in result["errors"])


class TestMigrateTenantScopedCLI:
    """CLI-level safety checks for dry-run summaries, resume, and verify."""

    def test_main_dry_run_prints_diff_summary_and_skips_writes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        from scripts import migrate_to_tenant_scoped as migrate_cli

        db = FakeFirestoreDB()
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
            "overview": "Trade-in focused electronics store.",
            "facts": {},
            "links": [],
            "system_connectors": {},
        })
        _install_fake_cli_modules(monkeypatch, db)

        migrate_cli.main(["--tenant", "public", "--dry-run", "--collections", "profiles"])

        out = capsys.readouterr().out
        assert "Dry run complete (no writes performed)." in out
        assert '"section": "profiles"' in out
        assert '"dryRun": true' in out
        assert '"operations"' in out
        assert '"sampleIds"' in out
        assert db.get_doc("tenants/public/companies/ekaette-electronics") is None

    def test_main_resume_creates_and_uses_checkpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: pytest.TempPathFactory,
    ):
        from scripts import migrate_to_tenant_scoped as migrate_cli

        db = FakeFirestoreDB()
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
            "overview": "Trade-in focused electronics store.",
            "facts": {},
            "links": [],
            "system_connectors": {},
        })
        checkpoint_file = tmp_path / "resume-checkpoint.json"
        _install_fake_cli_modules(monkeypatch, db)

        # First run writes and records checkpoint.
        migrate_cli.main([
            "--tenant", "public",
            "--resume",
            "--checkpoint-file", str(checkpoint_file),
            "--collections", "profiles",
        ])
        assert checkpoint_file.exists()
        payload = json.loads(checkpoint_file.read_text())
        assert payload["meta"]["tenant_id"] == "public"
        assert "profiles" in payload["completed"]
        assert "ekaette-electronics" in payload["completed"]["profiles"]

        # Remove target doc to prove resume skip is checkpoint-driven (not only idempotent target check).
        db._store.pop("tenants/public/companies/ekaette-electronics", None)
        migrate_cli.main([
            "--tenant", "public",
            "--resume",
            "--checkpoint-file", str(checkpoint_file),
            "--collections", "profiles",
        ])
        out = capsys.readouterr().out
        assert '"resume_skipped": 1' in out
        assert db.get_doc("tenants/public/companies/ekaette-electronics") is None

    def test_main_resume_rejects_checkpoint_tenant_mismatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: pytest.TempPathFactory,
    ):
        from scripts import migrate_to_tenant_scoped as migrate_cli

        db = FakeFirestoreDB()
        _install_fake_cli_modules(monkeypatch, db)

        checkpoint_file = tmp_path / "mismatch-checkpoint.json"
        checkpoint_file.write_text(json.dumps({
            "meta": {"tenant_id": "other-tenant", "company_id": ""},
            "completed": {"profiles": ["ekaette-electronics"]},
        }))

        with pytest.raises(SystemExit) as excinfo:
            migrate_cli.main([
                "--tenant", "public",
                "--resume",
                "--checkpoint-file", str(checkpoint_file),
                "--collections", "profiles",
            ])
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "invalid checkpoint file" in out
        assert "tenant_id mismatch" in out

    def test_main_verify_flag_exits_on_mismatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        from scripts import migrate_to_tenant_scoped as migrate_cli

        db = FakeFirestoreDB()
        # Source docs present, but we run --verify without running migrations first.
        db.set_doc("company_profiles/ekaette-electronics", {
            "industry": "electronics",
            "name": "Ekaette Devices Hub",
        })
        _install_fake_cli_modules(monkeypatch, db)
        monkeypatch.setattr(
            migrate_cli,
            "verify_migration",
            lambda *args, **kwargs: {
                "success": False,
                "checked": 1,
                "errors": ["[profiles] missing target tenants/public/companies/ekaette-electronics"],
            },
        )

        with pytest.raises(SystemExit) as excinfo:
            migrate_cli.main([
                "--tenant", "public",
                "--verify",
                "--collections", "profiles",
            ])
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "Verification summary:" in out
        assert '"success": false' in out.lower()
        assert "missing target tenants/public/companies/ekaette-electronics" in out
