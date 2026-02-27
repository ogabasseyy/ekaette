"""Data import/export/purge service functions.

Extracted from main.py as Phase B4 of modularization. Zero behavior changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from app.api.v1.admin.runtime import runtime as _m

from app.api.v1.admin.firestore_helpers import (
    _batch_delete_documents,
    _batch_set_documents,
    _doc_delete,
    _doc_get,
)
from app.api.v1.admin.service_companies import _admin_company_response
from app.api.v1.admin.service_knowledge import (
    _collect_query_docs,
    _list_company_collection_docs,
)


async def _import_company_runtime_docs(
    *,
    tenant_id: str,
    company_id: str,
    collection_name: str,
    items: list[dict[str, object]],
    data_tier: str,
    validator: object,
) -> dict[str, object]:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")

    operations = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    errors: list[str] = []
    doc_payloads: list[tuple[object, dict[str, object]]] = []
    validate = validator if callable(validator) else None

    for item in items:
        if not isinstance(item, dict):
            operations["failed"] += 1
            errors.append("entry: item must be an object")
            continue
        normalized = dict(item)
        entry_id = str(normalized.get("id", "")).strip()
        if not entry_id:
            operations["failed"] += 1
            errors.append("entry: missing required field 'id'")
            continue
        if "data_tier" not in normalized:
            normalized["data_tier"] = data_tier
        validation_errors = validate(normalized) if validate else []
        if validation_errors:
            operations["failed"] += 1
            errors.append(f"entry '{entry_id}': {'; '.join(validation_errors)}")
            continue

        doc_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection(collection_name)
            .document(entry_id)
        )
        existing_snapshot = await _doc_get(doc_ref)
        existing_data = (
            existing_snapshot.to_dict()
            if getattr(existing_snapshot, "exists", False) and hasattr(existing_snapshot, "to_dict")
            else {}
        )
        if existing_data == normalized:
            operations["unchanged"] += 1
            continue
        if getattr(existing_snapshot, "exists", False):
            operations["updated"] += 1
        else:
            operations["created"] += 1
        doc_payloads.append((doc_ref, normalized))

    for start in range(0, len(doc_payloads), 500):
        chunk = doc_payloads[start : start + 500]
        await _batch_set_documents(db, chunk, merge=False)

    return {"written": len(doc_payloads), "operations": operations, "errors": errors}


async def _import_company_products(
    *,
    tenant_id: str,
    company_id: str,
    products: list[dict[str, object]],
    data_tier: str = "admin",
) -> dict[str, object]:
    from app.configs.registry_schema import validate_product

    return await _import_company_runtime_docs(
        tenant_id=tenant_id,
        company_id=company_id,
        collection_name="products",
        items=products,
        data_tier=data_tier,
        validator=validate_product,
    )


async def _import_company_booking_slots(
    *,
    tenant_id: str,
    company_id: str,
    slots: list[dict[str, object]],
    data_tier: str = "admin",
) -> dict[str, object]:
    from app.configs.registry_schema import validate_booking_slot

    return await _import_company_runtime_docs(
        tenant_id=tenant_id,
        company_id=company_id,
        collection_name="booking_slots",
        items=slots,
        data_tier=data_tier,
        validator=validate_booking_slot,
    )


async def _purge_company_demo_runtime_data(
    *,
    tenant_id: str,
    company_id: str,
) -> dict[str, int]:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")

    deleted = {"products": 0, "booking_slots": 0, "knowledge": 0}
    for subcollection in ("products", "booking_slots", "knowledge"):
        query = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection(subcollection)
            .where("data_tier", "==", "demo")
        )
        docs = await _collect_query_docs(query)
        doc_refs: list[object] = []
        for doc in docs:
            doc_ref = getattr(doc, "reference", None)
            if doc_ref is None:
                continue
            doc_refs.append(doc_ref)
        for start in range(0, len(doc_refs), 500):
            chunk = doc_refs[start : start + 500]
            await _batch_delete_documents(db, chunk)
            deleted[subcollection] += len(chunk)
    return deleted


def _parse_timestamp_utc(raw_value: object) -> datetime | None:
    if not isinstance(raw_value, str):
        return None
    value = raw_value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _export_company_bundle(
    *,
    tenant_id: str,
    company_id: str,
    company_doc: dict[str, object],
    include_runtime_data: bool,
) -> dict[str, object]:
    knowledge_entries = await _list_company_collection_docs(
        tenant_id=tenant_id,
        company_id=company_id,
        collection_name="knowledge",
    )
    products: list[dict[str, object]] = []
    booking_slots: list[dict[str, object]] = []
    if include_runtime_data:
        products = await _list_company_collection_docs(
            tenant_id=tenant_id,
            company_id=company_id,
            collection_name="products",
        )
        booking_slots = await _list_company_collection_docs(
            tenant_id=tenant_id,
            company_id=company_id,
            collection_name="booking_slots",
        )
    return {
        "company": _admin_company_response(
            tenant_id=tenant_id,
            company_id=company_id,
            raw_company=company_doc,
        ),
        "collections": {
            "knowledge": knowledge_entries,
            "products": products,
            "booking_slots": booking_slots,
        },
        "counts": {
            "knowledge": len(knowledge_entries),
            "products": len(products),
            "booking_slots": len(booking_slots),
        },
    }


async def _delete_company_bundle(
    *,
    tenant_id: str,
    company_id: str,
) -> dict[str, int]:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")

    deleted_counts = {"knowledge": 0, "products": 0, "booking_slots": 0, "company": 0}
    company_doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
    )
    for subcollection in ("knowledge", "products", "booking_slots"):
        collection_ref = company_doc_ref.collection(subcollection)
        docs = await _collect_query_docs(collection_ref)
        doc_refs: list[object] = []
        for doc in docs:
            doc_ref = getattr(doc, "reference", None)
            if doc_ref is None:
                continue
            doc_refs.append(doc_ref)
        for start in range(0, len(doc_refs), 500):
            chunk = doc_refs[start : start + 500]
            await _batch_delete_documents(db, chunk)
            deleted_counts[subcollection] += len(chunk)

    snapshot = await _doc_get(company_doc_ref)
    if getattr(snapshot, "exists", False):
        await _doc_delete(company_doc_ref)
        deleted_counts["company"] = 1
    return deleted_counts


async def _purge_company_retention_data(
    *,
    tenant_id: str,
    company_id: str,
    older_than_days: int,
    collections: list[str],
    data_tier: str | None = None,
) -> dict[str, dict[str, int]]:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, older_than_days))
    target_tier = (data_tier or "").strip().lower() or None
    report: dict[str, dict[str, int]] = {}
    company_doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
    )

    for collection_name in collections:
        counters = {"scanned": 0, "deleted": 0, "skipped": 0, "missing_timestamp": 0}
        collection_ref = company_doc_ref.collection(collection_name)
        docs = await _collect_query_docs(collection_ref)
        deletable_refs: list[object] = []
        for doc in docs:
            counters["scanned"] += 1
            item = doc.to_dict() if hasattr(doc, "to_dict") else {}
            if not isinstance(item, dict):
                item = {}
            if target_tier:
                item_tier = str(item.get("data_tier", "")).strip().lower()
                if item_tier != target_tier:
                    counters["skipped"] += 1
                    continue
            timestamp = _parse_timestamp_utc(item.get("updated_at")) or _parse_timestamp_utc(
                item.get("created_at")
            )
            if timestamp is None:
                counters["missing_timestamp"] += 1
                counters["skipped"] += 1
                continue
            if timestamp >= cutoff:
                counters["skipped"] += 1
                continue
            doc_ref = getattr(doc, "reference", None)
            if doc_ref is None:
                counters["skipped"] += 1
                continue
            deletable_refs.append(doc_ref)
        for start in range(0, len(deletable_refs), 500):
            chunk = deletable_refs[start : start + 500]
            await _batch_delete_documents(db, chunk)
            counters["deleted"] += len(chunk)
        report[collection_name] = counters
    return report
