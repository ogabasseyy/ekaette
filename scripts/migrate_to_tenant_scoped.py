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
from pathlib import Path
from typing import Any

from app.configs import REGISTRY_SCHEMA_VERSION

_SAMPLE_ID_LIMIT = 5


def _new_migration_result(
    *,
    source_collection_path: str,
    target_collection_path: str,
) -> dict[str, Any]:
    return {
        "migrated": 0,
        "skipped": 0,
        "errors": [],
        "processed_ids": [],
        "summary": {
            "sourceCollectionPath": source_collection_path,
            "targetCollectionPath": target_collection_path,
            "operations": {
                "create": 0,
                "already_exists": 0,
                "resume_skipped": 0,
                "invalid_source": 0,
            },
            "sampleIds": {
                "create": [],
                "already_exists": [],
                "resume_skipped": [],
                "invalid_source": [],
            },
        },
    }


def _record_result_op(result: dict[str, Any], op: str, doc_id: str) -> None:
    summary = result.setdefault("summary", {})
    ops = summary.setdefault("operations", {})
    samples = summary.setdefault("sampleIds", {})
    ops[op] = int(ops.get(op, 0)) + 1
    op_samples = samples.setdefault(op, [])
    if isinstance(op_samples, list) and len(op_samples) < _SAMPLE_ID_LIMIT:
        op_samples.append(doc_id)


def _normalized_resume_ids(resume_processed_ids: set[str] | None) -> set[str]:
    return set(resume_processed_ids or set())


def _load_checkpoint(
    path: Path,
    *,
    expected_tenant_id: str | None = None,
    expected_company_id: str | None = None,
) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return {}
    meta = data.get("meta")
    if isinstance(meta, dict):
        tenant_in_file = meta.get("tenant_id")
        company_in_file = meta.get("company_id")
        if (
            isinstance(expected_tenant_id, str)
            and isinstance(tenant_in_file, str)
            and tenant_in_file != expected_tenant_id
        ):
            raise ValueError(
                f"checkpoint tenant_id mismatch (expected {expected_tenant_id}, found {tenant_in_file})"
            )
        # Only enforce company match when caller provided a company.
        if (
            isinstance(expected_company_id, str)
            and expected_company_id
            and isinstance(company_in_file, str)
            and company_in_file != expected_company_id
        ):
            raise ValueError(
                f"checkpoint company_id mismatch (expected {expected_company_id}, found {company_in_file})"
            )
    completed = data.get("completed", data)
    if not isinstance(completed, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, values in completed.items():
        if isinstance(key, str) and isinstance(values, list):
            result[key] = [str(v) for v in values]
    return result


def _save_checkpoint(
    path: Path,
    completed: dict[str, list[str]],
    *,
    tenant_id: str | None = None,
    company_id: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "tenant_id": tenant_id or "",
            "company_id": company_id or "",
        },
        "completed": completed,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _merge_checkpoint_ids(existing: list[str], new_ids: list[str]) -> list[str]:
    merged = list(existing)
    seen = set(existing)
    for item in new_ids:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


# ═══ Company Profile Migration ═══


def migrate_company_profiles(
    db: Any,
    *,
    tenant_id: str,
    dry_run: bool = False,
    resume_processed_ids: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Migrate company_profiles/{id} → tenants/{tenant}/companies/{id}.

    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    result = _new_migration_result(
        source_collection_path="company_profiles",
        target_collection_path=f"tenants/{tenant_id}/companies",
    )
    resume_ids = _normalized_resume_ids(resume_processed_ids)

    source_docs = db.collection("company_profiles").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            result["errors"].append("company profile doc has no ID")
            continue
        if not force and doc_id in resume_ids:
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "resume_skipped", doc_id)
            continue

        # Must have industry field to determine template
        industry = data.get("industry", "")
        if not isinstance(industry, str) or not industry.strip():
            result["errors"].append(
                f"company '{doc_id}' missing 'industry' field — cannot determine template"
            )
            _record_result_op(result, "invalid_source", doc_id)
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
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "already_exists", doc_id)
            continue

        # Transform to tenant-scoped shape
        migrated_data: dict[str, Any] = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
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
        result["migrated"] += 1
        result["processed_ids"].append(doc_id)
        _record_result_op(result, "create", doc_id)

    return result


# ═══ Company Knowledge Migration ═══


def migrate_company_knowledge(
    db: Any,
    *,
    tenant_id: str,
    dry_run: bool = False,
    resume_processed_ids: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Migrate company_knowledge (flat) → tenants/{t}/companies/{c}/knowledge/{id}.

    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    result = _new_migration_result(
        source_collection_path="company_knowledge",
        target_collection_path=f"tenants/{tenant_id}/companies/{{company_id}}/knowledge",
    )
    resume_ids = _normalized_resume_ids(resume_processed_ids)

    source_docs = db.collection("company_knowledge").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            result["errors"].append("knowledge doc has no ID")
            continue
        if not force and doc_id in resume_ids:
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "resume_skipped", doc_id)
            continue

        company_id = data.get("company_id", "")
        if not isinstance(company_id, str) or not company_id.strip():
            result["errors"].append(f"knowledge '{doc_id}' missing 'company_id'")
            _record_result_op(result, "invalid_source", doc_id)
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
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "already_exists", doc_id)
            continue

        if not dry_run:
            target_ref.set(dict(data))
        result["migrated"] += 1
        result["processed_ids"].append(doc_id)
        _record_result_op(result, "create", doc_id)

    return result


# ═══ Products Migration ═══


def migrate_products(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    dry_run: bool = False,
    resume_processed_ids: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Migrate products (flat) → tenants/{t}/companies/{c}/catalog_items/{id}.

    All products are assigned to the specified company.
    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    result = _new_migration_result(
        source_collection_path="products",
        target_collection_path=f"tenants/{tenant_id}/companies/{company_id}/catalog_items",
    )
    resume_ids = _normalized_resume_ids(resume_processed_ids)

    source_docs = db.collection("products").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            result["errors"].append("product doc has no ID")
            continue
        if not force and doc_id in resume_ids:
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "resume_skipped", doc_id)
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
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "already_exists", doc_id)
            continue

        if not dry_run:
            target_ref.set(dict(data))
        result["migrated"] += 1
        result["processed_ids"].append(doc_id)
        _record_result_op(result, "create", doc_id)

    return result


# ═══ Booking Slots Migration ═══


def migrate_booking_slots(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    dry_run: bool = False,
    resume_processed_ids: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Migrate booking_slots (flat) → tenants/{t}/companies/{c}/booking_slots/{id}.

    All booking slots are assigned to the specified company.
    Returns {"migrated": int, "skipped": int, "errors": list[str]}.
    """
    result = _new_migration_result(
        source_collection_path="booking_slots",
        target_collection_path=f"tenants/{tenant_id}/companies/{company_id}/booking_slots",
    )
    resume_ids = _normalized_resume_ids(resume_processed_ids)

    source_docs = db.collection("booking_slots").stream()

    for doc in source_docs:
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        doc_id = getattr(doc, "id", "") or ""
        if not doc_id:
            result["errors"].append("booking slot doc has no ID")
            continue
        if not force and doc_id in resume_ids:
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "resume_skipped", doc_id)
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
            result["skipped"] += 1
            result["processed_ids"].append(doc_id)
            _record_result_op(result, "already_exists", doc_id)
            continue

        if not dry_run:
            target_ref.set(dict(data))
        result["migrated"] += 1
        result["processed_ids"].append(doc_id)
        _record_result_op(result, "create", doc_id)

    return result


def verify_migration(
    db: Any,
    *,
    tenant_id: str,
    company_id: str | None = None,
    collections: list[str] | None = None,
) -> dict[str, Any]:
    """Verify migrated target docs exist for selected source collections."""
    selected = set(collections or ["profiles", "knowledge", "products", "slots"])
    errors: list[str] = []
    checked = 0

    if "profiles" in selected:
        for doc in db.collection("company_profiles").stream():
            doc_id = getattr(doc, "id", "") or ""
            if not doc_id:
                continue
            checked += 1
            target = (
                db.collection("tenants")
                .document(tenant_id)
                .collection("companies")
                .document(doc_id)
                .get()
            )
            if not getattr(target, "exists", False):
                errors.append(f"[profiles] missing target tenants/{tenant_id}/companies/{doc_id}")

    if "knowledge" in selected:
        for doc in db.collection("company_knowledge").stream():
            data = doc.to_dict() if hasattr(doc, "to_dict") else {}
            doc_id = getattr(doc, "id", "") or ""
            src_company_id = str(data.get("company_id", "")).strip()
            if not doc_id or not src_company_id:
                continue
            checked += 1
            target = (
                db.collection("tenants")
                .document(tenant_id)
                .collection("companies")
                .document(src_company_id)
                .collection("knowledge")
                .document(doc_id)
                .get()
            )
            if not getattr(target, "exists", False):
                errors.append(
                    f"[knowledge] missing target tenants/{tenant_id}/companies/{src_company_id}/knowledge/{doc_id}"
                )

    if company_id and "products" in selected:
        for doc in db.collection("products").stream():
            doc_id = getattr(doc, "id", "") or ""
            if not doc_id:
                continue
            checked += 1
            target = (
                db.collection("tenants")
                .document(tenant_id)
                .collection("companies")
                .document(company_id)
                .collection("catalog_items")
                .document(doc_id)
                .get()
            )
            if not getattr(target, "exists", False):
                errors.append(
                    f"[products] missing target tenants/{tenant_id}/companies/{company_id}/catalog_items/{doc_id}"
                )

    if company_id and "slots" in selected:
        for doc in db.collection("booking_slots").stream():
            doc_id = getattr(doc, "id", "") or ""
            if not doc_id:
                continue
            checked += 1
            target = (
                db.collection("tenants")
                .document(tenant_id)
                .collection("companies")
                .document(company_id)
                .collection("booking_slots")
                .document(doc_id)
                .get()
            )
            if not getattr(target, "exists", False):
                errors.append(
                    f"[slots] missing target tenants/{tenant_id}/companies/{company_id}/booking_slots/{doc_id}"
                )

    return {"success": not errors, "checked": checked, "errors": errors}


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
    parser.add_argument("--verify", action="store_true", help="Verify target docs exist after migration")
    parser.add_argument("--resume", action="store_true", help="Resume using checkpoint file")
    parser.add_argument("--force", action="store_true", help="Ignore checkpoint resume skips")
    parser.add_argument(
        "--checkpoint-file",
        default=".data/migrate_to_tenant_scoped.checkpoint.json",
        help="Checkpoint file path for resume support",
    )
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
    checkpoint_path = Path(args.checkpoint_file)
    if args.resume:
        try:
            checkpoint_completed = _load_checkpoint(
                checkpoint_path,
                expected_tenant_id=args.tenant,
                expected_company_id=args.company,
            )
        except ValueError as exc:
            print(f"ERROR: invalid checkpoint file: {exc}")
            sys.exit(1)
    else:
        checkpoint_completed = {}

    def _resume_ids(section: str) -> set[str]:
        return set(checkpoint_completed.get(section, []))

    def _save_section_checkpoint(section: str, result: dict[str, Any]) -> None:
        processed = [str(i) for i in result.get("processed_ids", [])]
        checkpoint_completed[section] = _merge_checkpoint_ids(
            checkpoint_completed.get(section, []),
            processed,
        )
        _save_checkpoint(
            checkpoint_path,
            checkpoint_completed,
            tenant_id=args.tenant,
            company_id=args.company,
        )

    def _print_diff_summary(section: str, result: dict[str, Any]) -> None:
        summary = result.get("summary", {})
        if not isinstance(summary, dict):
            return
        print(
            json.dumps(
                {
                    "section": section,
                    "dryRun": bool(args.dry_run),
                    "sourceCollectionPath": summary.get("sourceCollectionPath"),
                    "targetCollectionPath": summary.get("targetCollectionPath"),
                    "operations": summary.get("operations", {}),
                    "sampleIds": summary.get("sampleIds", {}),
                },
                indent=2,
                sort_keys=True,
            )
        )

    if "profiles" in args.collections:
        result = migrate_company_profiles(
            db,
            tenant_id=args.tenant,
            dry_run=args.dry_run,
            resume_processed_ids=_resume_ids("profiles"),
            force=args.force,
        )
        results["profiles"] = result
        print(f"Profiles: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")
        _print_diff_summary("profiles", result)
        if args.resume:
            _save_section_checkpoint("profiles", result)

    if "knowledge" in args.collections:
        result = migrate_company_knowledge(
            db,
            tenant_id=args.tenant,
            dry_run=args.dry_run,
            resume_processed_ids=_resume_ids("knowledge"),
            force=args.force,
        )
        results["knowledge"] = result
        print(f"Knowledge: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")
        _print_diff_summary("knowledge", result)
        if args.resume:
            _save_section_checkpoint("knowledge", result)

    if "products" in args.collections:
        if not args.company:
            print("ERROR: --company required for products migration")
            sys.exit(1)
        result = migrate_products(
            db,
            tenant_id=args.tenant,
            company_id=args.company,
            dry_run=args.dry_run,
            resume_processed_ids=_resume_ids("products"),
            force=args.force,
        )
        results["products"] = result
        print(f"Products: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")
        _print_diff_summary("products", result)
        if args.resume:
            _save_section_checkpoint("products", result)

    if "slots" in args.collections:
        if not args.company:
            print("ERROR: --company required for booking slots migration")
            sys.exit(1)
        result = migrate_booking_slots(
            db,
            tenant_id=args.tenant,
            company_id=args.company,
            dry_run=args.dry_run,
            resume_processed_ids=_resume_ids("slots"),
            force=args.force,
        )
        results["slots"] = result
        print(f"Slots: migrated={result['migrated']} skipped={result.get('skipped', 0)} errors={len(result['errors'])}")
        _print_diff_summary("slots", result)
        if args.resume:
            _save_section_checkpoint("slots", result)

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
        if args.verify:
            verify_result = verify_migration(
                db,
                tenant_id=args.tenant,
                company_id=args.company,
                collections=list(args.collections),
            )
            print("\nVerification summary:")
            print(json.dumps(verify_result, indent=2, sort_keys=True))
            if not verify_result["success"]:
                sys.exit(1)
        print("\nMigration complete!")


if __name__ == "__main__":
    main()
