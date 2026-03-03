# Registry Cutover Rollback Runbook

## When to Use This Runbook

Use this runbook when registry-first cutover causes elevated errors or startup failures and you need to stabilize production quickly.

## Trigger Conditions (Examples)

- `/api/onboarding/config` 5xx > 2% for 10m
- `/api/token` failures > 2% for 10m due to registry misses
- websocket startup failures > 2% for 10m
- repeated `REGISTRY_SCHEMA_VERSION_UNSUPPORTED` or registry data missing errors

## Immediate Stabilization Steps

1. Switch backend to compatibility mode:
   - `REGISTRY_ENABLED=FALSE`
2. Redeploy backend / restart service.
3. Confirm health and core endpoints recover.
4. Preserve logs/metrics and failing request examples for root-cause analysis.

## Post-Rollback Validation

- [ ] `/health` OK
- [ ] `/api/onboarding/config` works (compat mode)
- [ ] `/api/token` works
- [ ] websocket startup returns `session_started`
- [ ] no rapid disconnect spike

## Root-Cause Investigation Checklist

- [ ] Registry onboarding docs present for target tenant
- [ ] Company profile exists in registry under tenant path
- [ ] Company knowledge exists (or intentionally empty collection)
- [ ] Template/company `schema_version` supported
- [ ] `industryTemplateId` matches company configuration in strict mode
- [ ] Firestore indexes present for affected queries
- [ ] Connector metadata valid (`secret_ref` present for non-mock providers)

## Re-Cutover Steps

1. Fix provisioning/schema/index issue.
2. Run validation:
   - registry validation CLI
   - phase cutover tests
   - staging smoke tests
3. Re-enable registry mode:
   - `REGISTRY_ENABLED=TRUE`
4. Monitor SLOs and alert channels for 24h.

## Backup / Restore Notes

- Use your platform-standard Firestore backup/snapshot process before large migrations.
- Restore procedures should be rehearsed in staging before production cutover.
- After restore, rerun registry validation and smoke tests.
