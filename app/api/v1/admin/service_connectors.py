"""Connector service functions — payload normalization and validation.

Extracted from main.py as Phase B3 of modularization. Zero behavior changes.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from app.api.v1.admin.runtime import runtime as _m

from app.api.models import AdminConnectorPayload


_FORBIDDEN_SECRET_KEYS = frozenset({"password", "token", "api_key", "apikey", "secret"})


def _collect_forbidden_secret_paths(value: object, *, prefix: str = "") -> set[str]:
    """Recursively collect inline secret-like keys from nested config payloads."""
    findings: set[str] = set()
    if isinstance(value, dict):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                continue
            key = raw_key.strip()
            normalized = key.lower()
            dotted = f"{prefix}.{key}" if prefix else key
            if normalized in _FORBIDDEN_SECRET_KEYS:
                findings.add(dotted)
            findings.update(_collect_forbidden_secret_paths(child, prefix=dotted))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            item_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            findings.update(_collect_forbidden_secret_paths(child, prefix=item_prefix))
    return findings


def _normalize_connector_payload(
    *,
    connector_id: str,
    payload: AdminConnectorPayload,
    industry_template_id: str | None = None,
) -> tuple[dict[str, object] | None, JSONResponse | None]:
    normalized_connector_id = _m._normalize_connector_id(connector_id) or ""
    if not normalized_connector_id:
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Invalid connector id",
                "code": "INVALID_CONNECTOR_ID",
            },
        )

    provider = payload.provider.strip().lower()
    provider_catalog = _m._effective_mcp_provider_catalog()
    provider_policy = provider_catalog.get(provider)
    if provider_policy is None:
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Connector provider not allowed",
                "code": "CONNECTOR_PROVIDER_NOT_ALLOWED",
                "provider": provider,
            },
        )

    template_policy = _m._template_policy_config(industry_template_id)
    template_allowed_providers = {
        str(item).strip().lower()
        for item in (
            template_policy.get("allowed_provider_ids")
            if isinstance(template_policy.get("allowed_provider_ids"), list)
            else []
        )
        if str(item).strip()
    }
    if template_allowed_providers and provider not in template_allowed_providers:
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Connector provider not allowed for template",
                "code": "CONNECTOR_PROVIDER_NOT_ALLOWED_FOR_TEMPLATE",
                "provider": provider,
                "industryTemplateId": industry_template_id,
            },
        )

    template_allowed_connector_ids = {
        str(item).strip().lower()
        for item in (
            template_policy.get("allowed_connector_ids")
            if isinstance(template_policy.get("allowed_connector_ids"), list)
            else []
        )
        if str(item).strip()
    }
    if template_allowed_connector_ids and normalized_connector_id not in template_allowed_connector_ids:
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Connector id not allowed for template",
                "code": "CONNECTOR_ID_NOT_ALLOWED_FOR_TEMPLATE",
                "connectorId": normalized_connector_id,
                "industryTemplateId": industry_template_id,
            },
        )

    secret_ref = (payload.secret_ref or "").strip()
    requires_secret_ref = bool(provider_policy.get("requiresSecretRef", provider != "mock"))
    if requires_secret_ref and not secret_ref:
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Connector secretRef is required for this provider",
                "code": "CONNECTOR_SECRET_REF_REQUIRED",
                "provider": provider,
            },
        )

    config = payload.config if isinstance(payload.config, dict) else {}
    forbidden_secret_keys = _collect_forbidden_secret_paths(config)
    if forbidden_secret_keys:
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Inline secrets are forbidden in connector config",
                "code": "CONNECTOR_INLINE_SECRET_FORBIDDEN",
                "keys": sorted(forbidden_secret_keys),
            },
        )

    provider_allowed_capabilities = {
        str(cap).strip().lower()
        for cap in (
            provider_policy.get("capabilities")
            if isinstance(provider_policy.get("capabilities"), list)
            else []
        )
        if str(cap).strip()
    }
    normalized_capabilities = [
        str(cap).strip().lower()
        for cap in payload.capabilities
        if str(cap).strip()
    ]
    if provider_allowed_capabilities and any(
        cap not in provider_allowed_capabilities for cap in normalized_capabilities
    ):
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Connector capability not allowed for provider",
                "code": "CONNECTOR_CAPABILITY_NOT_ALLOWED",
                "provider": provider,
            },
        )

    template_allowed_capabilities = {
        str(cap).strip().lower()
        for cap in (
            template_policy.get("max_capabilities")
            if isinstance(template_policy.get("max_capabilities"), list)
            else []
        )
        if str(cap).strip()
    }
    if template_allowed_capabilities and any(
        cap not in template_allowed_capabilities for cap in normalized_capabilities
    ):
        return None, JSONResponse(
            status_code=400,
            content={
                "error": "Connector capability not allowed for template",
                "code": "CONNECTOR_CAPABILITY_NOT_ALLOWED_FOR_TEMPLATE",
                "industryTemplateId": industry_template_id,
            },
        )

    runtime_policy, runtime_policy_error = _m._normalize_connector_test_policy(provider, provider_policy)
    if runtime_policy_error:
        return None, runtime_policy_error

    normalized_payload: dict[str, object] = {
        "id": normalized_connector_id,
        "provider": provider,
        "enabled": bool(payload.enabled),
        "capabilities": normalized_capabilities,
        "config": config,
        "runtime_policy": runtime_policy if isinstance(runtime_policy, dict) else {},
    }
    if secret_ref:
        normalized_payload["secret_ref"] = secret_ref
    return normalized_payload, None
