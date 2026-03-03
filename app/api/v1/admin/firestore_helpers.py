"""Firestore document CRUD helpers — async wrappers for sync/async Firestore APIs.

Extracted from main.py as Phase A2 of modularization.  These are pure wrappers
with no dependencies on main.py — they only use asyncio from stdlib.
"""

from __future__ import annotations

import asyncio


async def _doc_get(doc_ref: object) -> object:
    get_fn = getattr(doc_ref, "get", None)
    if get_fn is None:
        raise RuntimeError("Document reference has no get()")
    if asyncio.iscoroutinefunction(get_fn):
        return await get_fn()
    result = await asyncio.to_thread(get_fn)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def _doc_set(doc_ref: object, payload: dict[str, object], *, merge: bool = True) -> None:
    set_fn = getattr(doc_ref, "set", None)
    if set_fn is None:
        raise RuntimeError("Document reference has no set()")
    if asyncio.iscoroutinefunction(set_fn):
        await set_fn(payload, merge=merge)
        return
    await asyncio.to_thread(set_fn, payload, merge)


async def _doc_create(doc_ref: object, payload: dict[str, object]) -> None:
    create_fn = getattr(doc_ref, "create", None)
    if create_fn is None:
        raise RuntimeError("Document reference has no create()")
    if asyncio.iscoroutinefunction(create_fn):
        await create_fn(payload)
        return
    await asyncio.to_thread(create_fn, payload)


async def _doc_update(doc_ref: object, payload: dict[str, object]) -> None:
    update_fn = getattr(doc_ref, "update", None)
    if update_fn is None:
        raise RuntimeError("Document reference has no update()")
    if asyncio.iscoroutinefunction(update_fn):
        await update_fn(payload)
        return
    await asyncio.to_thread(update_fn, payload)


async def _doc_delete(doc_ref: object) -> None:
    delete_fn = getattr(doc_ref, "delete", None)
    if delete_fn is None:
        raise RuntimeError("Document reference has no delete()")
    if asyncio.iscoroutinefunction(delete_fn):
        await delete_fn()
        return
    await asyncio.to_thread(delete_fn)


async def _batch_set_documents(
    db: object,
    doc_payloads: list[tuple[object, dict[str, object]]],
    *,
    merge: bool = False,
) -> None:
    if not doc_payloads:
        return
    batch_fn = getattr(db, "batch", None)
    if batch_fn is None:
        for doc_ref, payload in doc_payloads:
            await _doc_set(doc_ref, payload, merge=merge)
        return
    batch = batch_fn()
    for doc_ref, payload in doc_payloads:
        batch.set(doc_ref, payload, merge=merge)
    commit_fn = getattr(batch, "commit", None)
    if commit_fn is None:
        return
    if asyncio.iscoroutinefunction(commit_fn):
        await commit_fn()
        return
    await asyncio.to_thread(commit_fn)


async def _batch_delete_documents(db: object, doc_refs: list[object]) -> None:
    if not doc_refs:
        return
    batch_fn = getattr(db, "batch", None)
    if batch_fn is None:
        for doc_ref in doc_refs:
            await _doc_delete(doc_ref)
        return
    batch = batch_fn()
    for doc_ref in doc_refs:
        batch.delete(doc_ref)
    commit_fn = getattr(batch, "commit", None)
    if commit_fn is None:
        return
    if asyncio.iscoroutinefunction(commit_fn):
        await commit_fn()
        return
    await asyncio.to_thread(commit_fn)
