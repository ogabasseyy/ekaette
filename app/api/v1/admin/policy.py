"""Policy, circuit breaker, and distributed connector locks.

Extracted from main.py as Phase A4 of modularization. Zero behavior changes.

Constants and mutable state (_policy_cache, _connector_test_circuit_state,
_connector_lock_state) stay in main.py so test monkeypatching works.
Functions here access them through `_m.CONSTANT` at call time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from fastapi.responses import JSONResponse
from google.api_core.exceptions import AlreadyExists

logger = logging.getLogger(__name__)

from app.api.v1.admin.runtime import runtime as _m

from app.api.v1.admin.firestore_helpers import (
    _doc_create,
    _doc_delete,
    _doc_get,
    _doc_set,
)


def _load_policy_document(path: Path) -> dict[str, object]:
    cache_key = str(path.resolve()) if path.is_absolute() else str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        logger.warning("Policy file missing: %s", _m._sanitize_log(str(path)))
        return {}

    cached = _m._policy_cache.get(cache_key)
    if cached and cached[0] == mtime:
        return dict(cached[1])

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed reading policy file %s: %s", _m._sanitize_log(str(path)), exc)
        return {}

    data: object = {}
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(raw_text)
        except Exception as exc:
            logger.warning("Failed parsing policy file %s: %s", _m._sanitize_log(str(path)), exc)
            return {}

    if not isinstance(data, dict):
        logger.warning("Policy file %s must contain an object at root", _m._sanitize_log(str(path)))
        return {}

    normalized_data = dict(data)
    _m._policy_cache[cache_key] = (mtime, normalized_data)
    return normalized_data


def _provider_catalog_from_policy() -> dict[str, dict[str, object]]:
    policy = _load_policy_document(_m.MCP_PROVIDERS_POLICY_PATH)
    raw_providers = policy.get("providers")
    if not isinstance(raw_providers, dict):
        return {}

    catalog: dict[str, dict[str, object]] = {}
    for key, value in raw_providers.items():
        provider_id = str(key).strip().lower()
        if not provider_id or not isinstance(value, dict):
            continue
        capabilities = value.get("capabilities")
        test_policy = value.get("testPolicy")
        normalized_test_policy: dict[str, object] = {}
        if isinstance(test_policy, dict):
            allowed_hosts = test_policy.get("allowedHosts")
            normalized_test_policy = {
                "timeoutSeconds": test_policy.get("timeoutSeconds"),
                "maxRetries": test_policy.get("maxRetries"),
                "circuitOpenAfterFailures": test_policy.get("circuitOpenAfterFailures"),
                "circuitOpenSeconds": test_policy.get("circuitOpenSeconds"),
                "allowedHosts": [
                    str(host).strip().lower()
                    for host in (allowed_hosts if isinstance(allowed_hosts, list) else [])
                    if str(host).strip()
                ],
            }
        catalog[provider_id] = {
            "id": provider_id,
            "label": str(value.get("label") or provider_id.replace("-", " ").title()),
            "status": str(value.get("status") or "active"),
            "requiresSecretRef": bool(value.get("requiresSecretRef", provider_id != "mock")),
            "capabilities": [
                str(cap).strip().lower()
                for cap in (capabilities if isinstance(capabilities, list) else [])
                if str(cap).strip()
            ],
            "testPolicy": normalized_test_policy,
        }
    return catalog


def _normalize_connector_test_policy(
    provider: str,
    provider_policy: dict[str, object],
) -> tuple[dict[str, object] | None, JSONResponse | None]:
    raw_policy = provider_policy.get("testPolicy")
    if not isinstance(raw_policy, dict):
        if _m.CONNECTOR_TEST_REQUIRE_POLICY and provider != "mock":
            return None, JSONResponse(
                status_code=503,
                content={
                    "error": "Connector provider test policy is missing",
                    "code": "CONNECTOR_PROVIDER_POLICY_MISSING",
                    "provider": provider,
                },
            )
        raw_policy = {}

    def _safe_float(value: object, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _safe_int(value: object, default: int, minimum: int = 0) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= minimum else default

    normalized_policy = {
        "timeoutSeconds": _safe_float(raw_policy.get("timeoutSeconds"), _m.CONNECTOR_TEST_TIMEOUT_SECONDS),
        "maxRetries": _safe_int(raw_policy.get("maxRetries"), _m.CONNECTOR_TEST_MAX_RETRIES, minimum=0),
        "circuitOpenAfterFailures": _safe_int(
            raw_policy.get("circuitOpenAfterFailures"),
            _m.CONNECTOR_TEST_CIRCUIT_OPEN_AFTER_FAILURES,
            minimum=1,
        ),
        "circuitOpenSeconds": _safe_int(
            raw_policy.get("circuitOpenSeconds"),
            _m.CONNECTOR_TEST_CIRCUIT_OPEN_SECONDS,
            minimum=1,
        ),
        "allowedHosts": [
            str(host).strip().lower()
            for host in (
                raw_policy.get("allowedHosts")
                if isinstance(raw_policy.get("allowedHosts"), list)
                else []
            )
            if str(host).strip()
        ],
    }
    return normalized_policy, None


def _connector_circuit_key(tenant_id: str, company_id: str, connector_id: str) -> str:
    return f"{tenant_id}:{company_id}:{connector_id}"


def _connector_circuit_doc_ref(db: object, *, circuit_key: str):
    doc_id = hashlib.sha256(circuit_key.encode("utf-8")).hexdigest()
    return (
        db.collection("_runtime")
        .document("connector_circuit")
        .collection("entries")
        .document(doc_id)
    )


def _connector_lock_doc_ref(db: object, *, lock_key: str):
    doc_id = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()
    return (
        db.collection("_runtime")
        .document("connector_locks")
        .collection("entries")
        .document(doc_id)
    )


def _connector_circuit_uses_firestore() -> bool:
    return _m.CONNECTOR_CIRCUIT_BACKEND == "firestore"


def _connector_lock_uses_firestore() -> bool:
    return _m.CONNECTOR_LOCK_BACKEND == "firestore"


def _connector_circuit_retry_after_seconds_memory(circuit_key: str) -> int:
    now_epoch = time.time()
    state = _m._connector_test_circuit_state.get(circuit_key)
    if not isinstance(state, dict):
        return 0
    open_until = state.get("open_until")
    if not isinstance(open_until, (float, int)):
        return 0
    remaining = int(open_until - now_epoch)
    return remaining if remaining > 0 else 0


async def _connector_circuit_retry_after_seconds(circuit_key: str) -> int:
    if not _connector_circuit_uses_firestore():
        return _connector_circuit_retry_after_seconds_memory(circuit_key)

    db = _m._registry_db_client()
    if db is None:
        return _connector_circuit_retry_after_seconds_memory(circuit_key)
    try:
        snapshot = await _doc_get(_connector_circuit_doc_ref(db, circuit_key=circuit_key))
        if not getattr(snapshot, "exists", False):
            return 0
        payload = snapshot.to_dict()
    except Exception as exc:
        logger.warning("Failed reading connector circuit state from Firestore: %s", exc)
        return _connector_circuit_retry_after_seconds_memory(circuit_key)
    if not isinstance(payload, dict):
        return 0
    now_epoch = time.time()
    open_until = payload.get("open_until_epoch")
    if not isinstance(open_until, (float, int)):
        return 0
    remaining = int(float(open_until) - now_epoch)
    return remaining if remaining > 0 else 0


async def _connector_circuit_is_open(circuit_key: str) -> bool:
    return await _connector_circuit_retry_after_seconds(circuit_key) > 0


def _connector_circuit_record_failure_memory(
    circuit_key: str,
    circuit_open_after_failures: int,
    circuit_open_seconds: int,
) -> None:
    now_epoch = time.time()
    state = _m._connector_test_circuit_state.get(circuit_key)
    failures = 0
    if isinstance(state, dict):
        failures = int(state.get("failures", 0))
    failures += 1
    open_until = now_epoch + circuit_open_seconds if failures >= max(1, circuit_open_after_failures) else 0.0
    _m._connector_test_circuit_state[circuit_key] = {"failures": failures, "open_until": open_until}


async def _connector_circuit_record_failure(
    circuit_key: str,
    circuit_open_after_failures: int,
    circuit_open_seconds: int,
) -> None:
    if not _connector_circuit_uses_firestore():
        _connector_circuit_record_failure_memory(
            circuit_key,
            circuit_open_after_failures,
            circuit_open_seconds,
        )
        return
    db = _m._registry_db_client()
    if db is None:
        _connector_circuit_record_failure_memory(
            circuit_key,
            circuit_open_after_failures,
            circuit_open_seconds,
        )
        return
    now_epoch = time.time()
    doc_ref = _connector_circuit_doc_ref(db, circuit_key=circuit_key)
    try:
        snapshot = await _doc_get(doc_ref)
        payload = snapshot.to_dict() if getattr(snapshot, "exists", False) else {}
        failures = int(payload.get("failures", 0)) if isinstance(payload, dict) else 0
        failures += 1
        open_until = (
            now_epoch + circuit_open_seconds
            if failures >= max(1, circuit_open_after_failures)
            else 0.0
        )
        await _doc_set(
            doc_ref,
            {
                "circuit_key": circuit_key,
                "failures": failures,
                "open_until_epoch": open_until,
                "updated_at_epoch": now_epoch,
            },
            merge=True,
        )
    except Exception as exc:
        logger.warning("Failed writing connector circuit state to Firestore: %s", exc)
        _connector_circuit_record_failure_memory(
            circuit_key,
            circuit_open_after_failures,
            circuit_open_seconds,
        )


async def _connector_circuit_record_success(circuit_key: str) -> None:
    if _connector_circuit_uses_firestore():
        db = _m._registry_db_client()
        if db is not None:
            try:
                await _doc_delete(_connector_circuit_doc_ref(db, circuit_key=circuit_key))
                return
            except Exception as exc:
                logger.warning("Failed clearing connector circuit state from Firestore: %s", exc)
    _m._connector_test_circuit_state.pop(circuit_key, None)


async def _acquire_connector_lock(lock_key: str) -> bool:
    now_epoch = time.time()
    if not _connector_lock_uses_firestore():
        with _m._connector_lock_state_guard:
            stale_keys = [key for key, expiry in _m._connector_lock_state.items() if expiry <= now_epoch]
            for stale in stale_keys:
                _m._connector_lock_state.pop(stale, None)
            if lock_key in _m._connector_lock_state:
                return False
            _m._connector_lock_state[lock_key] = now_epoch + _m.CONNECTOR_LOCK_TTL_SECONDS
            return True

    db = _m._registry_db_client()
    if db is None:
        with _m._connector_lock_state_guard:
            stale_keys = [key for key, expiry in _m._connector_lock_state.items() if expiry <= now_epoch]
            for stale in stale_keys:
                _m._connector_lock_state.pop(stale, None)
            if lock_key in _m._connector_lock_state:
                return False
            _m._connector_lock_state[lock_key] = now_epoch + _m.CONNECTOR_LOCK_TTL_SECONDS
            return True
    doc_ref = _connector_lock_doc_ref(db, lock_key=lock_key)
    payload = {
        "lock_key": lock_key,
        "created_at_epoch": now_epoch,
        "expires_at_epoch": now_epoch + _m.CONNECTOR_LOCK_TTL_SECONDS,
    }
    try:
        await _doc_create(doc_ref, payload)
        return True
    except AlreadyExists:
        try:
            snapshot = await _doc_get(doc_ref)
            existing = snapshot.to_dict() if getattr(snapshot, "exists", False) else {}
            expires_at = float(existing.get("expires_at_epoch", 0) or 0) if isinstance(existing, dict) else 0.0
            if expires_at <= now_epoch:
                await _doc_delete(doc_ref)
                await _doc_create(doc_ref, payload)
                return True
        except Exception:
            return False
        return False
    except Exception as exc:
        logger.warning("Failed acquiring connector lock in Firestore: %s", exc)
        return False


async def _release_connector_lock(lock_key: str) -> None:
    if not _connector_lock_uses_firestore():
        with _m._connector_lock_state_guard:
            _m._connector_lock_state.pop(lock_key, None)
        return
    db = _m._registry_db_client()
    if db is None:
        with _m._connector_lock_state_guard:
            _m._connector_lock_state.pop(lock_key, None)
        return
    try:
        await _doc_delete(_connector_lock_doc_ref(db, lock_key=lock_key))
    except Exception:
        return


async def _execute_connector_test_probe(
    *,
    provider: str,
    tenant_id: str,
    company_id: str,
    connector_id: str,
    connector: dict[str, object],
) -> dict[str, object]:
    """Execute non-mock connector health probe.

    Production connector integrations should be implemented here with provider
    SDK/http clients and secret manager retrieval.
    """
    raise NotImplementedError(
        f"Provider '{provider}' test flow is not implemented for connector '{connector_id}'."
    )


def _effective_mcp_provider_catalog() -> dict[str, dict[str, object]]:
    catalog = _provider_catalog_from_policy() or dict(_m.MCP_PROVIDER_CATALOG)
    if _m.MCP_PROVIDER_ALLOWLIST:
        catalog = {k: v for k, v in catalog.items() if k in _m.MCP_PROVIDER_ALLOWLIST}
    return catalog


def _template_policy_config(template_id: str | None) -> dict[str, object]:
    normalized_template = _m._normalize_template_id(template_id)
    if not normalized_template:
        return {}
    policy = _load_policy_document(_m.CAPABILITY_MATRIX_POLICY_PATH)
    templates = policy.get("templates")
    if not isinstance(templates, dict):
        return {}
    template_policy = templates.get(normalized_template)
    return dict(template_policy) if isinstance(template_policy, dict) else {}


# Legacy re-export for backward compat
def effective_mcp_provider_catalog():
    return _effective_mcp_provider_catalog()
