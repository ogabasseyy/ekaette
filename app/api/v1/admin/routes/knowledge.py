"""Admin knowledge routes."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app.api.v1.admin.runtime import runtime as _m
from app.api.models import AdminKnowledgeImportTextPayload, AdminKnowledgeImportUrlPayload
from app.api.v1.admin.idempotency import require_idempotency_key

logger = logging.getLogger(__name__)

try:
    from kreuzberg import extract_bytes as _extract_bytes

    _HAS_KREUZBERG = True
except ImportError:  # pragma: no cover – kreuzberg is a required dep
    _HAS_KREUZBERG = False

router = APIRouter()


@router.get("/companies/{company_id}/knowledge")
async def list_admin_company_knowledge_route(
    company_id: str,
    request: Request,
):
    """List knowledge entries for a tenant company."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_knowledge_list",
    )
    if blocked_origin:
        return blocked_origin

    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(
            status_code=403,
            content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN", "tenantId": tenant_id},
        )
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(
            status_code=404,
            content={"error": "Company not found for tenant", "code": "COMPANY_NOT_FOUND", "tenantId": tenant_id},
        )
    _, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    try:
        raw_limit = int(request.query_params.get("limit", "50"))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid limit parameter", "code": "INVALID_LIMIT"},
        )
    safe_limit = max(1, min(raw_limit, 200))
    try:
        entries = await _m.load_company_knowledge(
            _m._registry_db_client(),
            normalized_company_id,
            limit=safe_limit,
            tenant_id=tenant_id,
        )
    except Exception as exc:
        logger.warning(
            "Admin knowledge list failed %s details=%s",
            _m.registry_log_context(
                tenant_id=tenant_id,
                company_id=normalized_company_id,
                registry_mode=_m._registry_enabled(),
                source="api_v1_admin_company_knowledge_list",
            ),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"},
        )

    return {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "entries": entries,
        "count": len(entries),
    }


@router.post("/companies/{company_id}/knowledge/import-text")
async def import_admin_company_knowledge_text_route(
    company_id: str,
    payload: AdminKnowledgeImportTextPayload,
    request: Request,
):
    """Import one text knowledge entry for a company."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_knowledge_import_text",
    )
    if blocked_origin:
        return blocked_origin

    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(
            status_code=403,
            content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN", "tenantId": tenant_id},
        )
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

    text = payload.text.strip()
    title = (payload.title or text[:80] or "Imported Knowledge").strip()
    tags = _m._normalize_tags(payload.tags, default_tag="general")
    source = (payload.source or "text").strip().lower() or "text"
    url = (payload.url or "").strip()
    knowledge_id = _m._normalize_connector_id(payload.knowledge_id) if payload.knowledge_id else None
    if not knowledge_id:
        seed = f"{title}|{text}|{time.time()}".encode("utf-8")
        knowledge_id = "kb-" + hashlib.sha256(seed).hexdigest()[:16]

    entry = {
        "id": knowledge_id,
        "title": title,
        "text": text,
        "tags": tags,
        "source": source,
        "url": url,
        "data_tier": "admin",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    from app.configs.registry_schema import validate_knowledge_entry

    validation_errors = validate_knowledge_entry(entry)
    if validation_errors:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid knowledge entry", "code": "INVALID_KNOWLEDGE_ENTRY", "details": validation_errors},
        )

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_begin(
        request,
        scope="admin_knowledge_import_text",
        tenant_id=tenant_id,
        payload={"companyId": normalized_company_id, "entry": entry},
    )
    if idempotency_response:
        return idempotency_response

    try:
        await _m._write_company_knowledge_entry(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            knowledge_id=knowledge_id,
            entry=entry,
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
        "knowledgeId": knowledge_id,
        "entry": entry,
        "created": True,
    }
    return await _m._idempotency_commit(
        scope="admin_knowledge_import_text",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=201,
        body=body,
    )


@router.post("/companies/{company_id}/knowledge/import-url")
async def import_admin_company_knowledge_url_route(
    company_id: str,
    payload: AdminKnowledgeImportUrlPayload,
    request: Request,
):
    """Import one URL knowledge entry for a company."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_knowledge_import_url",
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

    if not payload.url.startswith(("http://", "https://")):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid URL", "code": "INVALID_URL"},
        )
    text = (payload.text or f"Imported reference from {payload.url}").strip()
    text_payload = AdminKnowledgeImportTextPayload(
        knowledgeId=payload.knowledge_id,
        title=payload.title or payload.url,
        text=text,
        tags=payload.tags,
        source=payload.source or "url",
        url=payload.url,
    )
    return await import_admin_company_knowledge_text_route(normalized_company_id, text_payload, request)


@router.post("/companies/{company_id}/knowledge/import-file")
async def import_admin_company_knowledge_file_route(
    company_id: str,
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    source: str = Form(default="file"),
):
    """Import text knowledge from an uploaded file."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_knowledge_import_file",
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

    raw = await file.read(_m.KNOWLEDGE_IMPORT_MAX_BYTES + 1)
    await file.close()
    if len(raw) == 0:
        return JSONResponse(status_code=400, content={"error": "Empty file", "code": "EMPTY_UPLOAD"})
    if len(raw) > _m.KNOWLEDGE_IMPORT_MAX_BYTES:
        return JSONResponse(
            status_code=413,
            content={"error": "Upload exceeds max size", "code": "UPLOAD_TOO_LARGE", "maxBytes": _m.KNOWLEDGE_IMPORT_MAX_BYTES},
        )
    # Use kreuzberg to extract text from any supported format (PDF, DOCX, XLSX, etc.)
    if _HAS_KREUZBERG:
        try:
            mime_type = file.content_type or "application/octet-stream"
            result = await _extract_bytes(raw, mime_type=mime_type)
            text = result.content.strip()
        except Exception as exc:
            logger.warning("kreuzberg extraction failed for %s: %s", file.filename, exc)
            # Fallback: try plain UTF-8 decode for simple text files
            text = raw.decode("utf-8", errors="ignore").strip()
    else:
        text = raw.decode("utf-8", errors="ignore").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "File has no readable text", "code": "INVALID_FILE_TEXT"})
    parsed_tags = [item.strip() for item in tags.split(",")] if isinstance(tags, str) and tags.strip() else ["file"]
    payload = AdminKnowledgeImportTextPayload(
        title=title or file.filename or "Imported File",
        text=text[:20000],
        tags=parsed_tags,
        source=source or "file",
        url="",
    )
    return await import_admin_company_knowledge_text_route(normalized_company_id, payload, request)


@router.delete("/companies/{company_id}/knowledge/{knowledge_id}")
async def delete_admin_company_knowledge_route(
    company_id: str,
    knowledge_id: str,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    """Delete one knowledge entry by id."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_knowledge_delete",
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
    normalized_knowledge_id = _m._normalize_connector_id(knowledge_id)
    if not normalized_company_id or not normalized_knowledge_id:
        return JSONResponse(status_code=404, content={"error": "Knowledge entry not found", "code": "KNOWLEDGE_NOT_FOUND"})
    _, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_knowledge_delete",
        tenant_id=tenant_id,
        payload={"companyId": normalized_company_id, "knowledgeId": normalized_knowledge_id},
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    try:
        deleted = await _m._delete_company_knowledge_entry(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            knowledge_id=normalized_knowledge_id,
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"},
        )
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "Knowledge entry not found", "code": "KNOWLEDGE_NOT_FOUND"})
    body = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "knowledgeId": normalized_knowledge_id,
        "deleted": True,
    }
    return await _m._idempotency_commit(
        scope="admin_knowledge_delete",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=body,
    )
