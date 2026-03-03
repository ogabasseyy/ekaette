"""Typed settings + mutable runtime state for admin API internals.

This module centralizes admin configuration and in-memory state that used to
live in ``main.py``. It is intentionally importable by admin modules without
creating a dependency on the application entrypoint.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_allowlist(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _parse_csv_set(raw_value: str) -> set[str]:
    return {
        item.strip().lower()
        for item in raw_value.split(",")
        if isinstance(item, str) and item.strip()
    }


class AdminSettings(BaseSettings):
    """Typed env-driven admin settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    admin_allowed_roles: str = Field(default="tenant_admin", alias="ADMIN_ALLOWED_ROLES")
    admin_rate_limit: int = Field(default=120, alias="ADMIN_RATE_LIMIT")
    admin_shared_secret: str = Field(default="", alias="ADMIN_SHARED_SECRET")
    admin_require_shared_secret: bool = Field(
        default_factory=lambda: (os.getenv("K_SERVICE") is not None),
        alias="ADMIN_REQUIRE_SHARED_SECRET",
    )
    admin_auth_mode: str = Field(
        default_factory=lambda: ("iap" if os.getenv("K_SERVICE") else "headers"),
        alias="ADMIN_AUTH_MODE",
    )
    admin_iap_audience: str = Field(default="", alias="ADMIN_IAP_AUDIENCE")
    admin_iap_certs_url: str = Field(
        default="https://www.gstatic.com/iap/verify/public_key-jwk",
        alias="ADMIN_IAP_CERTS_URL",
    )
    admin_iap_allowed_issuers: str = Field(
        default="https://cloud.google.com/iap",
        alias="ADMIN_IAP_ALLOWED_ISSUERS",
    )
    admin_iap_allowlist_emails: str = Field(default="", alias="ADMIN_IAP_ALLOWLIST_EMAILS")
    admin_iap_default_scopes: str = Field(
        default="admin:read,admin:write",
        alias="ADMIN_IAP_DEFAULT_SCOPES",
    )
    admin_default_tenant_id: str = Field(default="public", alias="ADMIN_DEFAULT_TENANT_ID")

    mcp_provider_allowlist: str = Field(default="mock", alias="MCP_PROVIDER_ALLOWLIST")
    mcp_providers_policy_path: str = Field(
        default="policies/mcp_providers.v1.yaml",
        alias="MCP_PROVIDERS_POLICY_PATH",
    )
    capability_matrix_policy_path: str = Field(
        default="policies/capability_matrix.v1.yaml",
        alias="CAPABILITY_MATRIX_POLICY_PATH",
    )

    connector_test_require_policy: bool = Field(default=True, alias="CONNECTOR_TEST_REQUIRE_POLICY")
    connector_test_timeout_seconds: float = Field(default=2.0, alias="CONNECTOR_TEST_TIMEOUT_SECONDS")
    connector_test_max_retries: int = Field(default=1, alias="CONNECTOR_TEST_MAX_RETRIES")
    connector_test_circuit_open_after_failures: int = Field(
        default=2,
        alias="CONNECTOR_TEST_CIRCUIT_OPEN_AFTER_FAILURES",
    )
    connector_test_circuit_open_seconds: int = Field(
        default=20,
        alias="CONNECTOR_TEST_CIRCUIT_OPEN_SECONDS",
    )
    connector_circuit_backend: str = Field(
        default_factory=lambda: ("firestore" if os.getenv("K_SERVICE") else "memory"),
        alias="CONNECTOR_CIRCUIT_BACKEND",
    )
    connector_lock_backend: str = Field(
        default_factory=lambda: ("firestore" if os.getenv("K_SERVICE") else "memory"),
        alias="CONNECTOR_LOCK_BACKEND",
    )
    connector_lock_ttl_seconds: int = Field(default=30, alias="CONNECTOR_LOCK_TTL_SECONDS")

    idempotency_ttl_seconds: int = Field(default=86400, alias="IDEMPOTENCY_TTL_SECONDS")
    idempotency_pending_ttl_seconds: int = Field(default=120, alias="IDEMPOTENCY_PENDING_TTL_SECONDS")
    idempotency_store_backend: str = Field(
        default_factory=lambda: ("firestore" if os.getenv("K_SERVICE") else "memory"),
        alias="IDEMPOTENCY_STORE_BACKEND",
    )

    knowledge_import_max_bytes: int = Field(default=1_048_576, alias="KNOWLEDGE_IMPORT_MAX_BYTES")
    inventory_import_max_bytes: int = Field(default=5_242_880, alias="INVENTORY_IMPORT_MAX_BYTES")
    inventory_sync_http_timeout_seconds: float = Field(
        default=20.0,
        alias="INVENTORY_SYNC_HTTP_TIMEOUT_SECONDS",
    )
    inventory_sync_internal_enabled: bool = Field(default=False, alias="INVENTORY_SYNC_INTERNAL_ENABLED")
    inventory_sync_internal_auth_mode: str = Field(default="oidc", alias="INVENTORY_SYNC_INTERNAL_AUTH_MODE")
    inventory_sync_internal_shared_secret: str = Field(
        default="",
        alias="INVENTORY_SYNC_INTERNAL_SHARED_SECRET",
    )
    inventory_sync_internal_audience: str = Field(default="", alias="INVENTORY_SYNC_INTERNAL_AUDIENCE")
    inventory_sync_internal_allowed_service_accounts: str = Field(
        default="",
        alias="INVENTORY_SYNC_INTERNAL_ALLOWED_SERVICE_ACCOUNTS",
    )
    inventory_sync_internal_max_companies: int = Field(
        default=250,
        alias="INVENTORY_SYNC_INTERNAL_MAX_COMPANIES",
    )

    token_allowed_tenants: str = Field(default="public", alias="TOKEN_ALLOWED_TENANTS")

    allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
        alias="ALLOWED_ORIGINS",
    )
    rate_limit_window: int = Field(default=60, alias="RATE_LIMIT_WINDOW")
    rate_limit_max_buckets: int = Field(default=5000, alias="RATE_LIMIT_MAX_BUCKETS")


_cfg = AdminSettings()

ADMIN_ALLOWED_ROLE_SET = _parse_csv_set(_cfg.admin_allowed_roles) or {"tenant_admin"}
ADMIN_ALLOWED_SCOPES = {"admin:read", "admin:write", "admin:*"}
ADMIN_READ_SCOPE_SET = {"admin:read", "admin:write", "admin:*"}
ADMIN_WRITE_SCOPE_SET = {"admin:write", "admin:*"}

ADMIN_RATE_LIMIT = int(_cfg.admin_rate_limit)
ADMIN_SHARED_SECRET = (_cfg.admin_shared_secret or "").strip()
ADMIN_REQUIRE_SHARED_SECRET = bool(_cfg.admin_require_shared_secret)
ADMIN_AUTH_MODE = (_cfg.admin_auth_mode or "headers").strip().lower()
ADMIN_IAP_AUDIENCE = (_cfg.admin_iap_audience or "").strip()
ADMIN_IAP_CERTS_URL = (_cfg.admin_iap_certs_url or "").strip()
ADMIN_IAP_ALLOWED_ISSUERS = _parse_csv_set(_cfg.admin_iap_allowed_issuers)
ADMIN_IAP_ALLOWLIST_EMAILS = _parse_csv_set(_cfg.admin_iap_allowlist_emails)
ADMIN_IAP_DEFAULT_SCOPES = _parse_csv_set(_cfg.admin_iap_default_scopes)
ADMIN_DEFAULT_TENANT_ID = (_cfg.admin_default_tenant_id or "public").strip().lower() or "public"
_GOOGLE_AUTH_REQUEST = GoogleAuthRequest()

MCP_PROVIDER_ALLOWLIST = _parse_csv_set(_cfg.mcp_provider_allowlist) or {"mock"}
MCP_PROVIDER_CATALOG: dict[str, dict[str, object]] = {
    "mock": {
        "id": "mock",
        "label": "Mock Provider",
        "status": "active",
        "requiresSecretRef": False,
        "capabilities": ["read"],
    },
    "salesforce": {
        "id": "salesforce",
        "label": "Salesforce",
        "status": "preview",
        "requiresSecretRef": True,
        "capabilities": ["read", "write"],
    },
    "hubspot": {
        "id": "hubspot",
        "label": "HubSpot",
        "status": "preview",
        "requiresSecretRef": True,
        "capabilities": ["read", "write"],
    },
    "zendesk": {
        "id": "zendesk",
        "label": "Zendesk",
        "status": "preview",
        "requiresSecretRef": True,
        "capabilities": ["read", "write"],
    },
}

MCP_PROVIDERS_POLICY_PATH = Path(_cfg.mcp_providers_policy_path)
CAPABILITY_MATRIX_POLICY_PATH = Path(_cfg.capability_matrix_policy_path)
_policy_cache: dict[str, tuple[float, dict[str, object]]] = {}

CONNECTOR_TEST_REQUIRE_POLICY = bool(_cfg.connector_test_require_policy)
CONNECTOR_TEST_TIMEOUT_SECONDS = float(_cfg.connector_test_timeout_seconds)
CONNECTOR_TEST_MAX_RETRIES = int(_cfg.connector_test_max_retries)
CONNECTOR_TEST_CIRCUIT_OPEN_AFTER_FAILURES = int(_cfg.connector_test_circuit_open_after_failures)
CONNECTOR_TEST_CIRCUIT_OPEN_SECONDS = int(_cfg.connector_test_circuit_open_seconds)
CONNECTOR_CIRCUIT_BACKEND = (_cfg.connector_circuit_backend or "memory").strip().lower()
CONNECTOR_LOCK_BACKEND = (_cfg.connector_lock_backend or "memory").strip().lower()
CONNECTOR_LOCK_TTL_SECONDS = int(_cfg.connector_lock_ttl_seconds)
_connector_test_circuit_state: dict[str, dict[str, object]] = {}
_connector_lock_state: dict[str, float] = {}
_connector_lock_state_guard = threading.Lock()

IDEMPOTENCY_TTL_SECONDS = int(_cfg.idempotency_ttl_seconds)
IDEMPOTENCY_PENDING_TTL_SECONDS = int(_cfg.idempotency_pending_ttl_seconds)
IDEMPOTENCY_STORE_BACKEND = (_cfg.idempotency_store_backend or "memory").strip().lower()
_idempotency_store: dict[str, dict[str, object]] = {}
_idempotency_store_lock = threading.Lock()

KNOWLEDGE_IMPORT_MAX_BYTES = int(_cfg.knowledge_import_max_bytes)
INVENTORY_IMPORT_MAX_BYTES = int(_cfg.inventory_import_max_bytes)
INVENTORY_SYNC_HTTP_TIMEOUT_SECONDS = float(_cfg.inventory_sync_http_timeout_seconds)
INVENTORY_SYNC_INTERNAL_ENABLED = bool(_cfg.inventory_sync_internal_enabled)
INVENTORY_SYNC_INTERNAL_AUTH_MODE = (_cfg.inventory_sync_internal_auth_mode or "oidc").strip().lower()
INVENTORY_SYNC_INTERNAL_SHARED_SECRET = (_cfg.inventory_sync_internal_shared_secret or "").strip()
INVENTORY_SYNC_INTERNAL_AUDIENCE = (_cfg.inventory_sync_internal_audience or "").strip()
INVENTORY_SYNC_INTERNAL_ALLOWED_SERVICE_ACCOUNTS = _parse_csv_set(
    _cfg.inventory_sync_internal_allowed_service_accounts
)
INVENTORY_SYNC_INTERNAL_MAX_COMPANIES = max(1, min(int(_cfg.inventory_sync_internal_max_companies), 500))

TOKEN_ALLOWED_TENANTS = set(_parse_allowlist(_cfg.token_allowed_tenants))

ALLOWED_ORIGINS = _parse_allowlist(_cfg.allowed_origins)
ALLOWED_ORIGIN_SET = set(ALLOWED_ORIGINS)
RATE_LIMIT_WINDOW = int(_cfg.rate_limit_window)
RATE_LIMIT_MAX_BUCKETS = int(_cfg.rate_limit_max_buckets)
_rate_limit_buckets: dict[str, list[float]] = {}
_rate_limit_last_global_prune = 0.0

__all__ = [
    "google_id_token",
    "ADMIN_ALLOWED_ROLE_SET",
    "ADMIN_ALLOWED_SCOPES",
    "ADMIN_READ_SCOPE_SET",
    "ADMIN_WRITE_SCOPE_SET",
    "ADMIN_RATE_LIMIT",
    "ADMIN_SHARED_SECRET",
    "ADMIN_REQUIRE_SHARED_SECRET",
    "ADMIN_AUTH_MODE",
    "ADMIN_IAP_AUDIENCE",
    "ADMIN_IAP_CERTS_URL",
    "ADMIN_IAP_ALLOWED_ISSUERS",
    "ADMIN_IAP_ALLOWLIST_EMAILS",
    "ADMIN_IAP_DEFAULT_SCOPES",
    "ADMIN_DEFAULT_TENANT_ID",
    "_GOOGLE_AUTH_REQUEST",
    "MCP_PROVIDER_ALLOWLIST",
    "MCP_PROVIDER_CATALOG",
    "MCP_PROVIDERS_POLICY_PATH",
    "CAPABILITY_MATRIX_POLICY_PATH",
    "_policy_cache",
    "CONNECTOR_TEST_REQUIRE_POLICY",
    "CONNECTOR_TEST_TIMEOUT_SECONDS",
    "CONNECTOR_TEST_MAX_RETRIES",
    "CONNECTOR_TEST_CIRCUIT_OPEN_AFTER_FAILURES",
    "CONNECTOR_TEST_CIRCUIT_OPEN_SECONDS",
    "CONNECTOR_CIRCUIT_BACKEND",
    "CONNECTOR_LOCK_BACKEND",
    "CONNECTOR_LOCK_TTL_SECONDS",
    "_connector_test_circuit_state",
    "_connector_lock_state",
    "_connector_lock_state_guard",
    "IDEMPOTENCY_TTL_SECONDS",
    "IDEMPOTENCY_PENDING_TTL_SECONDS",
    "IDEMPOTENCY_STORE_BACKEND",
    "_idempotency_store",
    "_idempotency_store_lock",
    "KNOWLEDGE_IMPORT_MAX_BYTES",
    "INVENTORY_IMPORT_MAX_BYTES",
    "INVENTORY_SYNC_HTTP_TIMEOUT_SECONDS",
    "INVENTORY_SYNC_INTERNAL_ENABLED",
    "INVENTORY_SYNC_INTERNAL_AUTH_MODE",
    "INVENTORY_SYNC_INTERNAL_SHARED_SECRET",
    "INVENTORY_SYNC_INTERNAL_AUDIENCE",
    "INVENTORY_SYNC_INTERNAL_ALLOWED_SERVICE_ACCOUNTS",
    "INVENTORY_SYNC_INTERNAL_MAX_COMPANIES",
    "TOKEN_ALLOWED_TENANTS",
    "ALLOWED_ORIGINS",
    "ALLOWED_ORIGIN_SET",
    "RATE_LIMIT_WINDOW",
    "RATE_LIMIT_MAX_BUCKETS",
    "_rate_limit_buckets",
    "_rate_limit_last_global_prune",
]


def reset_runtime_state() -> None:
    """Reset mutable in-memory state (used by tests)."""
    global _rate_limit_last_global_prune

    _rate_limit_buckets.clear()
    _idempotency_store.clear()
    _connector_test_circuit_state.clear()
    _connector_lock_state.clear()
    _rate_limit_last_global_prune = 0.0
    # Keep runtime symbol->module cache clean between tests/mode switches.
    try:
        from app.api.v1.admin.runtime import runtime

        runtime.clear_resolution_cache()
    except Exception:
        # Best-effort cache clear; state reset should not fail on import timing.
        pass


def admin_settings_dict() -> dict[str, Any]:
    """Return a serializable snapshot for diagnostics/tests."""
    return {
        "admin_auth_mode": ADMIN_AUTH_MODE,
        "admin_rate_limit": ADMIN_RATE_LIMIT,
        "connector_circuit_backend": CONNECTOR_CIRCUIT_BACKEND,
        "connector_lock_backend": CONNECTOR_LOCK_BACKEND,
        "idempotency_store_backend": IDEMPOTENCY_STORE_BACKEND,
    }
