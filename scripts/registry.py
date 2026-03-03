"""Provisioning CLI for the multi-tenant industry registry.

Subcommands:
  seed-templates       Validate and write industry templates to Firestore
  provision-company    Create a tenant-scoped company profile
  import-knowledge     Import knowledge entries for a company
  validate             Cross-check templates, companies, and connectors
  smoke                Non-UI flow check: resolve config → verify state keys

Usage:
  python -m scripts.registry seed-templates --file=templates.json
  python -m scripts.registry provision-company --tenant=X --company=Y --template=Z
  python -m scripts.registry import-knowledge --tenant=X --company=Y --file=knowledge.json
  python -m scripts.registry validate [--tenant=X]
  python -m scripts.registry smoke --tenant=X --company=Y
  python -m scripts.registry seed-all [--data-dir=tests/fixtures/registry]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.configs.registry_schema import (
    validate_booking_slot,
    validate_capability_overrides,
    validate_company,
    validate_knowledge_entry,
    validate_product,
    validate_template,
    validate_theme,
)
from app.configs import REGISTRY_SCHEMA_VERSION


def _new_write_ops() -> dict[str, int]:
    return {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
    }


# ═══ seed-templates ═══


def seed_templates(
    db: Any,
    templates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and write industry templates to Firestore.

    Returns {"written": int, "errors": list[str]}.
    """
    written = 0
    errors: list[str] = []
    operations = _new_write_ops()

    for template in templates:
        normalized_template = dict(template)
        normalized_template.setdefault("schema_version", REGISTRY_SCHEMA_VERSION)
        validation_errors = validate_template(normalized_template)
        if validation_errors:
            template_id = normalized_template.get("id", "<unknown>")
            errors.append(f"template '{template_id}': {'; '.join(validation_errors)}")
            operations["failed"] += 1
            continue

        template_id = normalized_template["id"]
        doc_ref = db.collection("industry_templates").document(template_id)
        existing_doc = doc_ref.get()
        if getattr(existing_doc, "exists", False):
            existing_data = existing_doc.to_dict() if hasattr(existing_doc, "to_dict") else {}
            if existing_data == normalized_template:
                operations["unchanged"] += 1
                continue
            op = "updated"
        else:
            op = "created"
        doc_ref.set(normalized_template)
        operations[op] += 1
        written += 1

    return {"written": written, "errors": errors, "operations": operations}


# ═══ provision-company ═══


def provision_company(
    db: Any,
    company_data: dict[str, Any],
) -> dict[str, Any]:
    """Validate and write a tenant-scoped company profile.

    Returns {"success": bool, "errors": list[str]}.
    """
    normalized_company = dict(company_data)
    company_id = normalized_company.get("company_id")
    if isinstance(company_id, str) and company_id.strip():
        normalized_company.setdefault("display_name", company_id)
    normalized_company.setdefault("schema_version", REGISTRY_SCHEMA_VERSION)
    normalized_company.setdefault("overview", "")
    normalized_company.setdefault("facts", {})
    normalized_company.setdefault("links", [])
    normalized_company.setdefault("connectors", {})
    normalized_company.setdefault("capability_overrides", {})
    normalized_company.setdefault("ui_overrides", {})
    normalized_company.setdefault("status", "active")

    validation_errors = validate_company(normalized_company)
    if validation_errors:
        return {
            "success": False,
            "errors": validation_errors,
            "operation": "failed",
        }

    tenant_id = normalized_company["tenant_id"]
    company_id = normalized_company["company_id"]
    template_id = normalized_company["industry_template_id"]

    # Verify template exists
    template_ref = db.collection("industry_templates").document(template_id)
    template_doc = template_ref.get()
    if not getattr(template_doc, "exists", False):
        return {
            "success": False,
            "errors": [f"template '{template_id}' not found in industry_templates"],
            "operation": "failed",
        }

    # Validate capability overrides if present
    template_data = template_doc.to_dict() if hasattr(template_doc, "to_dict") else {}
    template_caps = template_data.get("capabilities", [])
    if isinstance(template_caps, list):
        cap_overrides = normalized_company.get("capability_overrides")
        if cap_overrides:
            cap_errors = validate_capability_overrides(cap_overrides, template_caps)
            if cap_errors:
                return {"success": False, "errors": cap_errors, "operation": "failed"}

    doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
    )
    existing_doc = doc_ref.get()
    if getattr(existing_doc, "exists", False):
        existing_data = existing_doc.to_dict() if hasattr(existing_doc, "to_dict") else {}
        if existing_data == normalized_company:
            return {"success": True, "errors": [], "operation": "unchanged"}
        operation = "updated"
    else:
        operation = "created"
    doc_ref.set(normalized_company)

    return {"success": True, "errors": [], "operation": operation}


# ═══ import-knowledge ═══


def import_knowledge(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and write knowledge entries under a company's knowledge subcollection.

    Returns {"written": int, "errors": list[str]}.
    """
    written = 0
    errors: list[str] = []
    operations = _new_write_ops()

    for entry in entries:
        validation_errors = validate_knowledge_entry(entry)
        if validation_errors:
            entry_id = entry.get("id", "<unknown>")
            errors.append(f"entry '{entry_id}': {'; '.join(validation_errors)}")
            operations["failed"] += 1
            continue

        entry_id = entry["id"]
        doc_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection("knowledge")
            .document(entry_id)
        )
        normalized_entry = dict(entry)
        existing_doc = doc_ref.get()
        if getattr(existing_doc, "exists", False):
            existing_data = existing_doc.to_dict() if hasattr(existing_doc, "to_dict") else {}
            if existing_data == normalized_entry:
                operations["unchanged"] += 1
                continue
            op = "updated"
        else:
            op = "created"
        doc_ref.set(normalized_entry)
        operations[op] += 1
        written += 1

    return {"written": written, "errors": errors, "operations": operations}


# ═══ import-products ═══


def import_products(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and write product documents under a company's products subcollection.

    Returns {"written": int, "errors": list[str], "operations": {...}}.
    """
    written = 0
    errors: list[str] = []
    operations = _new_write_ops()

    for product in products:
        validation_errors = validate_product(product)
        if validation_errors:
            product_id = product.get("id", "<unknown>")
            errors.append(f"product '{product_id}': {'; '.join(validation_errors)}")
            operations["failed"] += 1
            continue

        product_id = product["id"]
        doc_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection("products")
            .document(product_id)
        )
        normalized_product = dict(product)
        existing_doc = doc_ref.get()
        if getattr(existing_doc, "exists", False):
            existing_data = existing_doc.to_dict() if hasattr(existing_doc, "to_dict") else {}
            if existing_data == normalized_product:
                operations["unchanged"] += 1
                continue
            op = "updated"
        else:
            op = "created"
        doc_ref.set(normalized_product)
        operations[op] += 1
        written += 1

    return {"written": written, "errors": errors, "operations": operations}


# ═══ import-booking-slots ═══


def import_booking_slots(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    slots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and write booking slot documents under a company's booking_slots subcollection.

    Returns {"written": int, "errors": list[str], "operations": {...}}.
    """
    written = 0
    errors: list[str] = []
    operations = _new_write_ops()

    for slot in slots:
        validation_errors = validate_booking_slot(slot)
        if validation_errors:
            slot_id = slot.get("id", "<unknown>")
            errors.append(f"slot '{slot_id}': {'; '.join(validation_errors)}")
            operations["failed"] += 1
            continue

        slot_id = slot["id"]
        doc_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection("booking_slots")
            .document(slot_id)
        )
        normalized_slot = dict(slot)
        existing_doc = doc_ref.get()
        if getattr(existing_doc, "exists", False):
            existing_data = existing_doc.to_dict() if hasattr(existing_doc, "to_dict") else {}
            if existing_data == normalized_slot:
                operations["unchanged"] += 1
                continue
            op = "updated"
        else:
            op = "created"
        doc_ref.set(normalized_slot)
        operations[op] += 1
        written += 1

    return {"written": written, "errors": errors, "operations": operations}


# ═══ purge-demo-data ═══


def purge_demo_data(
    db: Any,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Delete all documents with data_tier='demo' from runtime subcollections.

    Scans products, booking_slots, and knowledge subcollections under every
    company in the given tenant. Only deletes documents explicitly tagged
    with data_tier='demo', leaving untagged (production) documents intact.

    Returns {"tenant_id": str, "deleted": {"products": int, ...}}.
    """
    deleted = {"products": 0, "booking_slots": 0, "knowledge": 0}
    companies = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .stream()
    )
    for company_doc in companies:
        company_id = company_doc.id
        for subcollection in ("products", "booking_slots", "knowledge"):
            col = (
                db.collection("tenants")
                .document(tenant_id)
                .collection("companies")
                .document(company_id)
                .collection(subcollection)
            )
            docs = col.where("data_tier", "==", "demo").stream()
            for doc in docs:
                doc.reference.delete()
                deleted[subcollection] += 1

    return {"tenant_id": tenant_id, "deleted": deleted}


# ═══ validate ═══


def validate_registry(
    db: Any,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Cross-validate templates and companies for consistency.

    Checks:
    - Every company references an existing template
    - Company connectors are in the template's connectors_supported list
    - Capability overrides reference valid capabilities

    Returns {"errors": list[str]}.
    """
    errors: list[str] = []

    # Load all templates
    template_docs = db.collection("industry_templates").stream()
    templates: dict[str, dict[str, Any]] = {}
    for doc in template_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or data.get("id", "")
        if doc_id:
            for err in validate_template(data):
                errors.append(f"template '{doc_id}': {err}")
            templates[doc_id] = data

    # Load all companies for this tenant
    company_docs = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .stream()
    )

    for doc in company_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        company_id = getattr(doc, "id", "") or data.get("company_id", "")
        template_id = data.get("industry_template_id", "")

        for err in validate_company(data):
            errors.append(f"company '{company_id}': {err}")

        ui_overrides = data.get("ui_overrides")
        if isinstance(ui_overrides, dict) and "theme" in ui_overrides:
            for err in validate_theme(ui_overrides.get("theme")):
                errors.append(f"company '{company_id}': {err}")

        # Check template exists
        if template_id not in templates:
            errors.append(
                f"company '{company_id}' references template '{template_id}' "
                f"which does not exist"
            )
            continue

        template = templates[template_id]

        # Check connectors
        company_connectors = data.get("connectors", {})
        supported_connectors = template.get("connectors_supported", [])
        if isinstance(company_connectors, dict) and isinstance(supported_connectors, list):
            for connector_key in company_connectors:
                if connector_key not in supported_connectors:
                    errors.append(
                        f"company '{company_id}' uses connector '{connector_key}' "
                        f"not in template '{template_id}' connectors_supported"
                    )

        # Check capability overrides
        cap_overrides = data.get("capability_overrides")
        if cap_overrides and isinstance(cap_overrides, dict):
            template_caps = template.get("capabilities", [])
            if isinstance(template_caps, list):
                cap_errors = validate_capability_overrides(cap_overrides, template_caps)
                for err in cap_errors:
                    errors.append(f"company '{company_id}': {err}")

    return {"errors": errors}


# ═══ smoke ═══


async def smoke_test(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
) -> dict[str, Any]:
    """Non-UI smoke test: resolve config → verify capabilities → verify voice → verify state keys.

    Returns {"success": bool, "capabilities": list, "voice": str, "state_keys": list, "errors": list}.
    """
    from app.configs.registry_loader import (
        build_session_state_from_registry,
        resolve_registry_config,
    )

    try:
        config = await resolve_registry_config(db, tenant_id, company_id)
    except Exception as exc:
        return {"success": False, "errors": [f"resolve failed: {exc}"]}

    if config is None:
        return {
            "success": False,
            "errors": [f"could not resolve config for {tenant_id}/{company_id}"],
        }

    state = build_session_state_from_registry(config)

    return {
        "success": True,
        "capabilities": list(config.capabilities),
        "voice": config.voice,
        "state_keys": list(state.keys()),
        "errors": [],
    }


# ═══ seed-all ═══


def _tenant_id_from_company_fixture(
    root: Path,
    company_id: str,
) -> str:
    """Read the tenant_id from a company fixture file, defaulting to 'public'."""
    company_fixture = root / "companies" / f"{company_id}.json"
    if company_fixture.exists():
        with open(company_fixture) as cf:
            return json.load(cf).get("tenant_id", "public")
    return "public"


def seed_all(
    db: Any,
    data_dir: str | Path = "tests/fixtures/registry",
    *,
    include_runtime_data: bool = False,
) -> dict[str, Any]:
    """Seed all templates and companies from a data directory.

    Reads ``{data_dir}/templates/*.json`` then ``{data_dir}/companies/*.json``.
    Templates are seeded first so that company provisioning can verify references.
    Default path uses the git-tracked ``tests/fixtures/registry`` for reproducibility.

    When ``include_runtime_data=True``, also seeds products, booking_slots, and
    knowledge entries from their respective subdirectories. Runtime data fixtures
    should be tagged with ``data_tier: "demo"`` for traceability.

    Returns {"templates": {...}, "companies": {...}, "products": {...},
             "booking_slots": {...}, "knowledge": {...}, "errors": list[str]}.
    """
    root = Path(data_dir)
    errors: list[str] = []
    template_ops = _new_write_ops()
    company_ops = _new_write_ops()
    product_ops = _new_write_ops()
    slot_ops = _new_write_ops()
    knowledge_ops = _new_write_ops()

    # --- templates ---
    templates_dir = root / "templates"
    if templates_dir.is_dir():
        for fp in sorted(templates_dir.glob("*.json")):
            with open(fp) as f:
                template = json.load(f)
            result = seed_templates(db, [template])
            for key in template_ops:
                template_ops[key] += result["operations"][key]
            errors.extend(result["errors"])
    else:
        errors.append(f"templates directory not found: {templates_dir}")

    # --- companies ---
    companies_dir = root / "companies"
    if companies_dir.is_dir():
        for fp in sorted(companies_dir.glob("*.json")):
            with open(fp) as f:
                company_data = json.load(f)
            result = provision_company(db, company_data)
            op = result.get("operation", "failed")
            if op in company_ops:
                company_ops[op] += 1
            errors.extend(result.get("errors", []))
    else:
        errors.append(f"companies directory not found: {companies_dir}")

    if include_runtime_data:
        # --- products ---
        products_dir = root / "products"
        if products_dir.is_dir():
            for fp in sorted(products_dir.glob("*.json")):
                company_id = fp.stem
                with open(fp) as f:
                    products = json.load(f)
                if not isinstance(products, list):
                    products = [products]
                tenant_id = _tenant_id_from_company_fixture(root, company_id)
                result = import_products(
                    db,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    products=products,
                )
                for key in product_ops:
                    product_ops[key] += result["operations"][key]
                errors.extend(result["errors"])

        # --- booking_slots ---
        slots_dir = root / "booking_slots"
        if slots_dir.is_dir():
            for fp in sorted(slots_dir.glob("*.json")):
                company_id = fp.stem
                with open(fp) as f:
                    slots = json.load(f)
                if not isinstance(slots, list):
                    slots = [slots]
                tenant_id = _tenant_id_from_company_fixture(root, company_id)
                result = import_booking_slots(
                    db,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    slots=slots,
                )
                for key in slot_ops:
                    slot_ops[key] += result["operations"][key]
                errors.extend(result["errors"])

        # --- knowledge ---
        knowledge_dir = root / "knowledge"
        if knowledge_dir.is_dir():
            for fp in sorted(knowledge_dir.glob("*.json")):
                company_id = fp.stem
                with open(fp) as f:
                    entries = json.load(f)
                if not isinstance(entries, list):
                    entries = [entries]
                tenant_id = _tenant_id_from_company_fixture(root, company_id)
                result = import_knowledge(
                    db,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    entries=entries,
                )
                for key in knowledge_ops:
                    knowledge_ops[key] += result["operations"][key]
                errors.extend(result["errors"])

    return {
        "templates": template_ops,
        "companies": company_ops,
        "products": product_ops,
        "booking_slots": slot_ops,
        "knowledge": knowledge_ops,
        "errors": errors,
    }


# ═══ CLI Entry Point ═══


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="registry",
        description="Provisioning CLI for the multi-tenant industry registry.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # seed-templates
    sp = subparsers.add_parser("seed-templates", help="Seed industry templates")
    sp.add_argument("--file", required=True, help="JSON file with template array")

    # provision-company
    sp = subparsers.add_parser("provision-company", help="Provision a company")
    sp.add_argument("--tenant", required=True)
    sp.add_argument("--company", required=True)
    sp.add_argument("--template", required=True)
    sp.add_argument("--file", help="JSON file with full company data")

    # import-knowledge
    sp = subparsers.add_parser("import-knowledge", help="Import knowledge entries")
    sp.add_argument("--tenant", required=True)
    sp.add_argument("--company", required=True)
    sp.add_argument("--file", required=True, help="JSON file with knowledge array")

    # validate
    sp = subparsers.add_parser("validate", help="Validate registry consistency")
    sp.add_argument("--tenant", default="public")

    # smoke
    sp = subparsers.add_parser("smoke", help="Run smoke test")
    sp.add_argument("--tenant", required=True)
    sp.add_argument("--company", required=True)

    # import-products
    sp = subparsers.add_parser("import-products", help="Import product catalog items")
    sp.add_argument("--tenant", required=True)
    sp.add_argument("--company", required=True)
    sp.add_argument("--file", required=True, help="JSON file with product array")

    # import-booking-slots
    sp = subparsers.add_parser("import-booking-slots", help="Import booking slots")
    sp.add_argument("--tenant", required=True)
    sp.add_argument("--company", required=True)
    sp.add_argument("--file", required=True, help="JSON file with slot array")

    # purge-demo-data
    sp = subparsers.add_parser(
        "purge-demo-data",
        help="Delete all data_tier=demo documents from runtime subcollections",
    )
    sp.add_argument("--tenant", default="public")

    # seed-all
    sp = subparsers.add_parser("seed-all", help="Seed all templates and companies")
    sp.add_argument(
        "--data-dir",
        default="tests/fixtures/registry",
        help="Path to registry data (default: tracked fixtures)",
    )
    sp.add_argument(
        "--include-runtime-data",
        action="store_true",
        default=False,
        help="Also seed products, booking_slots, and knowledge (demo data)",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    import os

    from dotenv import load_dotenv

    load_dotenv()

    parser = _create_parser()
    args = parser.parse_args(argv)

    from google.cloud import firestore

    db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT", "ekaette"))

    if args.command == "seed-templates":
        with open(args.file) as f:
            templates = json.load(f)
        if not isinstance(templates, list):
            templates = [templates]
        result = seed_templates(db, templates)
        print(json.dumps(result, indent=2))

    elif args.command == "provision-company":
        if args.file:
            with open(args.file) as f:
                company_data = json.load(f)
        else:
            company_data = {
                "company_id": args.company,
                "tenant_id": args.tenant,
                "industry_template_id": args.template,
                "schema_version": REGISTRY_SCHEMA_VERSION,
            }
        result = provision_company(db, company_data)
        print(json.dumps(result, indent=2))

    elif args.command == "import-knowledge":
        with open(args.file) as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            entries = [entries]
        result = import_knowledge(
            db,
            tenant_id=args.tenant,
            company_id=args.company,
            entries=entries,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "validate":
        result = validate_registry(db, tenant_id=args.tenant)
        print(json.dumps(result, indent=2))
        if result["errors"]:
            sys.exit(1)

    elif args.command == "smoke":
        result = asyncio.run(smoke_test(db, tenant_id=args.tenant, company_id=args.company))
        print(json.dumps(result, indent=2))
        if not result.get("success"):
            sys.exit(1)

    elif args.command == "import-products":
        with open(args.file) as f:
            products = json.load(f)
        if not isinstance(products, list):
            products = [products]
        result = import_products(
            db,
            tenant_id=args.tenant,
            company_id=args.company,
            products=products,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "import-booking-slots":
        with open(args.file) as f:
            slots = json.load(f)
        if not isinstance(slots, list):
            slots = [slots]
        result = import_booking_slots(
            db,
            tenant_id=args.tenant,
            company_id=args.company,
            slots=slots,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "purge-demo-data":
        result = purge_demo_data(db, tenant_id=args.tenant)
        print(json.dumps(result, indent=2))

    elif args.command == "seed-all":
        result = seed_all(
            db,
            data_dir=args.data_dir,
            include_runtime_data=args.include_runtime_data,
        )
        print(json.dumps(result, indent=2))
        if result["errors"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
