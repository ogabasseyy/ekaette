"""Admin connector routes."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.v1.admin.runtime import runtime as _m
from app.api.models import AdminConnectorPayload
from app.api.v1.admin.idempotency import require_idempotency_key

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/companies/{company_id}/connectors")
async def create_admin_company_connector_route(
    company_id: str,
    payload: AdminConnectorPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    """Create one connector configuration for a company."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_connector_create",
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
    connector_id = _m._normalize_connector_id(payload.connector_id)
    if not normalized_company_id or not connector_id:
        return JSONResponse(status_code=400, content={"error": "Invalid connectorId", "code": "INVALID_CONNECTOR_ID"})
    db = _m._registry_db_client()
    company_doc_ref = None
    if db is not None:
        company_doc_ref = (
            db
            .collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(normalized_company_id)
        )
    company_doc, company_error = await _m._load_registry_company_doc(tenant_id=tenant_id, company_id=normalized_company_id)
    if company_error:
        return company_error

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_connector_create",
        tenant_id=tenant_id,
        payload={"companyId": normalized_company_id, "connectorId": connector_id, "payload": payload.model_dump(by_alias=True)},
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    connector, connector_error = _m._normalize_connector_payload(
        connector_id=connector_id,
        payload=payload,
        industry_template_id=str(company_doc.get("industry_template_id") or ""),
    )
    if connector_error:
        return connector_error

    lock_key = _m._connector_circuit_key(tenant_id, normalized_company_id, connector_id)
    lock_acquired = await _m._acquire_connector_lock(lock_key)
    if not lock_acquired:
        return JSONResponse(
            status_code=409,
            content={
                "error": "Connector update already in progress",
                "code": "CONNECTOR_WRITE_IN_PROGRESS",
            },
        )
    try:
        company_doc, company_error = await _m._load_registry_company_doc(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
        )
        if company_error:
            return company_error

        connectors = company_doc.get("connectors")
        normalized_connectors = dict(connectors) if isinstance(connectors, dict) else {}
        if connector_id in normalized_connectors:
            return JSONResponse(
                status_code=409,
                content={"error": "Connector already exists", "code": "CONNECTOR_ALREADY_EXISTS"},
            )
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            if company_doc_ref is not None:
                try:
                    await _m._doc_update(
                        company_doc_ref,
                        {
                            f"connectors.{connector_id}": connector,
                            "updated_at": now_iso,
                        },
                    )
                except Exception:
                    company_doc_ref = None
            if company_doc_ref is None:
                normalized_connectors[connector_id] = connector
                company_doc["connectors"] = normalized_connectors
                company_doc["updated_at"] = now_iso
                await _m._save_registry_company_doc(
                    tenant_id=tenant_id,
                    company_id=normalized_company_id,
                    payload=company_doc,
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
            "connectorId": connector_id,
            "connector": connector,
            "created": True,
        }
        return await _m._idempotency_commit(
            scope="admin_connector_create",
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            fingerprint=request_fingerprint,
            status_code=201,
            body=body,
        )
    finally:
        await _m._release_connector_lock(lock_key)


@router.put("/companies/{company_id}/connectors/{connector_id}")
async def update_admin_company_connector_route(
    company_id: str,
    connector_id: str,
    payload: AdminConnectorPayload,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    """Update one connector configuration for a company."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_connector_update",
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
    normalized_connector_id = _m._normalize_connector_id(connector_id)
    if not normalized_company_id or not normalized_connector_id:
        return JSONResponse(status_code=404, content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"})
    db = _m._registry_db_client()
    company_doc_ref = None
    if db is not None:
        company_doc_ref = (
            db
            .collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(normalized_company_id)
        )
    if payload.connector_id and _m._normalize_connector_id(payload.connector_id) not in {None, normalized_connector_id}:
        return JSONResponse(status_code=409, content={"error": "Connector id mismatch", "code": "CONNECTOR_ID_MISMATCH"})

    company_doc, company_error = await _m._load_registry_company_doc(tenant_id=tenant_id, company_id=normalized_company_id)
    if company_error:
        return company_error
    connectors = company_doc.get("connectors")
    normalized_connectors = dict(connectors) if isinstance(connectors, dict) else {}
    if normalized_connector_id not in normalized_connectors:
        return JSONResponse(status_code=404, content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"})

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_connector_update",
        tenant_id=tenant_id,
        payload={"companyId": normalized_company_id, "connectorId": normalized_connector_id, "payload": payload.model_dump(by_alias=True)},
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    connector, connector_error = _m._normalize_connector_payload(
        connector_id=normalized_connector_id,
        payload=payload,
        industry_template_id=str(company_doc.get("industry_template_id") or ""),
    )
    if connector_error:
        return connector_error
    lock_key = _m._connector_circuit_key(tenant_id, normalized_company_id, normalized_connector_id)
    lock_acquired = await _m._acquire_connector_lock(lock_key)
    if not lock_acquired:
        return JSONResponse(
            status_code=409,
            content={
                "error": "Connector update already in progress",
                "code": "CONNECTOR_WRITE_IN_PROGRESS",
            },
        )
    try:
        company_doc, company_error = await _m._load_registry_company_doc(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
        )
        if company_error:
            return company_error
        connectors = company_doc.get("connectors")
        normalized_connectors = dict(connectors) if isinstance(connectors, dict) else {}
        if normalized_connector_id not in normalized_connectors:
            return JSONResponse(
                status_code=404,
                content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"},
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            if company_doc_ref is not None:
                try:
                    await _m._doc_update(
                        company_doc_ref,
                        {
                            f"connectors.{normalized_connector_id}": connector,
                            "updated_at": now_iso,
                        },
                    )
                except Exception:
                    company_doc_ref = None
            if company_doc_ref is None:
                normalized_connectors[normalized_connector_id] = connector
                company_doc["connectors"] = normalized_connectors
                company_doc["updated_at"] = now_iso
                await _m._save_registry_company_doc(
                    tenant_id=tenant_id,
                    company_id=normalized_company_id,
                    payload=company_doc,
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
            "connectorId": normalized_connector_id,
            "connector": connector,
            "updated": True,
        }
        return await _m._idempotency_commit(
            scope="admin_connector_update",
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            fingerprint=request_fingerprint,
            status_code=200,
            body=body,
        )
    finally:
        await _m._release_connector_lock(lock_key)


@router.post("/companies/{company_id}/connectors/{connector_id}/test")
async def test_admin_company_connector_route(
    company_id: str,
    connector_id: str,
    request: Request,
):
    """Test one connector configuration."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_connector_test",
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
    normalized_connector_id = _m._normalize_connector_id(connector_id)
    if not normalized_company_id or not normalized_connector_id:
        return JSONResponse(status_code=404, content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"})
    company_doc, company_error = await _m._load_registry_company_doc(tenant_id=tenant_id, company_id=normalized_company_id)
    if company_error:
        return company_error
    connectors = company_doc.get("connectors")
    normalized_connectors = dict(connectors) if isinstance(connectors, dict) else {}
    connector = normalized_connectors.get(normalized_connector_id)
    if not isinstance(connector, dict):
        return JSONResponse(status_code=404, content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"})
    provider = str(connector.get("provider") or "").strip().lower()
    provider_catalog = _m._effective_mcp_provider_catalog()
    provider_policy = provider_catalog.get(provider)
    if provider_policy is None:
        return JSONResponse(status_code=400, content={"error": "Connector provider not allowed", "code": "CONNECTOR_PROVIDER_NOT_ALLOWED"})

    requires_secret_ref = bool(provider_policy.get("requiresSecretRef", provider != "mock"))
    secret_ref = str(connector.get("secret_ref") or "").strip()
    if requires_secret_ref and not secret_ref:
        return JSONResponse(status_code=400, content={"error": "Connector secretRef is required for this provider", "code": "CONNECTOR_SECRET_REF_REQUIRED"})

    normalized_test_policy, test_policy_error = _m._normalize_connector_test_policy(provider, provider_policy)
    if test_policy_error:
        return test_policy_error
    test_policy = normalized_test_policy or {}

    if provider == "mock":
        await _m._connector_circuit_record_success(
            _m._connector_circuit_key(tenant_id, normalized_company_id, normalized_connector_id)
        )
        return {
            "apiVersion": "v1",
            "tenantId": tenant_id,
            "companyId": normalized_company_id,
            "connectorId": normalized_connector_id,
            "provider": provider,
            "ok": True,
            "details": "Mock connector test passed.",
            "policy": test_policy,
        }

    allowed_hosts = test_policy.get("allowedHosts")
    normalized_allowed_hosts = (
        allowed_hosts if isinstance(allowed_hosts, list) else []
    )
    endpoint_host = _m.extract_connector_endpoint_host(connector)
    if normalized_allowed_hosts:
        if not endpoint_host:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Connector endpoint host is required for this provider",
                    "code": "CONNECTOR_EGRESS_HOST_REQUIRED",
                    "provider": provider,
                    "allowedHosts": normalized_allowed_hosts,
                },
            )
        if not _m.host_matches_allowlist(endpoint_host, normalized_allowed_hosts):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Connector endpoint host is not in provider allowlist",
                    "code": "CONNECTOR_EGRESS_HOST_NOT_ALLOWED",
                    "provider": provider,
                    "endpointHost": endpoint_host,
                    "allowedHosts": normalized_allowed_hosts,
                },
            )

    circuit_key = _m._connector_circuit_key(tenant_id, normalized_company_id, normalized_connector_id)
    if await _m._connector_circuit_is_open(circuit_key):
        retry_after = await _m._connector_circuit_retry_after_seconds(circuit_key)
        return JSONResponse(
            status_code=503,
            content={
                "apiVersion": "v1",
                "tenantId": tenant_id,
                "companyId": normalized_company_id,
                "connectorId": normalized_connector_id,
                "provider": provider,
                "ok": False,
                "code": "CONNECTOR_CIRCUIT_OPEN",
                "details": "Connector probe circuit is open after repeated failures.",
                "retryAfterSeconds": retry_after,
                "policy": test_policy,
            },
        )

    timeout_seconds = float(test_policy.get("timeoutSeconds") or _m.CONNECTOR_TEST_TIMEOUT_SECONDS)
    max_retries = int(test_policy.get("maxRetries") or _m.CONNECTOR_TEST_MAX_RETRIES)
    max_attempts = max(1, max_retries + 1)
    attempt = 0
    failure_code = "CONNECTOR_TEST_NOT_IMPLEMENTED"
    failure_details = "Provider test flow not implemented yet."
    for attempt_index in range(max_attempts):
        attempt = attempt_index + 1
        try:
            probe_result = await asyncio.wait_for(
                _m._execute_connector_test_probe(
                    provider=provider,
                    tenant_id=tenant_id,
                    company_id=normalized_company_id,
                    connector_id=normalized_connector_id,
                    connector=connector,
                ),
                timeout=timeout_seconds,
            )
            await _m._connector_circuit_record_success(circuit_key)
            return {
                "apiVersion": "v1",
                "tenantId": tenant_id,
                "companyId": normalized_company_id,
                "connectorId": normalized_connector_id,
                "provider": provider,
                "ok": True,
                "details": "Connector probe passed.",
                "attempts": attempt,
                "policy": test_policy,
                "result": probe_result if isinstance(probe_result, dict) else {"status": "ok"},
            }
        except asyncio.TimeoutError:
            failure_code = "CONNECTOR_TEST_TIMEOUT"
            failure_details = (
                f"Connector probe timed out after {timeout_seconds:.2f}s "
                f"(attempt {attempt}/{max_attempts})."
            )
        except NotImplementedError as exc:
            failure_code = "CONNECTOR_TEST_NOT_IMPLEMENTED"
            logger.warning("Connector probe not implemented for %s/%s", normalized_company_id, normalized_connector_id, exc_info=True)
            failure_details = "Provider test flow not implemented yet."
            break
        except Exception as exc:  # pragma: no cover - defensive
            failure_code = "CONNECTOR_TEST_FAILED"
            logger.warning("Connector probe failed for %s/%s", normalized_company_id, normalized_connector_id, exc_info=True)
            failure_details = "Connector probe failed."

    await _m._connector_circuit_record_failure(
        circuit_key,
        circuit_open_after_failures=int(
            test_policy.get("circuitOpenAfterFailures") or _m.CONNECTOR_TEST_CIRCUIT_OPEN_AFTER_FAILURES
        ),
        circuit_open_seconds=int(test_policy.get("circuitOpenSeconds") or _m.CONNECTOR_TEST_CIRCUIT_OPEN_SECONDS),
    )
    retry_after = await _m._connector_circuit_retry_after_seconds(circuit_key)
    circuit_open = retry_after > 0

    return JSONResponse(
        status_code=503 if circuit_open else 200,
        content={
            "apiVersion": "v1",
            "tenantId": tenant_id,
            "companyId": normalized_company_id,
            "connectorId": normalized_connector_id,
            "provider": provider,
            "ok": False,
            "code": "CONNECTOR_CIRCUIT_OPEN" if circuit_open else failure_code,
            "details": (
                "Connector probe circuit is open after repeated failures."
                if circuit_open
                else failure_details
            ),
            "attempts": attempt,
            "retryAfterSeconds": retry_after if circuit_open else 0,
            "policy": test_policy,
        },
    )


@router.delete("/companies/{company_id}/connectors/{connector_id}")
async def delete_admin_company_connector_route(
    company_id: str,
    connector_id: str,
    request: Request,
    idempotency_key: str | JSONResponse = Depends(require_idempotency_key),
):
    """Delete one connector configuration."""
    blocked_origin = _m._origin_or_reject(
        request.headers.get("origin"),
        endpoint="api_v1_admin_company_connector_delete",
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
    normalized_connector_id = _m._normalize_connector_id(connector_id)
    if not normalized_company_id or not normalized_connector_id:
        return JSONResponse(status_code=404, content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"})
    db = _m._registry_db_client()
    company_doc_ref = None
    if db is not None:
        company_doc_ref = (
            db
            .collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(normalized_company_id)
        )
    company_doc, company_error = await _m._load_registry_company_doc(tenant_id=tenant_id, company_id=normalized_company_id)
    if company_error:
        return company_error
    connectors = company_doc.get("connectors")
    normalized_connectors = dict(connectors) if isinstance(connectors, dict) else {}
    if normalized_connector_id not in normalized_connectors:
        return JSONResponse(status_code=404, content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"})

    idempotency_key, request_fingerprint, idempotency_response = await _m._idempotency_preflight(
        scope="admin_connector_delete",
        tenant_id=tenant_id,
        payload={"companyId": normalized_company_id, "connectorId": normalized_connector_id},
        idempotency_key_or_response=idempotency_key,
    )
    if idempotency_response:
        return idempotency_response

    lock_key = _m._connector_circuit_key(tenant_id, normalized_company_id, normalized_connector_id)
    lock_acquired = await _m._acquire_connector_lock(lock_key)
    if not lock_acquired:
        return JSONResponse(
            status_code=409,
            content={
                "error": "Connector update already in progress",
                "code": "CONNECTOR_WRITE_IN_PROGRESS",
            },
        )
    try:
        company_doc, company_error = await _m._load_registry_company_doc(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
        )
        if company_error:
            return company_error
        connectors = company_doc.get("connectors")
        normalized_connectors = dict(connectors) if isinstance(connectors, dict) else {}
        if normalized_connector_id not in normalized_connectors:
            return JSONResponse(
                status_code=404,
                content={"error": "Connector not found", "code": "CONNECTOR_NOT_FOUND"},
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            if company_doc_ref is not None:
                from google.cloud import firestore  # type: ignore

                try:
                    await _m._doc_update(
                        company_doc_ref,
                        {
                            f"connectors.{normalized_connector_id}": firestore.DELETE_FIELD,
                            "updated_at": now_iso,
                        },
                    )
                except Exception:
                    company_doc_ref = None
            if company_doc_ref is None:
                normalized_connectors.pop(normalized_connector_id, None)
                company_doc["connectors"] = normalized_connectors
                company_doc["updated_at"] = now_iso
                await _m._save_registry_company_doc(
                    tenant_id=tenant_id,
                    company_id=normalized_company_id,
                    payload=company_doc,
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
            "connectorId": normalized_connector_id,
            "deleted": True,
        }
        return await _m._idempotency_commit(
            scope="admin_connector_delete",
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            fingerprint=request_fingerprint,
            status_code=200,
            body=body,
        )
    finally:
        await _m._release_connector_lock(lock_key)
