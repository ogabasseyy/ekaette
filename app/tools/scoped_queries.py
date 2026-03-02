"""Tenant/company-scoped Firestore collection helper.

All runtime tool queries should use these helpers instead of
accessing global Firestore collections directly.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _is_mapping_like(obj: Any) -> bool:
    """Check if *obj* supports dict-like `get` / `__contains__` (duck-typing).

    ADK's ``State`` object is **not** a ``dict`` subclass, so
    ``isinstance(state, dict)`` silently returns ``False`` and breaks
    scoped queries.  This helper accepts any mapping-like object.
    """
    return obj is not None and hasattr(obj, "get") and hasattr(obj, "__contains__")


def _tenant_id_from_context(tool_context: Any) -> str | None:
    """Extract tenant_id from tool context state."""
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    if not _is_mapping_like(state):
        return None
    value = state.get("app:tenant_id")
    return value if isinstance(value, str) and value.strip() else None


def _company_id_from_context(tool_context: Any) -> str | None:
    """Extract company_id from tool context state."""
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    if not _is_mapping_like(state):
        return None
    value = state.get("app:company_id")
    return value if isinstance(value, str) and value.strip() else None


def scoped_collection(
    db: Any,
    tool_context: Any,
    subcollection: str,
) -> Any | None:
    """Return a tenant/company-scoped Firestore collection reference.

    Path: tenants/{tenant_id}/companies/{company_id}/{subcollection}

    Returns None if db, tenant_id, or company_id is missing.
    """
    if db is None:
        return None

    tenant_id = _tenant_id_from_context(tool_context)
    if not tenant_id:
        return None

    company_id = _company_id_from_context(tool_context)
    if not company_id:
        return None

    # Normalize IDs to prevent path inconsistencies from whitespace.
    return (
        db.collection("tenants")
        .document(tenant_id.strip())
        .collection("companies")
        .document(company_id.strip())
        .collection(subcollection)
    )


def scoped_collection_or_global(
    db: Any,
    tool_context: Any,
    subcollection: str,
) -> Any | None:
    """Return scoped collection, falling back to global only in true compat mode.

    This is the migration-safe path: when tenant/company are in session
    state, queries are scoped. If canonical scoping keys are partially present
    (for example tenant_id without company_id), fail closed to avoid accidental
    cross-tenant/global queries. Only when *no* canonical scoping keys are
    present do we fall back to the legacy global collection.
    """
    if db is None:
        return None

    result = scoped_collection(db, tool_context, subcollection)
    if result is not None:
        return result

    state = getattr(tool_context, "state", None)
    if _is_mapping_like(state):
        has_tenant_key = "app:tenant_id" in state
        has_company_key = "app:company_id" in state
        if has_tenant_key or has_company_key:
            logger.warning(
                "scoped_collection fail-closed due partial canonical scope "
                "collection=%s has_tenant_key=%s has_company_key=%s",
                subcollection,
                has_tenant_key,
                has_company_key,
            )
            return None

    # Fallback to global (legacy/compat)
    logger.debug(
        "scoped_collection fallback to global collection=%s",
        subcollection,
    )
    return db.collection(subcollection)
