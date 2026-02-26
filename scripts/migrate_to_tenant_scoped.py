"""One-time data migration from flat Firestore collections to tenant-scoped paths.

Migrates:
  company_profiles/{id}  →  tenants/{tenant}/companies/{id}
  company_knowledge (flat, keyed by company_id)  →  tenants/{t}/companies/{c}/knowledge/{id}
  products (flat)  →  tenants/{t}/companies/{c}/catalog_items/{id}
  booking_slots (flat)  →  tenants/{t}/companies/{c}/booking_slots/{id}

Strategy: read old → write new (or preview with --dry-run).
Default tenant: 'public' (for existing data).
Idempotent: skips docs that already exist at the target path.

Usage:
  python -m scripts.migrate_to_tenant_scoped --tenant=public
  python -m scripts.migrate_to_tenant_scoped --tenant=public --company=ekaette-electronics
  python -m scripts.migrate_to_tenant_scoped --tenant=public --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


# ═══ Company Profile Migration ═══


def migrate_company_profiles(
    db: Any,
    *,
    tenant_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate company_profiles/{id} → tenants/{tenant}/companies/{id}.

    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    migrated = 0
    skipped = 0
    errors: list[str] = []

    source_docs = db.collection("company_profiles").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            errors.append("company profile doc has no ID")
            continue

        # Must have industry field to determine template
        industry = data.get("industry", "")
        if not isinstance(industry, str) or not industry.strip():
            errors.append(f"company '{doc_id}' missing 'industry' field — cannot determine template")
            continue

        # Check if target already exists (idempotent)
        target_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(doc_id)
        )
        target_doc = target_ref.get()
        if getattr(target_doc, "exists", False):
            skipped += 1
            continue

        # Transform to tenant-scoped shape
        migrated_data: dict[str, Any] = {
            "company_id": doc_id,
            "tenant_id": tenant_id,
            "industry_template_id": industry.strip().lower(),
            "display_name": data.get("name", doc_id),
            "overview": data.get("overview", ""),
            "facts": data.get("facts", {}),
            "links": data.get("links", []),
            "connectors": data.get("system_connectors", {}),
            "capability_overrides": {},
            "ui_overrides": {},
            "status": "active",
        }

        if not dry_run:
            target_ref.set(migrated_data)
        migrated += 1

    return {"migrated": migrated, "skipped": skipped, "errors": errors}


# ═══ Company Knowledge Migration ═══


def migrate_company_knowledge(
    db: Any,
    *,
    tenant_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate company_knowledge (flat) → tenants/{t}/companies/{c}/knowledge/{id}.

    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    migrated = 0
    skipped = 0
    errors: list[str] = []

    source_docs = db.collection("company_knowledge").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            errors.append("knowledge doc has no ID")
            continue

        company_id = data.get("company_id", "")
        if not isinstance(company_id, str) or not company_id.strip():
            errors.append(f"knowledge '{doc_id}' missing 'company_id'")
            continue

        # Check if target exists (idempotent)
        target_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id.strip())
            .collection("knowledge")
            .document(doc_id)
        )
        target_doc = target_ref.get()
        if getattr(target_doc, "exists", False):
            skipped += 1
            continue

        if not dry_run:
            target_ref.set(dict(data))
        migrated += 1

    return {"migrated": migrated, "skipped": skipped, "errors": errors}


# ═══ Products Migration ═══


def migrate_products(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate products (flat) → tenants/{t}/companies/{c}/catalog_items/{id}.

    All products are assigned to the specified company.
    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    migrated = 0
    skipped = 0
    errors: list[str] = []

    source_docs = db.collection("products").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            errors.append("product doc has no ID")
            continue

        target_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection("catalog_items")
            .document(doc_id)
        )
        target_doc = target_ref.get()
        if getattr(target_doc, "exists", False):
            skipped += 1
            continue

        if not dry_run:
            target_ref.set(dict(data))
        migrated += 1

    return {"migrated": migrated, "skipped": skipped, "errors": errors}


# ═══ Booking Slots Migration ═══


def migrate_booking_slots(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate booking_slots (flat) → tenants/{t}/companies/{c}/booking_slots/{id}.

    All booking slots are assigned to the specified company.
    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    migrated = 0
    skipped = 0
    errors: list[str] = []

    source_docs = db.collection("booking_slots").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            errors.append("booking slot doc has no ID")
            continue

        target_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection("booking_slots")
            .document(doc_id)
        )
        target_doc = target_ref.get()
        if getattr(target_doc, "exists", False):
            skipped += 1
            continue

        if not dry_run:
            target_ref.set(dict(data))
        migrated += 1

    return {"migrated": migrated, "skipped": skipped, "errors": errors}


# ═══ CLI Entry Point ═══


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for data migration."""
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="migrate_to_tenant_scoped",
        description="Migrate flat Firestore collections to tenant-scoped paths.",
    )
    parser.add_argument("--tenant", default="public", help="Target tenant ID")
    parser.add_argument("--company", help="Target company ID (for products/slots)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument(
        "--collections",
        nargs="+",
        default=["profiles", "knowledge", "products", "slots"],
        choices=["profiles", "knowledge", "products", "slots"],
        help="Which collections to migrate",
    )

    args = parser.parse_args(argv)

    from google.cloud import firestore

    db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT", "ekaette"))

    results: dict[str, Any] = {}

    if "profiles" in args.collections:
        result = migrate_company_profiles(db, tenant_id=args.tenant, dry_run=args.dry_run)
        results["profiles"] = result
        print(f"Profiles: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")

    if "knowledge" in args.collections:
        result = migrate_company_knowledge(db, tenant_id=args.tenant, dry_run=args.dry_run)
        results["knowledge"] = result
        print(f"Knowledge: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")

    if "products" in args.collections:
        if not args.company:
            print("ERROR: --company required for products migration")
            sys.exit(1)
        result = migrate_products(
            db, tenant_id=args.tenant, company_id=args.company, dry_run=args.dry_run
        )
        results["products"] = result
        print(f"Products: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")

    if "slots" in args.collections:
        if not args.company:
            print("ERROR: --company required for booking slots migration")
            sys.exit(1)
        result = migrate_booking_slots(
            db, tenant_id=args.tenant, company_id=args.company, dry_run=args.dry_run
        )
        results["slots"] = result
        print(f"Slots: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")

    total_errors = sum(len(r.get("errors", [])) for r in results.values())
    if total_errors > 0:
        print(f"\n{total_errors} total errors:")
        for section, result in results.items():
            for err in result.get("errors", []):
                print(f"  [{section}] {err}")
        sys.exit(1)

    if args.dry_run:
        print("\nDry run complete (no writes performed).")
    else:
        print("\nMigration complete!")


if __name__ == "__main__":
    main()
