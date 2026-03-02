# Registry Schema Versioning Policy

## Purpose
This policy defines how registry template and tenant-company documents are versioned, validated, and loaded during the registry-first cutover.

It applies to:
- `industry_templates/{templateId}`
- `tenants/{tenantId}/companies/{companyId}`

## Current Version Window
- `schema_version` is required on every template and company registry document.
- Current supported range:
  - `MIN_SUPPORTED_SCHEMA_VERSION = 1`
  - `MAX_SUPPORTED_SCHEMA_VERSION = 1`
- Current write version (provisioning + migration scripts):
  - `REGISTRY_SCHEMA_VERSION = 1`

## Loader Behavior (Fail-Closed)
Registry loaders must reject unsupported or malformed schema versions with explicit errors.

Required behavior:
- missing `schema_version` -> fail closed
- non-integer `schema_version` -> fail closed
- unsupported version (`< min` or `> max`) -> fail closed
- no silent coercion or defaulting at runtime

Runtime error code:
- `REGISTRY_SCHEMA_VERSION_UNSUPPORTED`

## Provisioning / Migration Write Policy
Provisioning and migration tools must:
- always write `schema_version`
- use the current `REGISTRY_SCHEMA_VERSION`
- be deterministic for the same input
- remain idempotent on reruns

## Compatibility Policy
- Compatibility fallbacks are temporary migration aids and not a substitute for schema versioning.
- New template additions must be registry-first and include `schema_version` from day one.
- Unsupported future schema versions are not interpreted until loaders are explicitly upgraded.

## Upgrade Process (Future Schema Versions)
When introducing a new schema version:
1. Add/track schema changes in code + docs.
2. Update validators to understand the new version.
3. Expand loader support (`MAX_SUPPORTED_SCHEMA_VERSION`).
4. Add migration tooling (if required) to transform old documents.
5. Add tests for:
   - old version support (if kept)
   - new version acceptance
   - unsupported version rejection
6. Roll out to staging and validate registry resolution behavior before production.

## Required Tests
- missing template/company `schema_version` fails validation
- unsupported template/company `schema_version` fails validation
- loaders fail closed on unsupported schema versions
- provisioning scripts write `schema_version`
- migration scripts write `schema_version`
