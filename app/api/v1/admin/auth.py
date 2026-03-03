"""Admin auth subsystem — JWT/IAP authentication, scope checks, rate limiting guard.

Extracted from main.py as Phase A1 of modularization. Zero behavior changes —
same error responses, same auth flow, same status codes.

Design note: all constants (ADMIN_AUTH_MODE, ADMIN_RATE_LIMIT, etc.) stay
defined in main.py so that test monkeypatching via `main_module.CONSTANT = x`
continues to work.  Functions here access them through `_m.CONSTANT` at call
time (not snapshot copies) to respect patches.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Reference to the partially-loaded main module.  Constants and utilities
# (_parse_csv_set, _normalize_tenant_id, _check_rate_limit, google_id_token,
# _GOOGLE_AUTH_REQUEST, ADMIN_*, RATE_LIMIT_WINDOW) are all accessed through
# this reference at call time so monkeypatching main works in tests.
from app.api.v1.admin.runtime import runtime as _m


# ═══ AdminAuthContext ═══

@dataclass(slots=True)
class AdminAuthContext:
    user_id: str
    tenant_id: str
    roles: set[str]
    scopes: set[str]


# ═══ Auth Functions ═══

def _parse_claim_values(raw_value: object) -> set[str]:
    if isinstance(raw_value, str):
        return _m._parse_csv_set(raw_value)
    if isinstance(raw_value, (list, tuple, set)):
        values: set[str] = set()
        for item in raw_value:
            if isinstance(item, str):
                normalized = item.strip().lower()
                if normalized:
                    values.add(normalized)
        return values
    return set()


def _iap_email_from_request(request: Request, claims: dict[str, object]) -> str:
    email_claim = claims.get("email")
    if isinstance(email_claim, str) and email_claim.strip():
        return email_claim.strip().lower()
    header_email = (request.headers.get("x-goog-authenticated-user-email") or "").strip()
    if not header_email:
        return ""
    if ":" in header_email:
        _, _, value = header_email.partition(":")
        return value.strip().lower()
    return header_email.lower()


def _iap_context_from_claims(
    request: Request,
    claims: dict[str, object],
) -> tuple[AdminAuthContext | None, JSONResponse | None]:
    issuer = str(claims.get("iss") or "").strip().lower()
    if _m.ADMIN_IAP_ALLOWED_ISSUERS and issuer not in _m.ADMIN_IAP_ALLOWED_ISSUERS:
        return None, JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "code": "ADMIN_IAP_ISSUER_INVALID",
            },
        )

    email = _iap_email_from_request(request, claims)
    if _m.ADMIN_IAP_ALLOWLIST_EMAILS and email not in _m.ADMIN_IAP_ALLOWLIST_EMAILS:
        return None, JSONResponse(
            status_code=403,
            content={
                "error": "Admin identity not allowed",
                "code": "ADMIN_IDENTITY_FORBIDDEN",
            },
        )

    user_id = str(claims.get("sub") or claims.get("email") or "").strip()
    if not user_id:
        return None, JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "code": "UNAUTHORIZED",
            },
        )

    tenant_candidate = (
        claims.get("tenant_id")
        or claims.get("tenantId")
        or claims.get("https://ekaette.dev/tenant_id")
        or claims.get("https://ekaette.dev/tenantId")
    )
    tenant_id = _m._normalize_tenant_id(tenant_candidate, default=_m.ADMIN_DEFAULT_TENANT_ID)
    if not tenant_id:
        return None, JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "code": "UNAUTHORIZED",
            },
        )

    roles = (
        _parse_claim_values(claims.get("roles"))
        or _parse_claim_values(claims.get("role"))
    )
    scopes = (
        _parse_claim_values(claims.get("scopes"))
        or _parse_claim_values(claims.get("scope"))
    )
    if email and _m.ADMIN_IAP_ALLOWLIST_EMAILS and email in _m.ADMIN_IAP_ALLOWLIST_EMAILS:
        roles.add("tenant_admin")
        scopes.update(_m.ADMIN_IAP_DEFAULT_SCOPES)

    return (
        AdminAuthContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=roles,
            scopes=scopes,
        ),
        None,
    )


def _legacy_admin_context_from_headers(request: Request) -> tuple[AdminAuthContext | None, JSONResponse | None]:
    if _m.ADMIN_REQUIRE_SHARED_SECRET:
        if not _m.ADMIN_SHARED_SECRET:
            return None, JSONResponse(
                status_code=503,
                content={
                    "error": "Admin authentication is not configured",
                    "code": "ADMIN_AUTH_NOT_CONFIGURED",
                },
            )
        provided_secret = (request.headers.get("x-admin-key") or "").strip()
        if not provided_secret or provided_secret != _m.ADMIN_SHARED_SECRET:
            return None, JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "code": "ADMIN_SHARED_SECRET_INVALID",
                },
            )

    user_id = (request.headers.get("x-user-id") or "").strip()
    tenant_id = _m._normalize_tenant_id(request.headers.get("x-tenant-id"), default="")
    if not user_id or not tenant_id:
        return None, JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "code": "UNAUTHORIZED",
            },
        )

    roles = _m._parse_csv_set(request.headers.get("x-roles", ""))
    scopes = _m._parse_csv_set(request.headers.get("x-scopes", ""))
    return (
        AdminAuthContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=roles,
            scopes=scopes,
        ),
        None,
    )


def _verify_iap_jwt_assertion(request: Request) -> tuple[dict[str, object] | None, JSONResponse | None]:
    if not _m.ADMIN_IAP_AUDIENCE:
        return None, JSONResponse(
            status_code=503,
            content={
                "error": "Admin IAP audience is not configured",
                "code": "ADMIN_IAP_NOT_CONFIGURED",
            },
        )
    assertion = (request.headers.get("x-goog-iap-jwt-assertion") or "").strip()
    if not assertion:
        return None, JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "code": "ADMIN_IAP_TOKEN_REQUIRED",
            },
        )
    try:
        claims = _m.google_id_token.verify_token(
            assertion,
            _m._GOOGLE_AUTH_REQUEST,
            audience=_m.ADMIN_IAP_AUDIENCE,
            certs_url=_m.ADMIN_IAP_CERTS_URL,
        )
    except Exception as exc:
        logger.warning("Admin IAP JWT verification failed: %s", exc)
        return None, JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "code": "ADMIN_IAP_TOKEN_INVALID",
            },
        )
    return dict(claims), None


def _extract_admin_auth_context(request: Request) -> tuple[AdminAuthContext | None, JSONResponse | None]:
    cached_context = getattr(request.state, "admin_auth_context", None)
    if isinstance(cached_context, AdminAuthContext):
        return cached_context, None

    mode = _m.ADMIN_AUTH_MODE if _m.ADMIN_AUTH_MODE in {"headers", "iap", "hybrid"} else "headers"

    if mode in {"iap", "hybrid"}:
        if mode == "iap" or request.headers.get("x-goog-iap-jwt-assertion"):
            claims, claims_error = _verify_iap_jwt_assertion(request)
            if claims_error:
                return None, claims_error
            if not isinstance(claims, dict):
                return None, JSONResponse(
                    status_code=401,
                    content={
                        "error": "Unauthorized",
                        "code": "UNAUTHORIZED",
                    },
                )
            return _iap_context_from_claims(request, claims)
    return _legacy_admin_context_from_headers(request)


def _has_admin_scope(context: AdminAuthContext, required_scope: str) -> bool:
    if context.roles.intersection(_m.ADMIN_ALLOWED_ROLE_SET):
        return True
    if required_scope == "write":
        return bool(context.scopes.intersection(_m.ADMIN_WRITE_SCOPE_SET))
    return bool(context.scopes.intersection(_m.ADMIN_READ_SCOPE_SET))


def _admin_context_or_reject(
    request: Request,
    *,
    tenant_id: str,
    required_scope: str = "read",
) -> tuple[AdminAuthContext | None, JSONResponse | None]:
    context, error_response = _extract_admin_auth_context(request)
    if error_response:
        return None, error_response

    if context is None:
        return None, JSONResponse(
            status_code=401,
            content={
                "error": "Unauthorized",
                "code": "UNAUTHORIZED",
            },
        )

    if context.tenant_id != tenant_id:
        return None, JSONResponse(
            status_code=403,
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )

    if required_scope not in {"read", "write"}:
        required_scope = "read"

    if not _has_admin_scope(context, required_scope):
        return None, JSONResponse(
            status_code=403,
            content={
                "error": "Admin scope required",
                "code": "ADMIN_SCOPE_REQUIRED",
                "tenantId": tenant_id,
                "requiredScope": required_scope,
            },
        )

    if _m.ADMIN_RATE_LIMIT > 0:
        rate_limit_identity = f"{context.tenant_id}:{context.user_id}"
        if not _m._check_rate_limit(rate_limit_identity, "admin_api", _m.ADMIN_RATE_LIMIT):
            return None, JSONResponse(
                status_code=429,
                content={
                    "error": "Admin rate limit exceeded",
                    "code": "ADMIN_RATE_LIMIT_EXCEEDED",
                    "tenantId": tenant_id,
                },
                headers={"Retry-After": str(_m.RATE_LIMIT_WINDOW)},
            )

    return context, None


# ═══ Depends() helpers (for future router-level auth) ═══

def _http_error(status_code: int, payload: dict[str, object]) -> HTTPException:
    return HTTPException(status_code=status_code, detail=payload)


def _resolve_tenant_id(request: Request) -> str:
    return _m._normalize_tenant_id(
        request.query_params.get("tenantId"),
        default=_m._normalize_tenant_id(
            request.headers.get("x-tenant-id"),
            default="public",
        ),
    )


async def require_admin_present(request: Request):
    cached = getattr(request.state, "admin_auth_context", None)
    if cached is not None:
        return cached

    context, error_response = _extract_admin_auth_context(request)
    if error_response is not None:
        detail: dict[str, object] = {"error": "Unauthorized", "code": "UNAUTHORIZED"}
        try:
            raw_body = getattr(error_response, "body", b"")
            if isinstance(raw_body, (bytes, bytearray)) and raw_body:
                parsed = json.loads(raw_body.decode("utf-8"))
                if isinstance(parsed, dict):
                    detail = parsed
        except (ValueError, UnicodeDecodeError):
            pass  # Malformed body; fall back to generic detail
        raise _http_error(error_response.status_code, detail)
    if context is None:
        raise _http_error(401, {"error": "Unauthorized", "code": "UNAUTHORIZED"})

    request.state.admin_auth_context = context
    return context


def require_admin_context(required_scope: Literal["read", "write"]) -> Callable:
    async def _dep(request: Request):
        context = await require_admin_present(request)
        tenant_id = _resolve_tenant_id(request)
        if context.tenant_id != tenant_id:
            raise _http_error(
                403,
                {
                    "error": "Tenant not allowed",
                    "code": "TENANT_FORBIDDEN",
                    "tenantId": tenant_id,
                },
            )
        if not _has_admin_scope(context, required_scope):
            raise _http_error(
                403,
                {
                    "error": "Admin scope required",
                    "code": "ADMIN_SCOPE_REQUIRED",
                    "tenantId": tenant_id,
                    "requiredScope": required_scope,
                },
            )
        return context

    return _dep
