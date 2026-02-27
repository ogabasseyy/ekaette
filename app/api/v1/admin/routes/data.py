"""Admin runtime data routes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.v1.admin.runtime import runtime as _m
from app.api.models import (
    AdminBookingSlotsImportPayload,
    AdminCompanyExportPayload,
    AdminProductsImportPayload,
    AdminRetentionPurgePayload,
)
from app.api.v1.admin.idempotency import require_idempotency_key

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/companies/{company_id}/products/import")
async def import_admin_company_products_route(
    company_id: str,
    payload: AdminProductsImportPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_products_import",
    )
    if blocked_origin:
        return blocked_origin
    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(status_code=403, content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN"})
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(status_code=404, content={"error": "Company not found for tenant", "code": "COMPANY_NOT_FOUND"})
    _, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_products_import",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "products": payload.products,
            "dataTier": payload.data_tier,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        result = await _m._import_company_products(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            products=payload.products,
            data_tier=payload.data_tier.strip().lower() or "admin",
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "collection": "products",
        **result,
    }
    return await _m._idempotency_commit(
        scope="admin_products_import",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.post("/companies/{company_id}/booking-slots/import")
async def import_admin_company_booking_slots_route(
    company_id: str,
    payload: AdminBookingSlotsImportPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_booking_slots_import",
    )
    if blocked_origin:
        return blocked_origin
    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(status_code=403, content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN"})
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(status_code=404, content={"error": "Company not found for tenant", "code": "COMPANY_NOT_FOUND"})
    _, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_booking_slots_import",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "slots": payload.slots,
            "dataTier": payload.data_tier,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        result = await _m._import_company_booking_slots(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            slots=payload.slots,
            data_tier=payload.data_tier.strip().lower() or "admin",
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "collection": "booking_slots",
        **result,
    }
    return await _m._idempotency_commit(
        scope="admin_booking_slots_import",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.post("/companies/{company_id}/runtime/purge-demo")
async def purge_admin_company_demo_runtime_data_route(
    company_id: str,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_runtime_purge_demo",
    )
    if blocked_origin:
        return blocked_origin
    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(status_code=403, content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN"})
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="read")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(status_code=404, content={"error": "Company not found for tenant", "code": "COMPANY_NOT_FOUND"})
    _, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_runtime_purge_demo",
        tenant_id=tenant_id,
        payload={"companyId": normalized_company_id, "action": "purge-demo"},
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        deleted = await _m._purge_company_demo_runtime_data(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "deleted": deleted,
    }
    return await _m._idempotency_commit(
        scope="admin_runtime_purge_demo",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.post("/companies/{company_id}/export")
async def export_admin_company_data_route(
    company_id: str,
    payload: AdminCompanyExportPayload,
    request: Request,
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_export",
    )
    if blocked_origin:
        return blocked_origin
    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(status_code=403, content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN"})
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(status_code=404, content={"error": "Company not found for tenant", "code": "COMPANY_NOT_FOUND"})
    company_doc, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    try:
        export_bundle = await _m._export_company_bundle(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            company_doc=company_doc,
            include_runtime_data=bool(payload.include_runtime_data),
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"},
        )

    return {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "includeRuntimeData": bool(payload.include_runtime_data),
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        **export_bundle,
    }


@router.delete("/companies/{company_id}")
async def delete_admin_company_route(
    company_id: str,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_delete",
    )
    if blocked_origin:
        return blocked_origin
    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(status_code=403, content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN"})
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(status_code=404, content={"error": "Company not found for tenant", "code": "COMPANY_NOT_FOUND"})
    _, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_company_delete",
        tenant_id=tenant_id,
        payload={"companyId": normalized_company_id},
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        deleted = await _m._delete_company_bundle(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"},
        )

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "deleted": deleted,
    }
    return await _m._idempotency_commit(
        scope="admin_company_delete",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.post("/companies/{company_id}/retention/purge")
async def purge_admin_company_retention_data_route(
    company_id: str,
    payload: AdminRetentionPurgePayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_retention_purge",
    )
    if blocked_origin:
        return blocked_origin
    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(status_code=403, content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN"})
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(status_code=404, content={"error": "Company not found for tenant", "code": "COMPANY_NOT_FOUND"})
    _, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    allowed_collections = {"knowledge", "products", "booking_slots"}
    selected_collections = {
        str(item).strip().lower()
        for item in payload.collections
        if str(item).strip()
    }
    if not selected_collections:
        return JSONResponse(
            status_code=400,
            content={"error": "At least one collection is required", "code": "RETENTION_COLLECTIONS_REQUIRED"},
        )
    invalid_collections = sorted(selected_collections - allowed_collections)
    if invalid_collections:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid retention collection(s)",
                "code": "RETENTION_COLLECTION_INVALID",
                "collections": invalid_collections,
            },
        )

    normalized_data_tier = (
        payload.data_tier.strip().lower() if isinstance(payload.data_tier, str) and payload.data_tier.strip() else None
    )
    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_company_retention_purge",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "olderThanDays": payload.older_than_days,
            "collections": sorted(selected_collections),
            "dataTier": normalized_data_tier,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        report = await _m._purge_company_retention_data(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            older_than_days=payload.older_than_days,
            collections=sorted(selected_collections),
            data_tier=normalized_data_tier,
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"},
        )

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "olderThanDays": payload.older_than_days,
        "collections": sorted(selected_collections),
        "dataTier": normalized_data_tier,
        "report": report,
    }
    return await _m._idempotency_commit(
        scope="admin_company_retention_purge",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )
