# Firestore Index Readiness Checklist (Registry Cutover)

## Purpose

Ensure tenant-scoped query paths have required indexes and are ready before registry-first production rollout.

## Tracked Index Config

- `firestore.indexes.json` (tracked in repo root)

## Pre-Cutover Checklist

- [ ] Required composite indexes are defined in `firestore.indexes.json`
- [ ] Indexes are created in the target project/environment
- [ ] Index builds are complete (no pending build jobs)
- [ ] Staging smoke tests run without missing-index errors
- [ ] Query logs show indexed execution for critical paths
- [ ] Hotspot risk reviewed (ID generation, write patterns)
- [ ] Index exemptions configured for large non-query fields (where applicable)

## Critical Query Families

### Booking slots

Expected filters (current and near-term):
- `tenant_id`, `company_id`, `date`, optional `location`, optional `available`
- Nested subcollection path also scopes by tenant/company structurally

### Bookings

Expected filters (current and near-term):
- `tenant_id`, `company_id`, `status`, `created_at`

### Catalog items

Expected filters (current and near-term):
- `tenant_id`, `company_id`, `category`, `in_stock`

### Company knowledge (if filtered search is added)

Expected filters (future-proofing):
- `tenant_id`, `company_id`, `tags`, `updated_at`

## Scale Hygiene (Required)

- [ ] No sequential IDs for high-write collections
- [ ] Tenant/company scoping enforced in runtime queries
- [ ] Large text/blob fields excluded from indexes when not queried
- [ ] New collection traffic ramped gradually after migration
