"""Shared admin helpers for normalization, origin checks, and runtime access."""

from __future__ import annotations

import logging
import re
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from app.configs import registry_enabled, sanitize_log
from app.observability import registry_metric_labels

from . import settings

logger = logging.getLogger(__name__)

DEFAULT_COMPANY_ID = (
    (settings.ADMIN_DEFAULT_TENANT_ID and "default") or "default"
)
_COMPANY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_TEMPLATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,127}$")
_CONNECTOR_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")

industry_config_client = None
company_config_client = None


def sync_runtime_clients(*, industry_client: object | None, company_client: object | None) -> None:
    """Synchronize runtime Firestore clients from app lifespan state."""
    global industry_config_client, company_config_client
    industry_config_client = industry_client
    company_config_client = company_client


def _registry_enabled() -> bool:
    return registry_enabled()


def _registry_db_client() -> object | None:
    return company_config_client or industry_config_client


def _normalize_company_id(raw_value: object) -> str:
    if not isinstance(raw_value, str):
        return DEFAULT_COMPANY_ID
    normalized = raw_value.strip().lower()
    if not normalized:
        return DEFAULT_COMPANY_ID
    if not _COMPANY_ID_PATTERN.fullmatch(normalized):
        return DEFAULT_COMPANY_ID
    return normalized


def _normalize_tenant_id(raw_value: object, default: str = "public") -> str:
    if not isinstance(raw_value, str):
        return default
    normalized = raw_value.strip().lower()
    if not normalized:
        return default
    if not _TENANT_ID_PATTERN.fullmatch(normalized):
        return default
    return normalized


def _normalize_template_id(raw_value: object) -> str | None:
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if not _TEMPLATE_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _normalize_company_id_strict(raw_value: object) -> str | None:
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if not _COMPANY_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _normalize_connector_id(raw_value: object) -> str | None:
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if not _CONNECTOR_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _sanitize_log(value: str | None) -> str:
    return sanitize_log(value)


def _parse_csv_set(raw_value: str) -> set[str]:
    return {
        item.strip().lower()
        for item in raw_value.split(",")
        if isinstance(item, str) and item.strip()
    }


def _is_origin_allowed(origin: str | None) -> bool:
    if origin is None:
        return True
    return origin in settings.ALLOWED_ORIGIN_SET


def _origin_or_reject(origin: str | None, *, endpoint: str) -> JSONResponse | None:
    if origin is None:
        logger.debug(
            "HTTP request accepted without Origin header endpoint=%s",
            sanitize_log(endpoint),
        )
        return None
    if not _is_origin_allowed(origin):
        return JSONResponse(status_code=403, content={"error": "Origin not allowed"})
    return None


def _tenant_allowed(tenant_id: str) -> bool:
    return not settings.TOKEN_ALLOWED_TENANTS or tenant_id in settings.TOKEN_ALLOWED_TENANTS


_rate_limit_lock = threading.Lock()


def _check_rate_limit(client_ip: str, bucket: str, limit: int) -> bool:
    now = time.time()
    key = f"{bucket}:{client_ip}"

    with _rate_limit_lock:
        if now - settings._rate_limit_last_global_prune >= settings.RATE_LIMIT_WINDOW:
            stale_keys = [
                existing_key
                for existing_key, values in settings._rate_limit_buckets.items()
                if not values or (now - values[-1]) >= settings.RATE_LIMIT_WINDOW
            ]
            for stale_key in stale_keys:
                settings._rate_limit_buckets.pop(stale_key, None)
            settings._rate_limit_last_global_prune = now

        if key not in settings._rate_limit_buckets and len(settings._rate_limit_buckets) >= settings.RATE_LIMIT_MAX_BUCKETS:
            oldest_key = min(
                settings._rate_limit_buckets.keys(),
                key=lambda existing_key: settings._rate_limit_buckets[existing_key][-1]
                if settings._rate_limit_buckets[existing_key]
                else 0.0,
                default=None,
            )
            if oldest_key is not None:
                settings._rate_limit_buckets.pop(oldest_key, None)

        timestamps = settings._rate_limit_buckets.get(key, [])
        timestamps = [t for t in timestamps if now - t < settings.RATE_LIMIT_WINDOW]
        if len(timestamps) >= limit:
            settings._rate_limit_buckets[key] = timestamps
            return False
        timestamps.append(now)
        settings._rate_limit_buckets[key] = timestamps
        return True


def _client_ip_from_request(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if isinstance(forwarded_for, str) and forwarded_for.strip():
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    return request.client.host if request.client else "unknown"


def build_admin_observability_fields(
    *,
    tenant_id: str | None,
    company_id: str | None,
    industry_template_id: str | None,
    route: str,
    method: str,
    auth_mode: str,
    idempotency_scope: str,
    idempotency_state: str,
    result_code: str,
    status_code: int,
) -> dict[str, str]:
    labels = registry_metric_labels(
        tenant_id=tenant_id,
        company_id=company_id,
        industry_template_id=industry_template_id,
        registry_mode=_registry_enabled(),
        source="admin_api",
    )
    labels.update(
        {
            "route": sanitize_log(route),
            "method": sanitize_log(method),
            "auth_mode": sanitize_log(auth_mode),
            "idempotency_scope": sanitize_log(idempotency_scope),
            "idempotency_state": sanitize_log(idempotency_state),
            "result_code": sanitize_log(result_code),
            "status_code": str(int(status_code)),
        }
    )
    return labels


def format_observability_fields(fields: dict[str, str]) -> str:
    return " ".join(f"{key}={value}" for key, value in fields.items())
