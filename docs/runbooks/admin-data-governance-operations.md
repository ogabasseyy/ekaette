# Admin Data Governance Operations (v1)

## Scope

This runbook covers tenant-scoped company governance operations implemented via `/api/v1/admin/...` endpoints:
- company export
- company deletion
- retention purge

All operations require tenant-admin auth headers and tenant-scoped authorization.

## Prerequisites

- Backend is running with registry access.
- Operator has tenant admin role.
- `tenantId` is known and validated.

Required headers:
- `x-user-id`
- `x-tenant-id`
- `x-roles: tenant_admin`

For mutating operations:
- `Idempotency-Key`

## 1) Export Company Data

Endpoint:
- `POST /api/v1/admin/companies/{companyId}/export?tenantId={tenantId}`

Request body:
```json
{
  "includeRuntimeData": true
}
```

Response includes:
- `apiVersion`
- `tenantId`
- `companyId`
- `company`
- `collections` (`knowledge`, `products`, `booking_slots`)
- `counts`
- `exportedAt`

Operational guidance:
- Use export output as a pre-change snapshot before destructive actions.
- Store export artifacts in secured storage; do not log full payload in plaintext logs.

## 2) Delete Company Data

Endpoint:
- `DELETE /api/v1/admin/companies/{companyId}?tenantId={tenantId}`

Headers:
- include `Idempotency-Key`

Behavior:
- Deletes known subcollections (`knowledge`, `products`, `booking_slots`)
- Deletes company document
- Returns per-collection delete counts and company delete flag

Operational guidance:
- Always run export first.
- Re-run with same idempotency key only for replay-safe confirmation.

## 3) Retention Purge

Endpoint:
- `POST /api/v1/admin/companies/{companyId}/retention/purge?tenantId={tenantId}`

Headers:
- include `Idempotency-Key`

Request body:
```json
{
  "olderThanDays": 90,
  "collections": ["knowledge"],
  "dataTier": "demo"
}
```

Behavior:
- Scans selected collections (`knowledge`, `products`, `booking_slots`)
- Purges records older than cutoff based on `updated_at` or `created_at`
- Optional `dataTier` filter (`data_tier` exact match)
- Returns report with `scanned`, `deleted`, `skipped`, `missing_timestamp`

Operational guidance:
- Start with narrow scope: one collection, one tier.
- Run in staging before production rollouts.
- Monitor purge report for unusually high `missing_timestamp`.

## Error Codes (common)

- `TENANT_FORBIDDEN`
- `COMPANY_NOT_FOUND`
- `IDEMPOTENCY_KEY_REQUIRED`
- `IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`
- `REGISTRY_STORAGE_UNAVAILABLE`
- `RETENTION_COLLECTIONS_REQUIRED`
- `RETENTION_COLLECTION_INVALID`

## Post-Operation Validation

- Query admin company endpoints to confirm expected state.
- For deletes, verify company endpoint returns `404`.
- For retention purge, validate report totals against expected volume.

## Staging Drill Automation

Use the staging drill script to exercise the full governance recovery chain:
- export -> retention purge -> delete -> restore -> verify

Command example:
```bash
python -m scripts.staging_governance_drill \
  --base-url http://localhost:8000 \
  --tenant public \
  --company ekaette-hotel \
  --user-id staging-admin \
  --roles tenant_admin \
  --scopes admin:write,admin:read \
  --older-than-days 0 \
  --collections knowledge,products,booking_slots \
  --data-tier demo \
  --output ./.artifacts/staging-drill-ekaette-hotel.json
```

Notes:
- The script calls admin APIs for export/purge/delete and restores from the export snapshot directly to Firestore.
- A unique `Idempotency-Key` is included automatically for mutating API calls.
- Use `--dry-run` to validate auth and export wiring without destructive operations.
