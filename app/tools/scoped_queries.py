"""Tenant/company-scoped Firestore collection helper.

All runtime tool queries should use these helpers instead of
accessing global Firestore collections directly.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _tenant_id_from_context(tool_context: Any) -> str | None:
    """Extract tenant_id from tool context state."""
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    if not isinstance(state, dict):
        return None
    value = state.get("app:tenant_id")
    return value if isinstance(value, str) and value.strip() else None


def _company_id_from_context(tool_context: Any) -> str | None:
    """Extract company_id from tool context state."""
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    if not isinstance(state, dict):
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

    return (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
        .collection(subcollection)
    )


def scoped_collection_or_global(
    db: Any,
    tool_context: Any,
    subcollection: str,
) -> Any | None:
    """Return scoped collection, falling back to global if tenant not set.

    This is the migration-safe path: when tenant/company are in session
    state, queries are scoped. Otherwise, falls back to the legacy global
    collection for backward compatibility.
    """
    if db is None:
        return None

    result = scoped_collection(db, tool_context, subcollection)
    if result is not None:
        return result

    # Fallback to global (legacy/compat)
    logger.debug(
        "scoped_collection fallback to global collection=%s",
        subcollection,
    )
    return db.collection(subcollection)
