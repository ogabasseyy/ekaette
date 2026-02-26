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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from app.configs.registry_schema import (
    validate_capability_overrides,
    validate_company,
    validate_knowledge_entry,
    validate_template,
    validate_theme,
)


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

    for template in templates:
        validation_errors = validate_template(template)
        if validation_errors:
            template_id = template.get("id", "<unknown>")
            errors.append(f"template '{template_id}': {'; '.join(validation_errors)}")
            continue

        template_id = template["id"]
        doc_ref = db.collection("industry_templates").document(template_id)
        doc_ref.set(dict(template))
        written += 1

    return {"written": written, "errors": errors}


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
    normalized_company.setdefault("overview", "")
    normalized_company.setdefault("facts", {})
    normalized_company.setdefault("links", [])
    normalized_company.setdefault("connectors", {})
    normalized_company.setdefault("capability_overrides", {})
    normalized_company.setdefault("ui_overrides", {})
    normalized_company.setdefault("status", "active")

    validation_errors = validate_company(normalized_company)
    if validation_errors:
        return {"success": False, "errors": validation_errors}

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
        }

    # Validate capability overrides if present
    template_data = template_doc.to_dict() if hasattr(template_doc, "to_dict") else {}
    template_caps = template_data.get("capabilities", [])
    if isinstance(template_caps, list):
        cap_overrides = normalized_company.get("capability_overrides")
        if cap_overrides:
            cap_errors = validate_capability_overrides(cap_overrides, template_caps)
            if cap_errors:
                return {"success": False, "errors": cap_errors}

    doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
    )
    doc_ref.set(normalized_company)

    return {"success": True, "errors": []}


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

    for entry in entries:
        validation_errors = validate_knowledge_entry(entry)
        if validation_errors:
            entry_id = entry.get("id", "<unknown>")
            errors.append(f"entry '{entry_id}': {'; '.join(validation_errors)}")
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
        doc_ref.set(dict(entry))
        written += 1

    return {"written": written, "errors": errors}


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


if __name__ == "__main__":
    main()
