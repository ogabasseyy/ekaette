# Multi-Tenant Registry Migration (TDD-First) — 2026-02-25

## Purpose

Migrate Ekaette from hardcoded industry behavior to a scalable, multi-tenant, capability-driven registry architecture while preserving backward compatibility during rollout.

This plan is telecom- and aviation-ready (aviation v1 support/status only).

## Core Decisions

- Industry model: platform-managed `industry_templates`
- Tenant model: tenant-owned `companies` referencing templates
- Runtime policy: backend resolves canonical config; client is not authoritative
- Rollout strategy: incremental compatibility adapters (no big-bang rewrite)
- Delivery model: strict TDD (`Red -> Green -> Refactor`) for code streams

## Canonical Runtime Model

- `tenant_id`
- `company_id`
- `industry_template_id`
- `capabilities[]`
- `registry_version`

Session state should carry both canonical keys and temporary legacy aliases during migration:
- Keep legacy: `app:industry`, `app:industry_config`, `app:company_id`
- Add canonical: `app:tenant_id`, `app:industry_template_id`, `app:capabilities`, `app:ui_theme`, `app:connector_manifest`, `app:registry_version`

## Scalability and Safety Principles (2026)

- All tool reads/writes must scope by `tenant_id + company_id` from session state
- Backend resolves company/template/capabilities and rejects client mismatches
- Connector secrets live in Secret Manager, not Firestore
- Frontend onboarding becomes backend-driven (`GET /api/onboarding/config`)
- Firestore collections move to tenant/company-scoped paths for runtime data
- Capability-driven routing/tool access replaces industry-string branching

## Firestore Target Shape (High Level)

- `industry_templates/{templateId}`
- `tenants/{tenantId}/companies/{companyId}`
- `tenants/{tenantId}/companies/{companyId}/knowledge/{id}`
- `tenants/{tenantId}/companies/{companyId}/catalog_items/{id}`
- `tenants/{tenantId}/companies/{companyId}/booking_slots/{id}`
- `tenants/{tenantId}/companies/{companyId}/bookings/{id}`

## API Contract Evolution (Backward-Compatible)

### `GET /api/onboarding/config` (new)

Returns dynamic templates, companies, themes, defaults, and UI lock policies.

### `POST /api/token` (extend)

Keep current fields; add canonical metadata:
- `industryTemplateId`
- `capabilities`
- `registryVersion`
- `voice`
- continue returning `manualVadActive` and `vadMode`

### WebSocket `session_started` (extend)

Add:
- `tenantId`
- `industryTemplateId`
- `capabilities`
- `registryVersion`

Keep legacy `industry` during migration.

## TDD Execution Phases

### Phase 0 — Baseline Characterization

- Capture current behavior in tests (done + top-up)
- No production code changes

### Phase 1 — Registry Core + Compatibility Adapters

- Add `registry_loader`
- Integrate registry-first adapters into existing loaders
- Preserve legacy session keys while adding canonical keys
- Tests:
  - template/company resolution
  - malformed data handling
  - registry version changes on config changes
  - adapter integration and fallback behavior

### Phase 2 — Token + WebSocket Canonical Resolution

- Backend resolves canonical company/template/capabilities for `/api/token` and websocket startup
- Tests:
  - token response canonical fields
  - `session_started` canonical fields
  - mismatch rejection / canonicalization
  - session resumption lock preservation

### Phase 3 — Tool Scoping + Capability Guards

- Scope booking/catalog tools by tenant/company
- Enforce capability allow/deny matrix
- Tests intentionally break current no-scoping baselines

### Phase 4 — Frontend Dynamic Onboarding + Type Migration

- Replace hardcoded industry maps/themes with backend onboarding config
- Migrate frontend from hardcoded `Industry` union to template/company metadata types
- Preserve mid-call onboarding lock behavior

### Phase 5 — Provisioning Tooling (Operationalizing “No Manual”)

- Seed templates
- Provision tenant/company
- Import company knowledge
- Validate registry schemas and capabilities
- Smoke-test tenant/template setups

### Phase 6 — Telecom + Aviation Templates (Config-First)

- Telecom v1: support, plan catalog, device comparison, optional booking/outage lookup
- Aviation v1: support + status only (no booking/rebooking/seat/payment mutations)

### Phase 7 — Cutover + Deprecation

- Make registry path default
- Keep legacy aliases for a defined deprecation window
- Remove hardcoded fallbacks from runtime path (keep only test/dev fixtures)

## Parallel Workstreams

- Stream A: Backend registry core + API contracts
- Stream B: Tool scoping + capability guards
- Stream C: Frontend dynamic onboarding + type migration
- Stream D: Docs (Architecture / Build Plan / Setup Guide / README), updated after code contracts stabilize

## Documentation Upgrade Requirements

Update in lockstep with code phases:
- `Ekaette_Architecture.md`
- `Ekaette_Build_Plan_v3.md`
- `Ekaette_Setup_Guide.md`
- `README.md` (recommended)

Docs must reflect actual merged interfaces, not speculative designs.

## Test Discipline (Mandatory)

For each code change:
1. Write failing tests first
2. Implement minimum code to pass
3. Refactor
4. Run targeted tests + regression checks
5. Update docs after contract stabilizes

## Notes

- This file is the persistent reference copy of the migration plan summary.
- Detailed per-phase test cases and acceptance criteria can be expanded as implementation proceeds.
