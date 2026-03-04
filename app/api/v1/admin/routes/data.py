"""Admin runtime data routes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app.api.v1.admin.runtime import runtime as _m
from app.api.models import (
    AdminBookingSlotsImportPayload,
    AdminCompanyExportPayload,
    AdminInventorySyncConfigPayload,
    AdminInventorySyncPayload,
    AdminInventorySyncRunPayload,
    AdminProductsImportPayload,
    AdminRetentionPurgePayload,
)
from app.api.v1.admin.idempotency import require_idempotency_key

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_error_message(exc: Exception, *, fallback: str) -> str:
    """Return a client-safe, sanitized error message."""
    sanitized = _m.sanitize_log(str(exc))
    return sanitized or fallback


async def _persist_inventory_sync_metadata(
    *,
    tenant_id: str,
    company_id: str,
    metadata: dict[str, object],
) -> None:
    await _m._save_registry_company_doc(
        tenant_id=tenant_id,
        company_id=company_id,
        payload={
            "inventory_sync": metadata,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _inventory_sync_schedule_from_existing(existing_sync: dict[str, object]) -> tuple[bool, int, str | None]:
    auto_enabled = bool(existing_sync.get("auto_enabled", False))
    try:
        interval_minutes = int(existing_sync.get("interval_minutes", 15))
    except (TypeError, ValueError):
        interval_minutes = 15
    interval_minutes = max(1, min(interval_minutes, 1440))
    next_run_at = existing_sync.get("next_run_at")
    if not isinstance(next_run_at, str) or not next_run_at.strip():
        next_run_at = None
    return auto_enabled, interval_minutes, next_run_at


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


@router.post("/companies/{company_id}/seed/demo")
async def seed_admin_company_demo_route(
    company_id: str,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_seed_demo",
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

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_demo_seed",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "seedVersion": getattr(_m, "_DEMO_SEED_VERSION", "electronics-v1"),
            "dataTier": "demo",
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        result = await _m._seed_company_demo_data(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            company_doc=company_doc if isinstance(company_doc, dict) else {},
            data_tier="demo",
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": _client_error_message(exc, fallback="Invalid demo seed request"),
                "code": "DEMO_SEED_INVALID",
            },
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
        **(result if isinstance(result, dict) else {}),
    }
    return await _m._idempotency_commit(
        scope="admin_demo_seed",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.post("/companies/{company_id}/inventory/sync")
async def sync_admin_company_inventory_route(
    company_id: str,
    payload: AdminInventorySyncPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_inventory_sync",
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

    source_type = str(payload.source_type or "").strip().lower()
    source_url = (payload.source_url or "").strip()
    connector_id = _m._normalize_connector_id(payload.connector_id) if payload.connector_id else None
    sheet_name = (payload.sheet_name or "").strip() or None
    data_tier = payload.data_tier.strip().lower() or "admin"
    dry_run = bool(payload.dry_run)

    if source_type == "google_sheets" and not source_url:
        return JSONResponse(
            status_code=400,
            content={"error": "sourceUrl is required for google_sheets", "code": "INVENTORY_SOURCE_URL_REQUIRED"},
        )
    if source_type == "mcp_connector" and not connector_id:
        return JSONResponse(
            status_code=400,
            content={"error": "connectorId is required for mcp_connector", "code": "INVENTORY_CONNECTOR_ID_REQUIRED"},
        )

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_inventory_sync",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "sourceType": source_type,
            "sourceUrl": source_url,
            "connectorId": connector_id,
            "sheetName": sheet_name,
            "dataTier": data_tier,
            "dryRun": dry_run,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        if source_type == "google_sheets":
            result = await _m._sync_company_inventory_from_google_sheet(
                tenant_id=tenant_id,
                company_id=normalized_company_id,
                source_url=source_url,
                sheet_name=sheet_name,
                data_tier=data_tier,
                dry_run=dry_run,
            )
        elif source_type == "mcp_connector":
            result = await _m._sync_company_inventory_from_connector(
                tenant_id=tenant_id,
                company_id=normalized_company_id,
                company_doc=company_doc if isinstance(company_doc, dict) else {},
                connector_id=connector_id or "",
                data_tier=data_tier,
                dry_run=dry_run,
            )
        else:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid sourceType", "code": "INVENTORY_SOURCE_INVALID"},
            )
    except NotImplementedError as exc:
        return JSONResponse(
            status_code=501,
            content={
                "error": _client_error_message(exc, fallback="Inventory source is not implemented"),
                "code": "INVENTORY_SOURCE_NOT_IMPLEMENTED",
            },
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": _client_error_message(exc, fallback="Invalid inventory source request"),
                "code": "INVENTORY_SOURCE_INVALID",
            },
        )
    except RuntimeError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": _client_error_message(exc, fallback="Inventory source fetch failed"),
                "code": "INVENTORY_SOURCE_FETCH_FAILED",
            },
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    existing_sync = (
        company_doc.get("inventory_sync")
        if isinstance(company_doc, dict) and isinstance(company_doc.get("inventory_sync"), dict)
        else {}
    )
    auto_enabled, interval_minutes, existing_next_run_at = _inventory_sync_schedule_from_existing(existing_sync)
    configured_at = (
        str(existing_sync.get("configured_at")).strip()
        if isinstance(existing_sync.get("configured_at"), str)
        else None
    )
    raw_errors = result.get("errors")
    normalized_errors = [str(item) for item in raw_errors] if isinstance(raw_errors, list) else []
    status = "success" if len(normalized_errors) == 0 else "partial"

    try:
        metadata = _m._inventory_sync_metadata(
            source_type=source_type,
            source_url=source_url or None,
            connector_id=connector_id,
            sheet_name=sheet_name,
            data_tier=data_tier,
            dry_run=dry_run,
            status=status,
            written=int(result.get("written", 0)),
            parsed_rows=int(result.get("parsedRows", 0)),
            normalized_rows=int(result.get("normalizedRows", 0)),
            errors=normalized_errors,
            auto_enabled=auto_enabled,
            interval_minutes=interval_minutes,
            next_run_at=existing_next_run_at,
            last_attempt_at=datetime.now(timezone.utc).isoformat(),
            last_error=normalized_errors[0] if normalized_errors else None,
        )
        if configured_at:
            metadata["configured_at"] = configured_at
        await _persist_inventory_sync_metadata(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            metadata=metadata,
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "sourceType": source_type,
        "sourceUrl": source_url,
        "connectorId": connector_id,
        "sheetName": sheet_name,
        "dataTier": data_tier,
        "dryRun": dry_run,
        **result,
    }
    return await _m._idempotency_commit(
        scope="admin_inventory_sync",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.post("/companies/{company_id}/inventory/upload")
async def upload_admin_company_inventory_route(
    company_id: str,
    request: Request,
    file: UploadFile = File(...),
    data_tier: str = Form(default="admin"),
    dry_run: bool = Form(default=False),
    sheet_name: str | None = Form(default=None),
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_inventory_upload",
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

    filename = (file.filename or "inventory-upload.csv").strip() or "inventory-upload.csv"
    normalized_data_tier = data_tier.strip().lower() or "admin"
    normalized_sheet_name = (sheet_name or "").strip() or None
    max_bytes = int(getattr(_m, "INVENTORY_IMPORT_MAX_BYTES", 5_242_880))
    raw = await file.read(max_bytes + 1)
    await file.close()
    if len(raw) == 0:
        return JSONResponse(status_code=400, content={"error": "Empty file", "code": "EMPTY_UPLOAD"})
    if len(raw) > max_bytes:
        return JSONResponse(
            status_code=413,
            content={"error": "Upload exceeds max size", "code": "UPLOAD_TOO_LARGE", "maxBytes": max_bytes},
        )
    if not filename.lower().endswith((".csv", ".xlsx")):
        return JSONResponse(
            status_code=400,
            content={"error": "Only .csv or .xlsx files are supported", "code": "INVENTORY_FILE_TYPE_INVALID"},
        )

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_inventory_upload",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "fileName": filename,
            "sizeBytes": len(raw),
            "dataTier": normalized_data_tier,
            "dryRun": bool(dry_run),
            "sheetName": normalized_sheet_name,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        result = await _m._sync_company_inventory_from_upload(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            filename=filename,
            raw=raw,
            sheet_name=normalized_sheet_name,
            data_tier=normalized_data_tier,
            dry_run=bool(dry_run),
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": _client_error_message(exc, fallback="Invalid inventory file"),
                "code": "INVENTORY_FILE_INVALID",
            },
        )
    except RuntimeError as exc:
        return JSONResponse(
            status_code=501,
            content={
                "error": _client_error_message(exc, fallback="XLSX processing unavailable"),
                "code": "INVENTORY_XLSX_UNAVAILABLE",
            },
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    existing_sync = (
        company_doc.get("inventory_sync")
        if isinstance(company_doc, dict) and isinstance(company_doc.get("inventory_sync"), dict)
        else {}
    )
    auto_enabled, interval_minutes, existing_next_run_at = _inventory_sync_schedule_from_existing(existing_sync)
    configured_at = (
        str(existing_sync.get("configured_at")).strip()
        if isinstance(existing_sync.get("configured_at"), str)
        else None
    )
    raw_errors = result.get("errors")
    normalized_errors = [str(item) for item in raw_errors] if isinstance(raw_errors, list) else []
    status = "success" if len(normalized_errors) == 0 else "partial"

    try:
        metadata = _m._inventory_sync_metadata(
            source_type="file_upload",
            data_tier=normalized_data_tier,
            dry_run=bool(dry_run),
            sheet_name=normalized_sheet_name,
            status=status,
            written=int(result.get("written", 0)),
            parsed_rows=int(result.get("parsedRows", 0)),
            normalized_rows=int(result.get("normalizedRows", 0)),
            errors=normalized_errors,
            auto_enabled=auto_enabled,
            interval_minutes=interval_minutes,
            next_run_at=existing_next_run_at,
            last_attempt_at=datetime.now(timezone.utc).isoformat(),
            last_error=normalized_errors[0] if normalized_errors else None,
        )
        if configured_at:
            metadata["configured_at"] = configured_at
        await _persist_inventory_sync_metadata(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            metadata=metadata,
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "fileName": filename,
        "dataTier": normalized_data_tier,
        "dryRun": bool(dry_run),
        "sheetName": normalized_sheet_name,
        **result,
    }
    return await _m._idempotency_commit(
        scope="admin_inventory_upload",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.put("/companies/{company_id}/inventory/sync/config")
async def configure_admin_company_inventory_sync_route(
    company_id: str,
    payload: AdminInventorySyncConfigPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_inventory_sync_config",
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

    source_type = str(payload.source_type or "").strip().lower()
    source_url = (payload.source_url or "").strip()
    connector_id = _m._normalize_connector_id(payload.connector_id) if payload.connector_id else None
    sheet_name = (payload.sheet_name or "").strip() or None
    data_tier = payload.data_tier.strip().lower() or "admin"
    dry_run = bool(payload.dry_run)
    auto_enabled = bool(payload.auto_enabled)
    interval_minutes = max(1, min(int(payload.interval_minutes), 1440))

    if source_type == "google_sheets" and not source_url:
        return JSONResponse(
            status_code=400,
            content={"error": "sourceUrl is required for google_sheets", "code": "INVENTORY_SOURCE_URL_REQUIRED"},
        )
    if source_type == "mcp_connector" and not connector_id:
        return JSONResponse(
            status_code=400,
            content={"error": "connectorId is required for mcp_connector", "code": "INVENTORY_CONNECTOR_ID_REQUIRED"},
        )

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_inventory_sync_config",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "sourceType": source_type,
            "sourceUrl": source_url,
            "connectorId": connector_id,
            "sheetName": sheet_name,
            "dataTier": data_tier,
            "dryRun": dry_run,
            "autoEnabled": auto_enabled,
            "intervalMinutes": interval_minutes,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    now_iso = datetime.now(timezone.utc).isoformat()
    existing_sync = (
        company_doc.get("inventory_sync")
        if isinstance(company_doc, dict) and isinstance(company_doc.get("inventory_sync"), dict)
        else {}
    )
    last_result = existing_sync.get("last_result")
    last_result_map = last_result if isinstance(last_result, dict) else {}
    status = str(existing_sync.get("status") or "configured").strip().lower() or "configured"
    next_run_at = now_iso if auto_enabled else None
    metadata = _m._inventory_sync_metadata(
        source_type=source_type,
        source_url=source_url or None,
        connector_id=connector_id,
        sheet_name=sheet_name,
        data_tier=data_tier,
        dry_run=dry_run,
        status=status,
        written=int(last_result_map.get("written", 0)),
        parsed_rows=int(last_result_map.get("parsed_rows", 0)),
        normalized_rows=int(last_result_map.get("normalized_rows", 0)),
        errors=[],
        auto_enabled=auto_enabled,
        interval_minutes=interval_minutes,
        next_run_at=next_run_at,
        last_attempt_at=(
            str(existing_sync.get("last_attempt_at")).strip()
            if isinstance(existing_sync.get("last_attempt_at"), str)
            else None
        ),
        last_error=(
            str(existing_sync.get("last_error")).strip()
            if isinstance(existing_sync.get("last_error"), str)
            else None
        ),
    )
    metadata["configured_at"] = now_iso

    try:
        await _persist_inventory_sync_metadata(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            metadata=metadata,
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "configured": True,
        "inventorySync": metadata,
    }
    return await _m._idempotency_commit(
        scope="admin_inventory_sync_config",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )


@router.post("/inventory/sync/run")
async def run_admin_inventory_sync_jobs_route(
    payload: AdminInventorySyncRunPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_inventory_sync_run",
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

    normalized_company_id = (
        _m._normalize_company_id_strict(payload.company_id)
        if isinstance(payload.company_id, str) and payload.company_id.strip()
        else None
    )
    if payload.company_id and not normalized_company_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid companyId", "code": "INVALID_COMPANY_ID"},
        )

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_inventory_sync_run",
        tenant_id=tenant_id,
        payload={
            "companyId": normalized_company_id,
            "maxCompanies": payload.max_companies,
            "force": bool(payload.force),
            "dryRunOverride": payload.dry_run_override,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        result = await _m._run_inventory_sync_jobs(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            max_companies=payload.max_companies,
            force=bool(payload.force),
            dry_run_override=payload.dry_run_override,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": _client_error_message(exc, fallback="Invalid inventory sync run request"),
                "code": "INVENTORY_SYNC_RUN_INVALID",
            },
        )
    except Exception:
        return JSONResponse(status_code=503, content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"})

    body = {
        "apiVersion": "v1",
        **result,
    }
    return await _m._idempotency_commit(
        scope="admin_inventory_sync_run",
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
