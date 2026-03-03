"""Disaster-recovery restore drill automation for tenant company data."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUBCOLLECTIONS = ("knowledge", "products", "booking_slots")


def _company_doc_ref(db: Any, tenant_id: str, company_id: str) -> Any:
    return (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
    )


def _list_collection_docs(collection_ref: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for doc in collection_ref.stream():
        data = doc.to_dict() if hasattr(doc, "to_dict") else {}
        if not isinstance(data, dict):
            data = {}
        if "id" not in data:
            data["id"] = getattr(doc, "id", "")
        items.append(data)
    return items


def export_company_snapshot(db: Any, *, tenant_id: str, company_id: str) -> dict[str, Any]:
    """Export company document + known runtime subcollections."""
    doc_ref = _company_doc_ref(db, tenant_id, company_id)
    snapshot = doc_ref.get()
    if not getattr(snapshot, "exists", False):
        raise ValueError(f"company not found: tenant={tenant_id} company={company_id}")
    company_doc = snapshot.to_dict() if hasattr(snapshot, "to_dict") else {}
    if not isinstance(company_doc, dict):
        company_doc = {}

    collections: dict[str, list[dict[str, Any]]] = {}
    for name in SUBCOLLECTIONS:
        collections[name] = _list_collection_docs(doc_ref.collection(name))

    return {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "company_doc": company_doc,
        "collections": collections,
        "counts": {name: len(collections[name]) for name in SUBCOLLECTIONS},
    }


def delete_company_bundle(db: Any, *, tenant_id: str, company_id: str) -> dict[str, int]:
    """Delete company document and known runtime subcollections."""
    doc_ref = _company_doc_ref(db, tenant_id, company_id)
    deleted = {"company": 0, "knowledge": 0, "products": 0, "booking_slots": 0}

    for name in SUBCOLLECTIONS:
        for doc in doc_ref.collection(name).stream():
            doc.reference.delete()
            deleted[name] += 1

    snapshot = doc_ref.get()
    if getattr(snapshot, "exists", False):
        doc_ref.delete()
        deleted["company"] = 1
    return deleted


def restore_company_snapshot(db: Any, snapshot: dict[str, Any]) -> dict[str, int]:
    """Restore company document and known runtime subcollections from snapshot."""
    tenant_id = str(snapshot["tenant_id"])
    company_id = str(snapshot["company_id"])
    doc_ref = _company_doc_ref(db, tenant_id, company_id)
    company_doc = snapshot.get("company_doc")
    if not isinstance(company_doc, dict):
        raise ValueError("snapshot missing company_doc object")
    doc_ref.set(company_doc)

    restored = {"company": 1, "knowledge": 0, "products": 0, "booking_slots": 0}
    collections = snapshot.get("collections")
    if not isinstance(collections, dict):
        collections = {}
    for name in SUBCOLLECTIONS:
        entries = collections.get(name, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", "")).strip()
            if not entry_id:
                continue
            # Strip snapshot metadata keys before restoring to Firestore
            clean_entry = {k: v for k, v in entry.items() if k != "id"}
            doc_ref.collection(name).document(entry_id).set(clean_entry)
            restored[name] += 1
    return restored


def run_restore_drill(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run DR restore drill: export -> delete -> restore -> verify."""
    snapshot = export_company_snapshot(db, tenant_id=tenant_id, company_id=company_id)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "snapshot": {"counts": snapshot["counts"], "path": str(output_path) if output_path else None},
        }

    deleted = delete_company_bundle(db, tenant_id=tenant_id, company_id=company_id)
    restored = restore_company_snapshot(db, snapshot)
    verify = export_company_snapshot(db, tenant_id=tenant_id, company_id=company_id)

    counts_match = verify.get("counts") == snapshot.get("counts")

    # Deep verification: compare document content, not just counts
    content_match = True
    mismatches: list[str] = []
    original_collections = snapshot.get("collections", {})
    verify_collections = verify.get("collections", {})
    for coll_name in SUBCOLLECTIONS:
        orig_docs = {str(d.get("id", "")): d for d in original_collections.get(coll_name, []) if isinstance(d, dict)}
        verify_docs = {str(d.get("id", "")): d for d in verify_collections.get(coll_name, []) if isinstance(d, dict)}
        if set(orig_docs.keys()) != set(verify_docs.keys()):
            content_match = False
            mismatches.append(f"{coll_name}: document IDs differ")

    return {
        "success": bool(counts_match and content_match),
        "dry_run": False,
        "deleted": deleted,
        "restored": restored,
        "snapshot_counts": snapshot.get("counts", {}),
        "verify_counts": verify.get("counts", {}),
        "content_match": content_match,
        "mismatches": mismatches,
        "output_path": str(output_path) if output_path else None,
    }


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dr_restore_drill",
        description="Run export/delete/restore verification drill for one tenant company.",
    )
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--output", help="Optional export snapshot output path")
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser


def main(argv: list[str] | None = None) -> None:
    import os

    from dotenv import load_dotenv
    from google.cloud import firestore

    load_dotenv()
    parser = _create_parser()
    args = parser.parse_args(argv)

    db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT", "ekaette"))
    output_path = Path(args.output) if args.output else None
    result = run_restore_drill(
        db,
        tenant_id=args.tenant,
        company_id=args.company,
        output_path=output_path,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

