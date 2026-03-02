# Operational Readiness: SLOs, Metrics Labels, Alerts, and Rollout Gates

## Scope

This document defines the minimum operational gates required for registry-first production rollout.

Tracked policy templates:
- `policies/observability_slos.v1.yaml`
- `policies/alert_policies.v1.json`

Enforcement points:
- `python -m scripts.release_gate --strict`
- `scripts/deploy_cloud_run.sh` (pre-deploy gate, enabled by default)
- `.github/workflows/ci.yml` (strict release-gate step on `main`/`dev`)

## SLO Targets (Initial)

These are starting targets for staging and production. Tune after observing real traffic, but do not ship without targets.

### `/api/onboarding/config`

- p95 latency: `<= 300ms` (warm path, registry data available)
- error rate: `< 1%` over 15 minutes

### `/api/token`

- p95 latency: `<= 500ms`
- error rate: `< 1%` over 15 minutes
- registry miss rate: `< 0.1%` over 15 minutes

### WebSocket startup (`connect -> session_started`)

- p95 latency: `<= 1500ms` (excludes downstream first model response)
- error rate: `< 1%` over 15 minutes

### Registry resolution (internal)

- p95 latency: `<= 100ms`
- cache miss path is allowed but must be monitored separately

## Mandatory Structured Context Fields (Logs + Metrics Labels)

All request/session logs and metrics labels for registry-sensitive paths should include:
- `tenant_id`
- `company_id`
- `industry_template_id`
- `registry_version`
- `schema_version` (template/company where available)
- `registry_mode` (`enabled` / `disabled`)
- `source` (`registry` / `compat_fallback`)

## Alert Conditions (Minimum Set)

Create alerts for:
- `registry_miss_total` spike (5m window)
- `/api/onboarding/config` 5xx rate spike
- `/api/token` 4xx mismatch spike (`TEMPLATE_COMPANY_MISMATCH`, unknown company)
- WebSocket `session_started` failure spike
- Phase 7 fail-closed errors increasing after deployment (usually indicates provisioning gaps)

## Rollout Gates (Promotion Criteria)

Promotion to production requires all of the following:
- full backend + frontend regression green
- registry-mode smoke tests green
- SLOs met in staging for 24h
- no unexplained registry miss spikes
- local docs drift checker passes (see `docs/local-docs-update-process.md`)
- Firestore index readiness checklist complete (see `docs/firestore-index-readiness-checklist.md`)

## Rollback Triggers (Examples)

Trigger rollback investigation/action when any of the following persist:
- onboarding 5xx > 2% for 10m
- token issuance failures > 2% for 10m due to registry misses
- websocket startup failures > 2% for 10m after deployment
- repeated schema-version unsupported errors after rollout

## Immediate Rollback Action

- Set `REGISTRY_ENABLED=FALSE` (compat mode) temporarily.
- Keep `REGISTRY_REQUIRE_COMPANY_TEMPLATE_MATCH` documented and intentional.
- Preserve logs/metrics for root-cause analysis before reattempting cutover.

## Staging Validation Checklist

- Capture latency percentiles for `/api/onboarding/config`, `/api/token`, websocket startup
- Validate fail-closed behavior for missing registry data (explicit 503 / error codes)
- Validate authz negative cases (403/404/409/503 semantics)
- Confirm no silent compat fallback in registry mode for onboarding/knowledge
