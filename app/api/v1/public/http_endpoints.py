"""Public HTTP endpoint handlers extracted from main.py.

Behavior-preserving handlers used by thin route delegates in main.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def configure_runtime(**kwargs: Any) -> None:
    """Inject runtime dependencies from main module."""
    globals().update(kwargs)


async def create_ephemeral_token(
    payload: Any,
    request: Request,
):
    """Issue a constrained short-lived Gemini Live API auth token."""
    blocked_origin = _origin_or_reject(request.headers.get("origin"), endpoint="api_token")
    if blocked_origin:
        return blocked_origin

    client_ip = _client_ip_from_request(request)
    if not _check_rate_limit(client_ip, "token", TOKEN_RATE_LIMIT):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "retryAfter": RATE_LIMIT_WINDOW},
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )

    if TOKEN_ALLOWED_TENANTS and payload.tenant_id not in TOKEN_ALLOWED_TENANTS:
        logger.warning(
            "Token request rejected (tenant forbidden) %s",
            registry_log_context(
                tenant_id=payload.tenant_id,
                registry_mode=_registry_enabled(),
                source="api_token",
            ),
        )
        return JSONResponse(
            status_code=403,
            content={"error": "Tenant not allowed"},
        )

    if TOKEN_CLIENT is None:
        return JSONResponse(
            status_code=500,
            content={"error": "Server API key not configured"},
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SECONDS)
    new_session_expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=TOKEN_NEW_SESSION_TTL_SECONDS
    )
    normalized_tenant_id = _normalize_tenant_id(payload.tenant_id, default="public")
    normalized_company_id = _normalize_company_id(payload.company_id)
    requested_template_id = _normalize_template_id(payload.industry_template_id)
    requested_industry = (payload.industry or "electronics").strip().lower() or "electronics"

    try:
        registry_config = await _resolve_registry_runtime_config(
            tenant_id=normalized_tenant_id,
            company_id=normalized_company_id,
        )
    except RegistrySchemaVersionError as exc:
        logger.warning(
            "Token request rejected (registry schema version) %s details=%s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                company_id=normalized_company_id,
                registry_mode=_registry_enabled(),
                source="api_token",
            ),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Unsupported registry schema version",
                "code": getattr(exc, "code", "REGISTRY_SCHEMA_VERSION_UNSUPPORTED"),
                "tenantId": normalized_tenant_id,
                "companyId": normalized_company_id,
                "details": str(exc),
            },
        )
    if _registry_enabled() and registry_config is None:
        logger.warning(
            "Token request rejected (registry config missing) %s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                company_id=normalized_company_id,
                registry_mode=True,
                source="api_token",
            ),
        )
        return JSONResponse(
            status_code=404,
            content={
                "error": "Company configuration not found in registry",
                "code": "REGISTRY_CONFIG_NOT_FOUND",
                "tenantId": normalized_tenant_id,
                "companyId": normalized_company_id,
            },
        )
    if registry_config is not None:
        mismatch_response = _registry_mismatch_response(
            requested_template_id=requested_template_id,
            resolved_template_id=getattr(registry_config, "industry_template_id", None),
        )
        if mismatch_response is not None:
            logger.warning(
                "Token request rejected (template/company mismatch) %s requested_template_id=%s resolved_template_id=%s",
                registry_log_context(
                    tenant_id=normalized_tenant_id,
                    company_id=normalized_company_id,
                    industry_template_id=getattr(registry_config, "industry_template_id", None),
                    registry_version=getattr(registry_config, "registry_version", None),
                    registry_mode=True,
                    source="api_token",
                ),
                _sanitize_log(requested_template_id),
                _sanitize_log(getattr(registry_config, "industry_template_id", None)),
            )
            return mismatch_response

    resolved_voice = (
        getattr(registry_config, "voice", None)
        if isinstance(getattr(registry_config, "voice", None), str)
        else None
    )
    live_connect_config_kwargs = _native_audio_live_config(
        requested_industry,
        voice_override=resolved_voice,
    )
    if REALTIME_INPUT_CONFIG is not None:
        live_connect_config_kwargs["realtime_input_config"] = REALTIME_INPUT_CONFIG

    auth_token = None
    selected_model = LIVE_MODEL_CANDIDATES[0]
    last_exc: Exception | None = None
    for index, candidate_model in enumerate(LIVE_MODEL_CANDIDATES):
        try:
            live_constraints = types.LiveConnectConstraints(
                model=candidate_model,
                config=types.LiveConnectConfig(**live_connect_config_kwargs),
            )
            auth_token = await TOKEN_CLIENT.aio.auth_tokens.create(
                config=types.CreateAuthTokenConfig(
                    uses=TOKEN_MAX_USES,
                    expire_time=expires_at,
                    new_session_expire_time=new_session_expires_at,
                    live_connect_constraints=live_constraints,
                )
            )
            selected_model = candidate_model
            if index > 0:
                logger.warning(
                    "Token creation fell back to model '%s' after primary failure",
                    candidate_model,
                )
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Token creation failed for model '%s': %s",
                candidate_model,
                exc,
            )
            continue

    if auth_token is None:
        logger.error("Token creation failed for all model candidates: %s", last_exc)
        return JSONResponse(
            status_code=502,
            content={"error": "Failed to create ephemeral token"},
        )

    if not auth_token.name:
        return JSONResponse(
            status_code=502,
            content={"error": "Token response was empty"},
        )

    resolved_legacy_industry = requested_industry
    if registry_config is not None:
        resolved_legacy_industry = _legacy_industry_alias_from_registry_config(
            registry_config,
            fallback=requested_industry,
        )

    response: dict[str, object] = {
        "token": auth_token.name,
        "expiresAt": expires_at.isoformat(),
        "maxUses": TOKEN_MAX_USES,
        "industry": resolved_legacy_industry,
        "companyId": normalized_company_id,
        "tenantId": normalized_tenant_id,
        "userId": payload.user_id,
        "model": selected_model,
        "fallbackModelUsed": selected_model != LIVE_MODEL_CANDIDATES[0],
        "manualVadActive": MANUAL_VAD_ACTIVE,
        "vadMode": "manual" if MANUAL_VAD_ACTIVE else "auto",
    }

    # Include a signed WS auth token when WS_TOKEN_SECRET is configured.
    # Import as module to avoid shadowing TOKEN_TTL_SECONDS injected by
    # configure_runtime() — a bare `from .settings import TOKEN_TTL_SECONDS`
    # would create a local binding that shadows the globals() value and cause
    # UnboundLocalError on line 60.
    from . import settings as _pub_settings
    from .ws_auth import create_ws_token

    if _pub_settings.WS_TOKEN_SECRET:
        ws_ttl = (
            _pub_settings.WS_TOKEN_TTL_SECONDS
            if _pub_settings.WS_TOKEN_TTL_SECONDS > 0
            else TOKEN_TTL_SECONDS
        )
        response["wsToken"] = create_ws_token(
            user_id=payload.user_id,
            tenant_id=normalized_tenant_id,
            company_id=normalized_company_id,
            ttl_seconds=ws_ttl,
        )

    response["voice"] = resolved_voice or _voice_for_industry(resolved_legacy_industry)
    if registry_config is not None:
        template_id = getattr(registry_config, "industry_template_id", None)
        capabilities = getattr(registry_config, "capabilities", None)
        registry_version = getattr(registry_config, "registry_version", None)
        if isinstance(template_id, str) and template_id:
            response["industryTemplateId"] = template_id
        if isinstance(capabilities, list):
            response["capabilities"] = capabilities
        if isinstance(registry_version, str) and registry_version:
            response["registryVersion"] = registry_version

    return response


async def get_onboarding_config(request: Request):
    """Return industry templates + companies for the onboarding UI."""
    blocked_origin = _origin_or_reject(request.headers.get("origin"), endpoint="api_onboarding")
    if blocked_origin:
        return blocked_origin

    tenant_id = request.query_params.get("tenantId")
    if not tenant_id:
        logger.warning(
            "Onboarding config request rejected (missing tenantId) %s",
            registry_log_context(registry_mode=_registry_enabled(), source="api_onboarding"),
        )
        return JSONResponse(
            status_code=400,
            content={"error": "Missing required query parameter: tenantId"},
        )
    normalized_tenant_id = _normalize_tenant_id(tenant_id, default="public")
    if not _tenant_allowed(normalized_tenant_id):
        logger.warning(
            "Onboarding config request rejected (tenant forbidden) %s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                registry_mode=_registry_enabled(),
                source="api_onboarding",
            ),
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "Tenant not allowed",
                "tenantId": normalized_tenant_id,
            },
        )

    from app.configs.registry_loader import RegistryDataMissingError, build_onboarding_config

    try:
        config = await build_onboarding_config(industry_config_client, normalized_tenant_id)
    except RegistryDataMissingError as exc:
        logger.warning(
            "Onboarding config unavailable %s code=%s details=%s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                registry_mode=_registry_enabled(),
                source="api_onboarding",
            ),
            getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry onboarding config unavailable",
                "code": getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
                "tenantId": normalized_tenant_id,
                "details": str(exc),
            },
        )
    return config


async def get_runtime_bootstrap(request: Request):
    """Return authoritative runtime context for end-user app initialization."""
    blocked_origin = _origin_or_reject(request.headers.get("origin"), endpoint="api_runtime_bootstrap")
    if blocked_origin:
        return blocked_origin

    tenant_id = _normalize_tenant_id(request.query_params.get("tenantId"), default="public")
    if not _tenant_allowed(tenant_id):
        logger.warning(
            "Runtime bootstrap rejected (tenant forbidden) %s",
            registry_log_context(
                tenant_id=tenant_id,
                registry_mode=_registry_enabled(),
                source="api_runtime_bootstrap",
            ),
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "Tenant not allowed",
                "code": "TENANT_FORBIDDEN",
                "tenantId": tenant_id,
            },
        )

    from app.configs.registry_loader import RegistryDataMissingError, build_onboarding_config

    try:
        onboarding = await build_onboarding_config(industry_config_client, tenant_id)
    except RegistryDataMissingError as exc:
        logger.warning(
            "Runtime bootstrap unavailable %s code=%s details=%s",
            registry_log_context(
                tenant_id=tenant_id,
                registry_mode=_registry_enabled(),
                source="api_runtime_bootstrap",
            ),
            getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry runtime bootstrap unavailable",
                "code": getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
                "tenantId": tenant_id,
                "details": str(exc),
            },
        )

    companies = onboarding.get("companies", [])
    defaults = onboarding.get("defaults", {})
    requested_company_id = request.query_params.get("companyId")
    resolved_company_id = _resolve_company_for_bootstrap(
        requested_company_id=requested_company_id,
        companies=companies if isinstance(companies, list) else [],
        defaults=defaults if isinstance(defaults, dict) else None,
    )

    if requested_company_id and not resolved_company_id:
        return JSONResponse(
            status_code=404,
            content={
                "error": "Company not found for tenant",
                "code": "COMPANY_NOT_FOUND",
                "tenantId": tenant_id,
                "companyId": _normalize_company_id(requested_company_id),
            },
        )

    if not resolved_company_id:
        allowed_companies = [
            item
            for item in (companies if isinstance(companies, list) else [])
            if isinstance(item, dict) and item.get("id")
        ]
        return JSONResponse(
            status_code=409,
            content={
                "error": "Runtime company selection required",
                "code": "NEED_COMPANY_SELECTION",
                "tenantId": tenant_id,
                "onboardingRequired": True,
                "companies": allowed_companies,
            },
        )

    company_meta = next(
        (
            item
            for item in (companies if isinstance(companies, list) else [])
            if isinstance(item, dict) and _normalize_company_id(item.get("id")) == resolved_company_id
        ),
        None,
    )
    fallback_template = ""
    if isinstance(company_meta, dict):
        fallback_template = _normalize_template_id(company_meta.get("templateId")) or ""
    if not fallback_template and isinstance(defaults, dict):
        fallback_template = _normalize_template_id(defaults.get("templateId")) or ""
    requested_template = _normalize_template_id(request.query_params.get("industryTemplateId"))
    effective_template = requested_template or fallback_template or "electronics"

    try:
        registry_config = await _resolve_registry_runtime_config(
            tenant_id=tenant_id,
            company_id=resolved_company_id,
        )
    except RegistrySchemaVersionError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Unsupported registry schema version",
                "code": getattr(exc, "code", "REGISTRY_SCHEMA_VERSION_UNSUPPORTED"),
                "tenantId": tenant_id,
                "companyId": resolved_company_id,
                "details": str(exc),
            },
        )

    if _registry_enabled() and registry_config is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Company configuration not found in registry",
                "code": "REGISTRY_CONFIG_NOT_FOUND",
                "tenantId": tenant_id,
                "companyId": resolved_company_id,
            },
        )

    if registry_config is not None:
        mismatch_response = _registry_mismatch_response(
            requested_template_id=requested_template,
            resolved_template_id=getattr(registry_config, "industry_template_id", None),
        )
        if mismatch_response is not None:
            return mismatch_response

    resolved_template_id = effective_template
    resolved_industry = effective_template
    resolved_voice = _voice_for_industry(effective_template)
    resolved_capabilities: list[str] = []
    resolved_registry_version: str | None = None

    if registry_config is not None:
        template_id = getattr(registry_config, "industry_template_id", None)
        if isinstance(template_id, str) and template_id:
            resolved_template_id = template_id
        resolved_industry = _legacy_industry_alias_from_registry_config(
            registry_config,
            fallback=resolved_template_id,
        )
        voice = getattr(registry_config, "voice", None)
        if isinstance(voice, str) and voice:
            resolved_voice = voice
        capabilities = getattr(registry_config, "capabilities", None)
        if isinstance(capabilities, list):
            resolved_capabilities = [str(c) for c in capabilities]
        registry_version = getattr(registry_config, "registry_version", None)
        if isinstance(registry_version, str) and registry_version:
            resolved_registry_version = registry_version
    else:
        template_meta = next(
            (
                item
                for item in (onboarding.get("templates", []) if isinstance(onboarding, dict) else [])
                if isinstance(item, dict)
                and _normalize_template_id(item.get("id")) == resolved_template_id
            ),
            None,
        )
        if isinstance(template_meta, dict):
            category = _normalize_template_id(template_meta.get("category"))
            if category:
                resolved_industry = category
            default_voice = template_meta.get("defaultVoice")
            if isinstance(default_voice, str) and default_voice.strip():
                resolved_voice = default_voice
            caps = template_meta.get("capabilities")
            if isinstance(caps, list):
                resolved_capabilities = [str(c) for c in caps]

    response: dict[str, object] = {
        "apiVersion": "v1",
        "tenantId": tenant_id,
        "companyId": resolved_company_id,
        "industryTemplateId": resolved_template_id,
        "industry": resolved_industry,
        "voice": resolved_voice,
        "capabilities": resolved_capabilities,
        "onboardingRequired": False,
        "sessionPolicy": {
            "industryLocked": True,
            "companyLocked": True,
            "switchRequiresDisconnect": True,
        },
    }
    if resolved_registry_version:
        response["registryVersion"] = resolved_registry_version
    return response


async def validate_upload(
    request: Request,
    file: UploadFile,
):
    """Validate upload MIME and file size before any storage write."""
    blocked_origin = _origin_or_reject(
        request.headers.get("origin"), endpoint="api_upload_validate"
    )
    if blocked_origin:
        return blocked_origin

    client_ip = _client_ip_from_request(request)
    if not _check_rate_limit(client_ip, "upload", UPLOAD_RATE_LIMIT):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "retryAfter": RATE_LIMIT_WINDOW},
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )

    mime_type = file.content_type or "application/octet-stream"
    data = await file.read(MAX_UPLOAD_BYTES + 1)

    try:
        _validate_upload_bytes(mime_type, data)
    except ValueError as exc:
        code = str(exc)
        if code == "UPLOAD_TOO_LARGE":
            return JSONResponse(
                status_code=413,
                content={"error": "Upload exceeds max size", "maxBytes": MAX_UPLOAD_BYTES},
            )
        if code == "MIME_TYPE_NOT_ALLOWED":
            return JSONResponse(
                status_code=415,
                content={
                    "error": "MIME type not allowed",
                    "allowedMimeTypes": sorted(ALLOWED_UPLOAD_MIME_TYPES),
                },
            )
        return JSONResponse(status_code=400, content={"error": "Invalid upload payload"})
    finally:
        await file.close()

    return {
        "status": "ok",
        "filename": file.filename,
        "mimeType": mime_type,
        "sizeBytes": len(data),
    }
