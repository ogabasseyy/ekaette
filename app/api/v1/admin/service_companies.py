"""Company service functions — payload builders, registry CRUD.

Extracted from main.py as Phase B1 of modularization. Zero behavior changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from app.api.v1.admin.runtime import runtime as _m

from app.api.v1.admin.firestore_helpers import _doc_get, _doc_set


def _admin_company_payload(
    *,
    tenant_id: str,
    company_id: str,
    industry_template_id: str,
    display_name: str,
    spoken_name: str | None,
    status: str,
    connectors: dict[str, object],
    overview: str | None,
    facts: dict[str, object],
    links: list[str],
) -> dict[str, object]:
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "industry_template_id": industry_template_id,
        "display_name": display_name,
        "spoken_name": (spoken_name or "").strip(),
        "status": status,
        "connectors": connectors,
        "overview": (overview or "").strip(),
        "facts": facts if isinstance(facts, dict) else {},
        "links": [str(link).strip() for link in links if str(link).strip()],
        "updated_at": now_iso,
    }


def _admin_company_response(
    *,
    tenant_id: str,
    company_id: str,
    raw_company: dict[str, object] | None,
) -> dict[str, object]:
    company = raw_company if isinstance(raw_company, dict) else {}
    display_name = company.get("display_name") or company.get("name") or company_id
    spoken_name = company.get("spoken_name") or company.get("name") or display_name
    template_id = _m._normalize_template_id(company.get("industry_template_id")) or ""
    status = str(company.get("status") or "active").strip().lower() or "active"
    connectors = company.get("connectors")
    inventory_sync = company.get("inventory_sync")
    facts = company.get("facts")
    links = company.get("links")
    return {
        "id": company_id,
        "tenantId": tenant_id,
        "templateId": template_id,
        "displayName": str(display_name),
        "spokenName": str(spoken_name),
        "status": status,
        "connectors": connectors if isinstance(connectors, dict) else {},
        "inventorySync": inventory_sync if isinstance(inventory_sync, dict) else {},
        "overview": str(company.get("overview") or ""),
        "facts": facts if isinstance(facts, dict) else {},
        "links": links if isinstance(links, list) else [],
        "schemaVersion": int(company.get("schema_version") or 1),
        "updatedAt": company.get("updated_at"),
        "createdAt": company.get("created_at"),
    }


async def _load_registry_company_doc(
    *,
    tenant_id: str,
    company_id: str,
) -> tuple[dict[str, object] | None, JSONResponse | None]:
    db = _m._registry_db_client()
    try:
        from app.configs.registry_loader import load_tenant_company

        company_doc = await load_tenant_company(db, tenant_id, company_id)
    except _m.RegistrySchemaVersionError as exc:
        logger.warning(
            "Unsupported registry schema version %s",
            _m.registry_log_context(
                tenant_id=tenant_id,
                company_id=company_id,
                registry_mode=_m._registry_enabled(),
                source="api_v1_admin_company_lookup",
            ),
        )
        return None, JSONResponse(
            status_code=503,
            content={
                "error": "Unsupported registry schema version",
                "code": getattr(exc, "code", "REGISTRY_SCHEMA_VERSION_UNSUPPORTED"),
                "tenantId": tenant_id,
                "companyId": company_id,
            },
        )
    except Exception as exc:
        logger.warning(
            "Admin company lookup failed %s details=%s",
            _m.registry_log_context(
                tenant_id=tenant_id,
                company_id=company_id,
                registry_mode=_m._registry_enabled(),
                source="api_v1_admin_company_lookup",
            ),
            exc,
        )
        return None, JSONResponse(
            status_code=503,
            content={
                "error": "Registry storage unavailable",
                "code": "REGISTRY_STORAGE_UNAVAILABLE",
                "tenantId": tenant_id,
            },
        )
    if not isinstance(company_doc, dict):
        return None, JSONResponse(
            status_code=404,
            content={
                "error": "Company not found for tenant",
                "code": "COMPANY_NOT_FOUND",
                "tenantId": tenant_id,
                "companyId": company_id,
            },
        )
    return company_doc, None


async def _save_registry_company_doc(
    *,
    tenant_id: str,
    company_id: str,
    payload: dict[str, object],
) -> None:
    db = _m._registry_db_client()
    if db is None:
        raise RuntimeError("Registry storage unavailable")
    doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
    )
    await _doc_set(doc_ref, payload, merge=True)


async def _upsert_registry_company_doc(
    db: object,
    *,
    tenant_id: str,
    company_id: str,
    display_name: str,
    spoken_name: str | None,
    industry_template_id: str,
    status: str,
    connectors: dict[str, object],
    overview: str,
    facts: dict[str, object],
    links: list[str],
) -> tuple[bool, dict[str, object]]:
    if db is None:
        raise RuntimeError("Registry storage unavailable")

    doc_ref = (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
    )
    existing_snapshot = await _doc_get(doc_ref)
    existing_data = (
        existing_snapshot.to_dict()
        if getattr(existing_snapshot, "exists", False) and hasattr(existing_snapshot, "to_dict")
        else {}
    )
    created = not bool(existing_data)
    now_iso = datetime.now(timezone.utc).isoformat()
    created_at = (
        existing_data.get("created_at")
        if isinstance(existing_data, dict) and isinstance(existing_data.get("created_at"), str)
        else now_iso
    )

    doc_payload: dict[str, object] = {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "industry_template_id": industry_template_id,
        "display_name": display_name,
        "spoken_name": (spoken_name or "").strip(),
        "status": status,
        "connectors": connectors,
        "overview": overview.strip(),
        "facts": facts if isinstance(facts, dict) else {},
        "links": [str(link).strip() for link in links if str(link).strip()],
        "created_at": created_at,
        "updated_at": now_iso,
    }

    await _doc_set(doc_ref, doc_payload, merge=True)
    return created, doc_payload


def _resolve_company_for_bootstrap(
    *,
    requested_company_id: str | None,
    companies: list[dict[str, object]],
    defaults: dict[str, object] | None,
) -> str | None:
    """Resolve bootstrap company selection from request/defaults/available companies."""
    company_ids = {
        str(item.get("id", "")).strip().lower()
        for item in companies
        if isinstance(item, dict) and item.get("id")
    }

    if requested_company_id:
        normalized = _m._normalize_company_id(requested_company_id)
        if normalized in company_ids:
            return normalized
        return None

    default_company = ""
    if isinstance(defaults, dict):
        default_company = _m._normalize_company_id(defaults.get("companyId"))
    if default_company and default_company in company_ids:
        return default_company

    if len(company_ids) == 1:
        return next(iter(company_ids))
    return None
