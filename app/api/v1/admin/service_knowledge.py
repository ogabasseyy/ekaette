"""Knowledge entry service functions — write, delete, list, query.

Extracted from main.py as Phase B2 of modularization. Zero behavior changes.
"""

from __future__ import annotations

import asyncio
import inspect

from app.api.v1.admin.runtime import runtime as _m

from app.api.v1.admin.firestore_helpers import _doc_delete, _doc_get, _doc_set


def _normalize_tags(raw_tags: object, *, default_tag: str = "general") -> list[str]:
    if isinstance(raw_tags, list):
        tags = [str(tag).strip().lower() for tag in raw_tags if str(tag).strip()]
    else:
        tags = []
    return tags or [default_tag]


async def _write_company_knowledge_entry(
    *,
    tenant_id: str,
    company_id: str,
    knowledge_id: str,
    entry: dict[str, object],
) -> None:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")
    doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
        .collection("knowledge")
        .document(knowledge_id)
    )
    await _doc_set(doc_ref, entry, merge=True)


async def _delete_company_knowledge_entry(
    *,
    tenant_id: str,
    company_id: str,
    knowledge_id: str,
) -> bool:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")
    doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
        .collection("knowledge")
        .document(knowledge_id)
    )
    snapshot = await _doc_get(doc_ref)
    if not getattr(snapshot, "exists", False):
        return False
    await _doc_delete(doc_ref)
    return True


async def _collect_query_docs(query: object) -> list[object]:
    stream_fn = getattr(query, "stream", None)
    if stream_fn is None:
        return []
    stream_result = stream_fn()
    if inspect.isawaitable(stream_result):
        stream_result = await stream_result
    if hasattr(stream_result, "__aiter__"):
        return [doc async for doc in stream_result]
    return await asyncio.to_thread(lambda: list(stream_result))


async def _list_company_collection_docs(
    *,
    tenant_id: str,
    company_id: str,
    collection_name: str,
) -> list[dict[str, object]]:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")
    collection_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
        .collection(collection_name)
    )
    docs = await _collect_query_docs(collection_ref)
    normalized: list[dict[str, object]] = []
    for doc in docs:
        item = doc.to_dict() if hasattr(doc, "to_dict") else {}
        if not isinstance(item, dict):
            item = {}
        doc_id = getattr(doc, "id", None)
        normalized_id = str(doc_id).strip() if isinstance(doc_id, str) else str(item.get("id", "")).strip()
        if normalized_id and "id" not in item:
            item["id"] = normalized_id
        normalized.append(item)
    return normalized
