"""Admin company + provider routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.v1.admin.runtime import runtime as _m
from app.api.models import AdminCompanyUpdatePayload, AdminCompanyUpsertPayload
from app.api.v1.admin.idempotency import require_idempotency_key

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/companies")
async def upsert_admin_company_route(
    payload: AdminCompanyUpsertPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    """Create or update a tenant company profile in registry storage."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_companies_upsert",
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
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )

    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    payload_tenant_id = _m._normalize_tenant_id(payload.tenant_id, default=tenant_id)
    if payload_tenant_id != tenant_id:
        return JSONResponse(
            status_code=403,
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )

    company_id = _m._normalize_company_id_strict(payload.company_id)
    if not company_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid companyId",
                "code": "INVALID_COMPANY_ID",
            },
        )
    industry_template_id = _m._normalize_template_id(payload.industry_template_id) or ""
    display_name = payload.display_name.strip()
    spoken_name = payload.spoken_name.strip() if isinstance(payload.spoken_name, str) else ""
    status = payload.status.strip().lower()
    connectors = payload.connectors if isinstance(payload.connectors, dict) else {}
    facts = payload.facts if isinstance(payload.facts, dict) else {}
    links = payload.links if isinstance(payload.links, list) else []
    overview = payload.overview if isinstance(payload.overview, str) else ""

    if not industry_template_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid industryTemplateId",
                "code": "INVALID_TEMPLATE_ID",
            },
        )
    if not display_name:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid displayName",
                "code": "INVALID_DISPLAY_NAME",
            },
        )
    if status not in {"active", "beta", "disabled"}:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid status",
                "code": "INVALID_STATUS",
            },
        )

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_companies_upsert",
        tenant_id=tenant_id,
        payload={
            "tenantId": tenant_id,
            "companyId": company_id,
            "industryTemplateId": industry_template_id,
            "displayName": display_name,
            "spokenName": spoken_name,
            "status": status,
            "connectors": connectors,
            "facts": facts,
            "links": links,
            "overview": overview,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    from app.configs.registry_loader import RegistryDataMissingError, build_onboarding_config

    try:
        onboarding = await build_onboarding_config(_m.industry_config_client, tenant_id)
    except RegistryDataMissingError as exc:
        logger.warning(
            "Registry onboarding config missing for tenant_id=%s code=%s",
            _m.sanitize_log(tenant_id),
            _m.sanitize_log(getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND")),
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry onboarding config unavailable",
                "code": getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
                "tenantId": tenant_id,
            },
        )

    templates = onboarding.get("templates", [])
    template_ids = {
        _m._normalize_template_id(item.get("id"))
        for item in templates
        if isinstance(item, dict) and item.get("id")
    }
    if industry_template_id not in template_ids:
        return JSONResponse(
            status_code=404,
            content={
                "error": "Industry template not found for tenant",
                "code": "TEMPLATE_NOT_FOUND",
                "tenantId": tenant_id,
                "industryTemplateId": industry_template_id,
            },
        )

    try:
        created, stored_doc = await _m._upsert_registry_company_doc(
            _m._registry_db_client(),
            tenant_id=tenant_id,
            company_id=company_id,
            display_name=display_name,
            spoken_name=spoken_name,
            industry_template_id=industry_template_id,
            status=status,
            connectors=connectors,
            overview=overview,
            facts=facts,
            links=links,
        )
    except Exception as exc:
        logger.warning(
            "Admin company upsert failed %s details=%s",
            _m.registry_log_context(
                tenant_id=tenant_id,
                company_id=company_id,
                industry_template_id=industry_template_id,
                registry_mode=_m._registry_enabled(),
                source="api_v1_admin_companies_upsert",
            ),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry storage unavailable",
                "code": "REGISTRY_STORAGE_UNAVAILABLE",
                "tenantId": tenant_id,
            },
        )

    response_body: dict[str, object] = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": company_id,
        "created": created,
        "company": _m._admin_company_response(
            tenant_id=tenant_id,
            company_id=company_id,
            raw_company=stored_doc,
        ),
    }
    response_status = 201 if created else 200
    return await _m._idempotency_commit(
        scope="admin_companies_upsert",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=response_status,
        body=response_body,
    )


@router.get("/companies")
async def get_admin_companies_route(
    request: Request,
):
    """List companies visible to the authenticated tenant admin."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_companies",
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
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )

    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="read")
    if auth_error:
        return auth_error

    from app.configs.registry_loader import RegistryDataMissingError, build_onboarding_config

    try:
        onboarding = await build_onboarding_config(_m.industry_config_client, tenant_id)
    except RegistryDataMissingError as exc:
        logger.warning(
            "Registry onboarding config missing for tenant_id=%s code=%s",
            _m.sanitize_log(tenant_id),
            _m.sanitize_log(getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND")),
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry onboarding config unavailable",
                "code": getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
                "tenantId": tenant_id,
            },
        )

    companies = onboarding.get("companies", [])
    if not isinstance(companies, list):
        companies = []
    return {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companies": companies,
        "count": len(companies),
    }


@router.get("/companies/{company_id}")
async def get_admin_company_route(
    company_id: str,
    request: Request,
):
    """Get one tenant company profile by id."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_get",
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
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="read")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(
            status_code=404,
            content={
                "error": "Company not found for tenant",
                "code": "COMPANY_NOT_FOUND",
                "tenantId": tenant_id,
                "companyId": str(company_id).strip().lower(),
            },
        )

    company_doc, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    return {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "company": _m._admin_company_response(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            raw_company=company_doc,
        ),
    }


@router.put("/companies/{company_id}")
async def update_admin_company_route(
    company_id: str,
    payload: AdminCompanyUpdatePayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    """Update an existing tenant company profile."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_update",
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
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )
    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="write")
    if auth_error:
        return auth_error

    normalized_company_id = _m._normalize_company_id_strict(company_id)
    if not normalized_company_id:
        return JSONResponse(
            status_code=404,
            content={
                "error": "Company not found for tenant",
                "code": "COMPANY_NOT_FOUND",
                "tenantId": tenant_id,
                "companyId": str(company_id).strip().lower(),
            },
        )

    payload_tenant_id = _m._normalize_tenant_id(payload.tenant_id, default=tenant_id)
    if payload_tenant_id != tenant_id:
        return JSONResponse(
            status_code=403,
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )

    company_doc, company_error = await _m._load_registry_company_doc(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
    )
    if company_error:
        return company_error

    next_display_name = (
        payload.display_name.strip()
        if isinstance(payload.display_name, str)
        else str(company_doc.get("display_name") or company_doc.get("name") or normalized_company_id)
    )
    next_spoken_name = (
        payload.spoken_name.strip()
        if isinstance(payload.spoken_name, str)
        else (
            str(company_doc.get("spoken_name"))
            if "spoken_name" in company_doc
            else str(company_doc.get("name") or next_display_name)
        )
    )
    next_template_id = (
        _m._normalize_template_id(payload.industry_template_id)
        if isinstance(payload.industry_template_id, str)
        else _m._normalize_template_id(company_doc.get("industry_template_id"))
    ) or ""
    next_status = (
        payload.status.strip().lower()
        if isinstance(payload.status, str)
        else str(company_doc.get("status") or "active").strip().lower()
    )
    next_connectors = (
        payload.connectors
        if isinstance(payload.connectors, dict)
        else (company_doc.get("connectors") if isinstance(company_doc.get("connectors"), dict) else {})
    )
    next_overview = (
        payload.overview
        if isinstance(payload.overview, str)
        else str(company_doc.get("overview") or "")
    )
    next_facts = (
        payload.facts
        if isinstance(payload.facts, dict)
        else (company_doc.get("facts") if isinstance(company_doc.get("facts"), dict) else {})
    )
    next_links = (
        payload.links
        if isinstance(payload.links, list)
        else (company_doc.get("links") if isinstance(company_doc.get("links"), list) else [])
    )

    if not next_display_name:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid displayName", "code": "INVALID_DISPLAY_NAME"},
        )
    if not next_template_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid industryTemplateId", "code": "INVALID_TEMPLATE_ID"},
        )
    if next_status not in {"active", "beta", "disabled"}:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid status", "code": "INVALID_STATUS"},
        )

    from app.configs.registry_loader import RegistryDataMissingError, build_onboarding_config

    try:
        onboarding = await build_onboarding_config(_m.industry_config_client, tenant_id)
    except RegistryDataMissingError as exc:
        logger.warning(
            "Registry onboarding config missing for tenant_id=%s code=%s",
            _m.sanitize_log(tenant_id),
            _m.sanitize_log(getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND")),
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry onboarding config unavailable",
                "code": getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
                "tenantId": tenant_id,
            },
        )
    templates = onboarding.get("templates", [])
    template_ids = {
        _m._normalize_template_id(item.get("id"))
        for item in templates
        if isinstance(item, dict) and item.get("id")
    }
    if next_template_id not in template_ids:
        return JSONResponse(
            status_code=404,
            content={
                "error": "Industry template not found for tenant",
                "code": "TEMPLATE_NOT_FOUND",
                "tenantId": tenant_id,
                "industryTemplateId": next_template_id,
            },
        )

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_company_update",
        tenant_id=tenant_id,
        payload={
            "tenantId": tenant_id,
            "companyId": normalized_company_id,
            "displayName": next_display_name,
            "spokenName": next_spoken_name,
            "industryTemplateId": next_template_id,
            "status": next_status,
            "connectors": next_connectors,
            "overview": next_overview,
            "facts": next_facts,
            "links": next_links,
        },
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    existing_created_at = company_doc.get("created_at")
    payload_doc = _m._admin_company_payload(
        tenant_id=tenant_id,
        company_id=normalized_company_id,
        industry_template_id=next_template_id,
        display_name=next_display_name,
        spoken_name=next_spoken_name,
        status=next_status,
        connectors=next_connectors if isinstance(next_connectors, dict) else {},
        overview=next_overview,
        facts=next_facts if isinstance(next_facts, dict) else {},
        links=next_links if isinstance(next_links, list) else [],
    )
    if isinstance(existing_created_at, str) and existing_created_at:
        payload_doc["created_at"] = existing_created_at

    try:
        await _m._save_registry_company_doc(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            payload=payload_doc,
        )
    except Exception as exc:
        logger.warning(
            "Admin company update failed %s details=%s",
            _m.registry_log_context(
                tenant_id=tenant_id,
                company_id=normalized_company_id,
                industry_template_id=next_template_id,
                registry_mode=_m._registry_enabled(),
                source="api_v1_admin_company_update",
            ),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry storage unavailable",
                "code": "REGISTRY_STORAGE_UNAVAILABLE",
                "tenantId": tenant_id,
            },
        )

    response_body: dict[str, object] = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": normalized_company_id,
        "updated": True,
        "company": _m._admin_company_response(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            raw_company=payload_doc,
        ),
    }
    return await _m._idempotency_commit(
        scope="admin_company_update",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=request_fingerprint,
        status_code=200,
        body=response_body,
    )


@router.get("/mcp/providers")
async def get_admin_mcp_providers_route(
    request: Request,
):
    """Return allowed MCP provider definitions for admin connector setup."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_mcp_providers",
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
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )

    _, auth_error = _m._admin_context_or_reject(request, tenant_id=tenant_id, required_scope="read")
    if auth_error:
        return auth_error

    providers = [dict(item) for _, item in sorted(_m._effective_mcp_provider_catalog().items())]

    return {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "providers": providers,
        "count": len(providers),
    }
