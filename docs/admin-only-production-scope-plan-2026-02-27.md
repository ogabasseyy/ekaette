# Admin-Only Provisioning + Zero End-User Onboarding (2026 Production-Complete, v2)

## Summary
This is the upgraded, decision-complete plan with all 2026 production controls added.

- End-user app skips onboarding and uses server bootstrap.
- Admin portal is the source of truth for company/template/knowledge/runtime data/connectors.
- MCP is controlled by provider allowlist + policy enforcement.
- Security, authz, observability, versioning, DR, governance, and rollout controls are explicit and testable.

## Important Changes / Additions to Public APIs / Interfaces / Types

## 1. API versioning strategy (new, explicit)
All new endpoints are versioned from day 1:
- `/api/v1/runtime/bootstrap`
- `/api/v1/admin/...`

Version policy:
- Additive changes only in minor releases.
- Breaking changes require `/api/v2/...`.
- Response includes:
  - `apiVersion: "v1"`
  - `schemaVersion` where relevant (template/company/connectors)

## 2. Runtime bootstrap endpoint (v1)
### `GET /api/v1/runtime/bootstrap`
Returns authoritative runtime context:
- `apiVersion`
- `tenantId`
- `companyId`
- `industryTemplateId`
- `industry` (legacy alias)
- `voice`
- `capabilities`
- `registryVersion`
- `onboardingRequired`
- `sessionPolicy` (lock flags)

Error contract:
- `403 TENANT_FORBIDDEN`
- `404 COMPANY_NOT_FOUND`
- `409 NEED_COMPANY_SELECTION`
- `503 REGISTRY_CONFIG_NOT_FOUND`

## 3. Admin API surface (v1)
All endpoints require tenant-admin role and tenant ownership checks.

- Company config:
  - `GET /api/v1/admin/companies`
  - `POST /api/v1/admin/companies`
  - `GET /api/v1/admin/companies/{companyId}`
  - `PUT /api/v1/admin/companies/{companyId}`

- Knowledge:
  - `POST /api/v1/admin/companies/{companyId}/knowledge/import-text`
  - `POST /api/v1/admin/companies/{companyId}/knowledge/import-url`
  - `POST /api/v1/admin/companies/{companyId}/knowledge/import-file`
  - `GET /api/v1/admin/companies/{companyId}/knowledge`
  - `DELETE /api/v1/admin/companies/{companyId}/knowledge/{knowledgeId}`

- Runtime data:
  - `POST /api/v1/admin/companies/{companyId}/products/import`
  - `POST /api/v1/admin/companies/{companyId}/booking-slots/import`
  - `POST /api/v1/admin/companies/{companyId}/runtime/purge-demo`

- Connectors/MCP (allowlist):
  - `GET /api/v1/admin/mcp/providers`
  - `POST /api/v1/admin/companies/{companyId}/connectors`
  - `PUT /api/v1/admin/companies/{companyId}/connectors/{connectorId}`
  - `POST /api/v1/admin/companies/{companyId}/connectors/{connectorId}/test`
  - `DELETE /api/v1/admin/companies/{companyId}/connectors/{connectorId}`

## 4. Idempotency keys for mutating admin APIs (new)
For write/import endpoints:
- Require `Idempotency-Key` header.
- Server stores key hash + request fingerprint + result for TTL window (24h).
- Repeat same key+payload returns original result.
- Same key with different payload returns `409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`.

## 5. Interface/type additions
Add/standardize:
- `RuntimeBootstrapResponseV1`
- `AdminCompanyV1`
- `AdminConnectorV1`
- `McpProviderDefinitionV1`
- `ApiErrorV1 { code, message, details?, traceId }`
- `AuthContext { userId, tenantId, roles, scopes }`

## Security + Auth Architecture (Required Controls)

## A. Auth model contract (OIDC/JWT)
Use JWT claims contract:
- `sub` -> user id
- `tenant_id` -> tenant scope
- `roles` -> includes `tenant_admin` for admin APIs
- `scopes` -> granular permission checks

Rules:
- Missing/invalid token -> `401`.
- Tenant mismatch -> `403`.
- Company not owned by tenant -> `404`.

Session/token lifecycle:
- Access token TTL: short-lived (for example 15m)
- Refresh token handled by auth layer (not app custom)
- WebSocket authenticates before session bootstrap; no trust in query params.

## B. CSRF policy (explicit)
If browser cookie auth is used for admin APIs:
- CSRF token required for mutating requests.
- `SameSite=Lax/Strict` + secure cookies in prod.
If bearer tokens only:
- CSRF not required, but enforce strict CORS + origin policy + auth headers.

## C. Connector/MCP security controls
- Allowlist only: no arbitrary MCP URLs in v1.
- `secretRef` required for non-mock providers.
- No raw secrets in Firestore/config/logs.
- Connector settings schema-validated per provider.
- Per-provider capability ceilings enforced server-side.

## D. Egress/network controls
- Outbound destination allowlist by provider.
- Per-provider:
  - timeout budgets
  - retry policy (bounded exponential backoff)
  - circuit breaker thresholds
- Hard fail-closed if provider policy unavailable.

## E. Policy-as-code (new)
Store provider allowlist and capability policies as versioned config:
- `policies/mcp_providers.v1.json`
- `policies/capability_matrix.v1.yaml`
Changes require:
- code review
- changelog entry
- audit record

## Data Governance + Compliance Controls

## A. PII classification
Classify stored data:
- P0 secrets (Secret Manager only)
- P1 customer identifiers
- P2 operational metadata
Annotate collections and fields in docs.

## B. Retention/deletion
Define retention per data class:
- transcripts/events (for example 30/90 days as decided)
- knowledge docs (until deleted by admin)
- connector test logs (short retention)
Add scheduled purge jobs and legal-hold override path.

## C. Tenant data export/delete workflows
Admin endpoints or internal runbooks for:
- tenant export
- tenant/company deletion
- verification receipts and audit logging

## DR / Reliability Controls

## A. RTO/RPO targets (explicit)
Initial targets:
- RTO: 2 hours
- RPO: 15 minutes

## B. Backup/restore drills
- Monthly restore validation in non-prod from latest snapshot.
- Quarterly game-day for partial tenant restore.
- Track outcomes and action items.

## C. Incident runbooks
Add runbooks for:
- registry outage
- onboarding/token failure spikes
- connector provider outage
- rollback to compat mode (temporary)

## Operational Readiness (SLOs, Metrics, Alerts)

## SLOs
- `/api/v1/runtime/bootstrap`
  - p95 <= 300ms
  - error rate <1% / 15m
- `/api/token`
  - p95 <= 500ms
  - error rate <1% / 15m
  - registry-miss <0.1% / 15m
- WebSocket startup (`connect -> session_started`)
  - p95 <= 1500ms
  - error rate <1% / 15m
- internal registry resolution
  - p95 <= 100ms

## Mandatory metric/log labels
- `tenant_id`
- `company_id`
- `industry_template_id`
- `registry_version`
- `schema_version`
- `registry_mode`
- `source` (`registry|compat_fallback`)
- `api_version`
- `trace_id`

## Alerts (minimum)
- registry miss spikes
- bootstrap/token 5xx/4xx spikes
- websocket startup failure spikes
- connector test failure spikes
- policy validation failures at deploy time

## End-User UX Decision (Final)
End users do not complete setup onboarding.

Production behavior:
- app calls bootstrap
- if one company context is resolvable -> starts directly
- if multiple and no default -> lightweight company picker only
- no industry setup wizard for end users

Admin-only setup owns:
- template selection
- company profile
- knowledge
- runtime data
- connectors/MCP

## Implementation Plan (Decision-Complete)

## Phase 1 — API and auth foundations
1. Add `/api/v1/runtime/bootstrap`.
2. Introduce `AuthContext` extraction middleware.
3. Enforce JWT claim contract (`tenant_id`, roles/scopes).
4. Add API version in responses.
5. Add idempotency-key middleware/store for mutating admin APIs.

## Phase 2 — Admin backend APIs
1. Implement company CRUD.
2. Implement knowledge import/list/delete.
3. Implement products/slots import and demo purge wrappers.
4. Implement MCP provider catalog and connector CRUD/test.
5. Enforce allowlist + capability ceilings + secretRef policy.
6. Add policy-as-code loaders + validators.

## Phase 3 — Admin frontend
1. Build `/admin` routes/pages.
2. Company profile/template forms.
3. Knowledge import UI (text/url/file).
4. Runtime data import UI (products/slots).
5. Connector manager with test actions.
6. Publish/validate workflow with blocking errors.

## Phase 4 — End-user frontend cutover
1. Remove onboarding gate from primary user flow.
2. Add bootstrap gate + loading/error states.
3. Add minimal company-picker fallback when needed.
4. Keep dev-only compat onboarding behind explicit flag.

## Phase 5 — Governance + reliability
1. Add retention/deletion jobs + endpoints/runbooks.
2. Add backup/restore validation scripts and schedules.
3. Add SLO dashboards and alert policies.
4. Add rollout gates and rollback triggers.

## Phase 6 — Docs + runbooks + policy files
1. Update architecture/build/setup docs.
2. Add schema versioning and API versioning docs.
3. Add security policy docs (authz, CSRF, connector policy).
4. Add DR runbooks and cutover checklist.

## Test Cases and Scenarios

## Backend tests
1. Authz/ownership:
- `/api/v1/runtime/bootstrap` tenant mismatch -> `403`
- `/api/token` non-owned company -> `404`
- admin APIs require `tenant_admin`
- websocket tamper -> explicit error + no session start

2. Idempotency:
- same `Idempotency-Key` + same payload -> replay same result
- same key + different payload -> `409`

3. Connector policy:
- unknown provider rejected
- missing `secretRef` rejected
- out-of-policy capability rejected
- test connector respects timeout/retry/circuit policy

4. Policy-as-code:
- invalid allowlist/capability matrix blocks startup/deploy checks

5. Retention/deletion:
- purge jobs delete correct scope only
- tenant delete/export workflows scoped correctly

## Frontend tests
1. End-user app:
- bypasses onboarding and uses bootstrap
- company picker only when required
- no ws/token calls before bootstrap resolution

2. Admin app:
- CRUD flows for company/knowledge/runtime data/connectors
- validation errors surfaced from backend policy checks

3. Regression:
- existing voice/transcript/socket behavior unaffected by onboarding removal

## Integration/E2E
1. Admin configures new company + connector, publishes.
2. End-user logs in and starts live session without onboarding.
3. Tamper attempts via query/body are rejected.
4. Connector outage simulation triggers fail-closed + alerts.
5. Rollback drill (`REGISTRY_ENABLED=false`) documented and tested in staging only.

## Rollout and Acceptance Gates

Promotion requires all:
- full backend/frontend regression green
- schema/policy validation green
- staging smoke tests green
- SLO targets met for 24h
- alerting verified with synthetic triggers
- backup/restore drill passed within target window

Rollback triggers:
- bootstrap/token/websocket failure rates breach thresholds
- unexplained registry miss spikes
- connector policy failures causing widespread impact

Rollback action:
- temporary compat fallback by flag, while preserving audit trail and incident timeline.

## Explicit Assumptions and Defaults

1. End-user onboarding is removed from production runtime flow.
2. Admin portal is required for tenant/company setup in production.
3. MCP v1 is allowlist-only; arbitrary MCP URLs are out of scope.
4. Canonical aviation template ID is `aviation-support`.
5. `schema_version=1` required on template/company docs; unsupported versions fail closed.
6. API versioning starts at `/api/v1`.
7. Idempotency keys are mandatory on mutating admin/import endpoints.
8. Secret values never stored in Firestore; only `secretRef`.
9. Compatibility mode remains rollback/debug only, not normal operation.

## Execution Tracker (as of 2026-02-27)

### Completed foundations
- [x] Registry-first cutover defaults and fail-closed behavior in backend paths.
- [x] Canonical template parity fixtures for all six templates (`electronics`, `hotel`, `automotive`, `fashion`, `telecom`, `aviation-support`).
- [x] Schema/version controls (`schema_version` enforcement + loader validation).
- [x] Provisioning/migration CLI hardening (`seed-all`, runtime data imports, dry-run/reporting, validation tests).
- [x] Connector manifest runtime enforcement and fail-closed behavior for missing/invalid connector configuration.
- [x] Local docs governance controls (`docs/local-docs-manifest.json`, `scripts/check_local_docs.py`, process doc).
- [x] Origin policy split for HTTP vs WebSocket with test coverage and debug logging.
- [x] `/api/v1/runtime/bootstrap` endpoint with fail-closed registry behavior, tenant/company/template guards, and explicit error codes (`TENANT_FORBIDDEN`, `COMPANY_NOT_FOUND`, `NEED_COMPANY_SELECTION`, `REGISTRY_CONFIG_NOT_FOUND`).
- [x] End-user frontend bootstrap gate: runtime bootstrap first, compat onboarding only behind explicit fallback/onboarding flags.
- [x] Targeted bootstrap regression coverage (backend + frontend) and production build validation.
- [x] Tenant admin auth-context enforcement for v1 admin endpoints (tenant lock + role/scope checks via trusted headers).
- [x] Minimal v1 admin read endpoints: `GET /api/v1/admin/companies`, `GET /api/v1/admin/mcp/providers`.
- [x] Idempotency foundation for mutating admin APIs with conflict protection + replay semantics.
- [x] First mutating endpoint: `POST /api/v1/admin/companies` with `Idempotency-Key` required.
- [x] Backend regression tests for admin v1 auth/read/write/idempotency paths.
- [x] Company detail/update endpoints: `GET/PUT /api/v1/admin/companies/{companyId}`.
- [x] Knowledge endpoints: `GET /knowledge`, `POST /knowledge/import-text`, `POST /knowledge/import-url`, `POST /knowledge/import-file`, `DELETE /knowledge/{knowledgeId}`.
- [x] Connector endpoints: `POST/PUT/DELETE /connectors...` and `POST /connectors/{connectorId}/test`.
- [x] Shared idempotency helper usage applied across admin mutating endpoints implemented in this phase.
- [x] Runtime data admin endpoints: `POST /products/import`, `POST /booking-slots/import`, `POST /runtime/purge-demo`.
- [x] Extended admin endpoint tests for knowledge/connectors/runtime imports (positive + negative paths).
- [x] Policy-as-code files added and wired:
  - `policies/mcp_providers.v1.json`
  - `policies/capability_matrix.v1.yaml`
- [x] Connector create/update now enforce provider + template capability policy rules from policy files.
- [x] Idempotency dependency extraction added for mutating admin routes:
  - reusable `Idempotency-Key` dependency
  - shared preflight helper (`_idempotency_preflight`) + keyed begin helper
  - mutating admin routes migrated to dependency + shared preflight path
- [x] Admin frontend bootstrap added:
  - `/admin` entrypoint via `frontend/src/main.tsx`
  - `AdminDashboard` for tenant auth headers, company snapshot, provider snapshot, and company create action.
- [x] Admin frontend flows completed for:
  - knowledge import (`text`, `url`, `file`) + listing + delete actions
  - connectors create/update/test/delete workflows
  - runtime imports (`products`, `booking_slots`) + data-tier selector + purge-demo action
  - targeted admin frontend regression tests + production build validation
- [x] Non-mock connector test policy enforcement added:
  - provider `testPolicy` parsing from policy-as-code
  - egress host allowlist checks (`allowedHosts`) for connector endpoint URLs
  - timeout/retry budget enforcement for probe execution
  - circuit breaker behavior for repeated non-mock probe failures
- [x] Runtime connector policy fail-closed enforcement added in tool dispatch:
  - non-mock connectors require `runtime_policy`
  - runtime policy validation (`timeoutSeconds`, `maxRetries`)
  - runtime egress host allowlist checks before provider dispatch
  - explicit runtime error codes for policy/egress violations
- [x] API versioned contract coverage added for admin surface:
  - dedicated suite `tests/test_admin_v1_contracts.py`
  - validates `apiVersion` + required response fields across all current `/api/v1/admin/...` endpoints
- [x] Data governance admin workflows implemented:
  - `POST /api/v1/admin/companies/{companyId}/export`
  - `DELETE /api/v1/admin/companies/{companyId}`
  - `POST /api/v1/admin/companies/{companyId}/retention/purge`
  - targeted behavior/contract tests for the new endpoints
- [x] Operator runbook added for governance workflows:
  - `docs/runbooks/admin-data-governance-operations.md`
- [x] DR restore-drill automation implemented:
  - `scripts/dr_restore_drill.py` (export -> delete -> restore -> verify)
  - test coverage in `tests/test_dr_restore_drill.py`
- [x] Release gate automation implemented:
  - `scripts/release_gate.py` (policy + artifact checks)
  - test coverage in `tests/test_release_gate.py`

### Recently completed (this plan scope)
- [x] SLO/alert policy templates added:
  - `policies/observability_slos.v1.yaml`
  - `policies/alert_policies.v1.json`
- [x] Release gate validation extended to enforce observability policy shape.
- [x] Deployment automation now runs release gates pre-deploy:
  - `scripts/deploy_cloud_run.sh` invokes `python -m scripts.release_gate --strict` by default.
- [x] End-to-end staging governance drill automation added:
  - `scripts/staging_governance_drill.py` (export -> purge -> delete -> restore -> verify)
  - test coverage in `tests/test_staging_governance_drill.py`
- [x] CI target added for strict release gates on `main` and `dev`:
  - `.github/workflows/ci.yml` includes `python -m scripts.release_gate --strict`.

### Immediate next milestones
1. Run staged smoke and publish operator execution receipts.
2. Execute one production-like governance drill and archive result artifact.
3. Run full backend + frontend regression once before release tag.
