# Plan: Complete main.py Modularization (Strangler-Fig Phase 2+3)

## Context

The current state after two agents' work:

- **main.py: 6,366 lines** — grew from 2,010 (committed) due to new admin API, JWT/IAP auth, Firestore-backed idempotency, distributed connector locks
- **app/api/ package: 572 lines** — thin proxy shell with 26 `import main as main_module` lazy imports
- All 20 admin handler functions + ~2,040 lines of admin infrastructure still live in main.py
- Route wiring is correct — 623 tests pass, parity test confirms 20 admin routes serve through APIRouter
- `deps.py` exists but is unused; 3 of 4 service stubs are empty
- Pydantic models properly extracted to `models.py` (one real win)
- Tests monkeypatch `main_module.*` — 20+ symbols patched across test files

**Phase 1 (routing shell) is done.** This plan completes **Phase 2 (logic extraction)** and **Phase 3 (cleanup)** using an incremental approach with compatibility re-exports to avoid test breakage.

## Addressing the Other Agent's Concerns

| Concern | Resolution |
|---------|-----------|
| Admin count is 20, not 21 | Confirmed: 20 admin routes + `validate_upload` (non-admin). Using 20. |
| Lifespan + app.state migration under-specified for tests | Tests use `TestClient(app)` which triggers lifespan. Compatibility re-exports in main.py bridge the gap. Tests keep patching `main_module.*` until Phase 3. |
| Auth/idempotency extraction risks regressing hardening | Zero logic changes — pure cut-paste + import update. JWT/IAP flow, Firestore idempotency, distributed locks preserved exactly. |
| idempotency_guard exception pattern not decision-complete | **Not doing the Depends() pattern yet.** Keep current `_idempotency_begin()`/`_idempotency_commit()` call pattern inside handlers. Behavioral change deferred to a future task. |
| Big-bang test migration too late | Each extraction adds re-exports in main.py immediately. Tests never break. Test updates happen in a separate phase. |
| service.py (~800 lines) recreates monolith | Split into 5 focused service modules (120-200 lines each). |
| Router-level + per-handler auth ambiguous | **Not using router-level Depends() yet.** Current `_admin_context_or_reject()` pattern inside handlers is preserved. Conversion to Depends() is a future optimization. |

## Design Principle: Zero Behavior Changes

Every extraction step is a **pure structural move**:
1. Cut function from main.py -> paste into target module
2. Add re-export in main.py: `from app.api.v1.admin.auth import _extract_admin_auth_context`
3. Tests continue to monkeypatch `main_module._extract_admin_auth_context` -> works because the re-export is the same object
4. Run tests after each move

No error response changes. No auth flow changes. No Depends() conversion. No HTTPException migration. Just moving code.

## Target Module Structure

```
main.py                                     (~1,400 lines — app init, public routes, WS)
app/api/
  __init__.py                               (empty, exists)
  deps.py                                   (24 lines, exists, unused — keep for future)
  models.py                                 (113 lines, exists, complete)
  v1/
    __init__.py                             (empty, exists)
    admin/
      __init__.py                           (~30 lines — admin_router, compose sub-routers)
      auth.py                               (~300 lines — AdminAuthContext, JWT/IAP, scope check)
      idempotency.py                        (~400 lines — store, Firestore backend, begin/commit)
      policy.py                             (~400 lines — policy cache, circuit breaker, locks)
      firestore_helpers.py                  (~130 lines — _doc_get/set/create/update/delete, batch ops)
      service_companies.py                  (~200 lines — company payload/response, load/save/upsert)
      service_knowledge.py                  (~120 lines — write/delete/list knowledge entries)
      service_connectors.py                 (~180 lines — normalize connector payload, connector CRUD helpers)
      service_data.py                       (~200 lines — import products/slots, purge, export, retention)
      routes/
        __init__.py                         (exists)
        companies.py                        (~600 lines — 5 actual handlers, not trampolines)
        knowledge.py                        (~400 lines — 5 actual handlers)
        connectors.py                       (~500 lines — 4 actual handlers)
        data.py                             (~450 lines — 6 actual handlers)
      services/                             (DELETE — replaced by service_*.py flat modules)
```

## Implementation Order

### Phase A: Extract infrastructure (auth -> idempotency -> policy -> firestore)

These are self-contained subsystems with clear boundaries.

**Step A1: Auth subsystem -> `app/api/v1/admin/auth.py`**
Move from main.py (~290 lines):
- `AdminAuthContext` dataclass (remove duplicate already in auth.py)
- `_parse_claim_values`, `_iap_email_from_request`, `_iap_context_from_claims`
- `_legacy_admin_context_from_headers`, `_verify_iap_jwt_assertion`
- `_extract_admin_auth_context`, `_has_admin_scope`, `_admin_context_or_reject`
- Config constants: `ADMIN_AUTH_MODE`, `ADMIN_IAP_*`, `ADMIN_ALLOWED_ROLE_SET`, `ADMIN_SHARED_SECRET`, etc.
- **Dependencies from main.py:** `_check_rate_limit`, `_client_ip_from_request`, `_sanitize_log` — import from main
- **Re-export in main.py:** `from app.api.v1.admin.auth import AdminAuthContext, _extract_admin_auth_context, _has_admin_scope, _admin_context_or_reject`
- **Run tests**

**Step A2: Firestore helpers -> `app/api/v1/admin/firestore_helpers.py`**
Move from main.py (~130 lines):
- `_doc_get`, `_doc_set`, `_doc_create`, `_doc_update`, `_doc_delete`
- `_batch_set_documents`, `_batch_delete_documents`
- **No dependencies from main.py** — these are pure Firestore wrappers
- **Re-export in main.py**
- **Run tests**

**Step A3: Idempotency subsystem -> `app/api/v1/admin/idempotency.py`**
Move from main.py (~390 lines):
- `_idempotency_store` dict, `IDEMPOTENCY_TTL_SECONDS`, `IDEMPOTENCY_STORE_BACKEND`
- All `_idempotency_*` functions (fingerprint, prune, doc_ref, uses_firestore, memory/firestore variants)
- `_idempotency_key_dependency`, `_idempotency_begin`, `_idempotency_commit`
- **Dependencies from main.py:** `_registry_db_client` — import from main
- **Re-export in main.py**
- **Run tests**

**Step A4: Policy + circuit breaker + locks -> `app/api/v1/admin/policy.py`**
Move from main.py (~400 lines):
- `_policy_cache`, `_connector_test_circuit_state`, `_connector_lock_state` dicts
- Policy loading: `_load_policy_document`, `_provider_catalog_from_policy`, `_effective_mcp_provider_catalog`, `_template_policy_config`
- Connector test config: `_normalize_connector_test_policy`, `CONNECTOR_TEST_*` constants
- Circuit breaker: `_connector_circuit_*` functions (key, retry_after, is_open, record_failure, record_success)
- Locks: `_acquire_connector_lock`, `_release_connector_lock`, `CONNECTOR_LOCK_BACKEND`
- `_execute_connector_test_probe` (async stub)
- **Dependencies from main.py:** `_registry_db_client` — import from main
- **Re-export in main.py**
- **Run tests**

**Checkpoint A: main.py should be ~4,950 lines (down from 6,366). All 623 tests pass.**
**Gate 2 checked here:** `pytest tests/test_admin_v1_contracts.py tests/test_main.py -v` must pass without test modifications.

### Phase B: Extract service modules

**Step B1: `service_companies.py`** (~200 lines)
- `_admin_company_payload`, `_admin_company_response`
- `_load_registry_company_doc`, `_save_registry_company_doc`
- `_upsert_registry_company_doc`
- `_resolve_company_for_bootstrap` (used by bootstrap route — stays importable)
- **Imports from:** `firestore_helpers` (_doc_get, _doc_set, etc.), main (_registry_db_client, _normalize_company_id_strict, etc.)

**Step B2: `service_knowledge.py`** (~120 lines)
- `_normalize_tags`, `_write_company_knowledge_entry`, `_delete_company_knowledge_entry`
- `_list_company_collection_docs`, `_collect_query_docs`
- **Imports from:** `firestore_helpers`

**Step B3: `service_connectors.py`** (~180 lines)
- `_normalize_connector_payload` (the big one, ~150 lines)
- **Imports from:** `policy` (_effective_mcp_provider_catalog, _template_policy_config)

**Step B4: `service_data.py`** (~200 lines)
- `_import_company_runtime_docs`, `_import_company_products`, `_import_company_booking_slots`
- `_purge_company_demo_runtime_data`
- `_parse_timestamp_utc`
- `_export_company_bundle`, `_delete_company_bundle`
- `_purge_company_retention_data`
- **Imports from:** `firestore_helpers`, `service_knowledge` (_collect_query_docs, _list_company_collection_docs)

**Each step:** move functions, add re-exports in main.py, run tests.

**Checkpoint B: main.py should be ~4,200 lines. All tests pass.**

### Phase C: Move handlers into route modules

Replace the trampoline pattern with actual handler code. Each route file imports from the service modules instead of from main.

**Step C1: `routes/companies.py`** — move 5 handlers from main.py
**Step C2: `routes/knowledge.py`** — move 5 handlers
**Step C3: `routes/connectors.py`** — move 4 handlers
**Step C4: `routes/data.py`** — move 6 handlers

Each handler:
- Imports auth functions from `auth.py`
- Imports idempotency functions from `idempotency.py`
- Imports service functions from `service_*.py`
- Imports shared utils from `main` (origin checks, normalization, tenant validation)
- **Add re-export in main.py** for each handler function (tests monkeypatch these)

**Checkpoint C: main.py should be ~1,400 lines. All tests pass.**
**Gate 1 checked here:** `grep -n "^async def.*admin\|^def.*admin" main.py` must return zero matches.

### Phase D: Update tests + cleanup

**Step D1:** Update `tests/test_admin_v1_contracts.py` — change monkeypatch targets from `main_module` to the actual modules where functions now live

**Step D2:** Update `tests/test_main.py` — fix mutable state clearing for moved dicts (idempotency_store, circuit_state, lock_state)

**Step D3:** Delete re-exports from main.py (the `from app.api.v1.admin.* import ...` lines)

**Step D4:** Delete dead code:
- Empty `services/` directory (the old stub layer)
- Unused proxy functions in auth.py, idempotency.py, policy.py (the old 11-line trampolines)

**Step D5:** Update `test_admin_router_parity.py` if needed

**Final checkpoint: main.py ~1,400 lines, all 623+ tests pass, zero re-exports.**

## What Stays in main.py

```python
# ~1,400 lines total
- Imports + logging setup (~50 lines)
- Lifespan + app.state + CORS middleware (~100 lines)
- Shared utilities: normalization, origin checks, tenant validation, rate limiting (~300 lines)
- Registry resolution: _resolve_registry_runtime_config, _registry_mismatch_response, etc. (~200 lines)
- Token endpoint helpers + /api/token handler (~350 lines)
- /api/onboarding/config handler (~70 lines)
- /api/v1/runtime/bootstrap handler (~200 lines)
- /api/upload/validate handler (~50 lines)
- app.include_router(admin_router)
- WebSocket /ws/{user_id}/{session_id} (~930 lines — too coupled to extract now)
- Static frontend mount (~10 lines)
```

## Verification

```bash
# After each step:
cd "/Users/mac/Downloads/Ekaette/Ekaette " && python -m pytest tests/ -v --tb=short -q

# After Phase C (handlers moved):
wc -l main.py  # should be ~1,400

# After Phase D (cleanup):
cd "/Users/mac/Downloads/Ekaette/Ekaette /frontend" && npx vitest run

# Verify all routes mounted:
python -c "
from main import app
admin = [r for r in app.routes if hasattr(r, 'path') and '/admin/' in r.path]
print(f'Admin routes: {len(admin)}')
assert len(admin) >= 20
"

# Verify Dockerfile compatibility:
python -c "import main; assert hasattr(main, 'app'); print('OK')"

# Verify no circular imports at module load:
python -c "from app.api.v1.admin import admin_router; print(f'Router OK: {len(admin_router.routes)} routes')"
```

## Acceptance Gates

### Gate 1: No admin handler body remains in main.py
After Phase C, run:
```bash
grep -n "^async def.*admin\|^def.*admin" main.py
```
**Must return zero matches.** All 20 admin handler functions must exist ONLY in `app/api/v1/admin/routes/*.py`. Re-exports of the function name are allowed (for test compat), but the function body (the actual `async def` with business logic) must not be in main.py.

### Gate 2: Auth/idempotency behavior is byte-for-byte equivalent
After Phase A (infrastructure extraction), verify:
```bash
cd "/Users/mac/Downloads/Ekaette/Ekaette "
python -m pytest tests/test_admin_v1_contracts.py tests/test_main.py -v --tb=short -q
```
All existing auth and idempotency tests must pass **without any test modifications**. If a test needs changing to pass, the extraction introduced a behavioral regression — stop and fix.

These gates are checked at Checkpoint C (after handler extraction) and Checkpoint A (after infrastructure extraction) respectively.

## Risk Mitigation

- **Zero behavior changes** — error responses, auth flow, status codes all identical
- **Re-exports prevent test breakage** — `main_module.foo` works throughout migration via re-exports
- **One subsystem at a time** — test checkpoint after every step
- **No Depends() conversion** — deferred to future task to avoid changing error contract
- **No router-level auth** — deferred to future task to avoid changing auth enforcement order
- **WebSocket stays in main.py** — too coupled to extract in this pass (~930 lines, uses 12+ singletons)
