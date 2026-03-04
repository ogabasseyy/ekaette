"""Internal scheduler-triggered inventory sync routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.models import AdminInventorySyncRunPayload
from app.api.v1.admin.runtime import runtime as _m

router = APIRouter(
    prefix="/api/v1/internal",
    tags=["internal"],
)


def _bearer_token_from_request(request: Request) -> str:
    header = (request.headers.get("authorization") or "").strip()
    if not header:
        return ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _verify_inventory_sync_internal_auth(
    request: Request,
) -> tuple[tuple[str, str] | None, JSONResponse | None]:
    mode = str(getattr(_m, "INVENTORY_SYNC_INTERNAL_AUTH_MODE", "oidc")).strip().lower()
    if mode not in {"oidc", "shared_secret", "hybrid"}:
        mode = "oidc"

    shared_secret = str(getattr(_m, "INVENTORY_SYNC_INTERNAL_SHARED_SECRET", "")).strip()
    shared_header = (request.headers.get("x-inventory-sync-key") or "").strip()
    if mode in {"shared_secret", "hybrid"} and shared_secret:
        if shared_header and shared_header == shared_secret:
            return ("shared_secret", "shared-secret"), None
        if mode == "shared_secret":
            return None, JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "code": "INVENTORY_SYNC_INTERNAL_AUTH_REQUIRED"},
            )

    if mode in {"oidc", "hybrid"}:
        token = _bearer_token_from_request(request)
        if not token:
            return None, JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "code": "INVENTORY_SYNC_INTERNAL_AUTH_REQUIRED"},
            )
        audience = str(getattr(_m, "INVENTORY_SYNC_INTERNAL_AUDIENCE", "")).strip() or str(
            request.url.replace(query="")
        )
        try:
            claims = _m.google_id_token.verify_oauth2_token(
                token,
                _m._GOOGLE_AUTH_REQUEST,
                audience=audience,
            )
        except Exception:
            return None, JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "code": "INVENTORY_SYNC_INTERNAL_AUTH_INVALID"},
            )
        if not isinstance(claims, dict):
            claims = {}
        email = str(claims.get("email") or "").strip().lower()
        allowed = getattr(_m, "INVENTORY_SYNC_INTERNAL_ALLOWED_SERVICE_ACCOUNTS", set())
        allowed_set = allowed if isinstance(allowed, set) else set()
        if allowed_set and email not in allowed_set:
            return None, JSONResponse(
                status_code=403,
                content={
                    "error": "Service account not allowed",
                    "code": "INVENTORY_SYNC_INTERNAL_AUTH_FORBIDDEN",
                },
            )
        subject = email or str(claims.get("sub") or "").strip() or "unknown"
        return ("oidc", subject), None

    return None, JSONResponse(
        status_code=401,
        content={"error": "Unauthorized", "code": "INVENTORY_SYNC_INTERNAL_AUTH_REQUIRED"},
    )


@router.post("/inventory/sync/run")
async def run_internal_inventory_sync_route(
    payload: AdminInventorySyncRunPayload,
    request: Request,
):
    if not bool(getattr(_m, "INVENTORY_SYNC_INTERNAL_ENABLED", False)):
        return JSONResponse(
            status_code=404,
            content={"error": "Not found", "code": "INTERNAL_ROUTE_DISABLED"},
        )

    tenant_id = _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=getattr(_m, "ADMIN_DEFAULT_TENANT_ID", "public"),
    )
    if not _m._tenant_allowed(tenant_id):
        return JSONResponse(
            status_code=403,
            content={"error": "Tenant not allowed", "code": "TENANT_FORBIDDEN"},
        )

    auth_identity, auth_error = _verify_inventory_sync_internal_auth(request)
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

    max_companies_cap = int(getattr(_m, "INVENTORY_SYNC_INTERNAL_MAX_COMPANIES", 250))
    max_companies = max(1, min(int(payload.max_companies), max_companies_cap))
    try:
        result = await _m._run_inventory_sync_jobs(
            tenant_id=tenant_id,
            company_id=normalized_company_id,
            max_companies=max_companies,
            force=bool(payload.force),
            dry_run_override=payload.dry_run_override,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc), "code": "INVENTORY_SYNC_RUN_INVALID"})
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Registry storage unavailable", "code": "REGISTRY_STORAGE_UNAVAILABLE"},
        )

    mode, subject = auth_identity if auth_identity else ("unknown", "unknown")
    return JSONResponse(
        status_code=200,
        content={
            "apiVersion": "v1",
            "authMode": mode,
            "authSubject": subject,
            **result,
        },
    )
