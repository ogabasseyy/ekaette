# Ekaette — Claude Code Project Instructions

## Architecture

Registry-driven multi-tenant model: **tenant -> company -> industry template -> capabilities**.

- Root orchestrator agent (Live API voice model) delegates to 5 sub-agents (vision, valuation, booking, catalog, support).
- **Dual-runner architecture**: Voice channels use a Live API-compatible model (`LIVE_MODEL_ID`) via `bidiGenerateContent`. Text channels (WhatsApp, SMS) use `gemini-3-flash-preview` (`TEXT_MODEL_ID`) via `Runner.run_async()` / `generateContent`. The text model does NOT support `bidiGenerateContent` — this is intentional; text channels never use the Live API.
- ALL agents in the **voice pipeline** must use a Live API-compatible model. The text pipeline uses `create_ekaette_router(model=text_model, channel="text")` with its own instruction set.
- Config source: `REGISTRY_ENABLED=true` (default) makes Firestore registry authoritative. Setting `false` enables legacy local-config fallback (deprecated).

## TDD Rule

Red -> Green -> Refactor for ALL code changes. Write failing tests first, then implement.

- Backend: `pytest tests/ -v` (pytest + pytest-asyncio + fakefirestore)
- Frontend: `cd frontend && npx vitest run` (Vitest + @testing-library/react)
- Gate: both must pass before every commit.

## Session State Conventions

Prefix conventions for ADK session state keys:
- `app:*` — application/industry config (read-only during session)
- `user:*` — user-specific data (persists across sessions)
- `temp:*` — transient data (cleared on session end)

Canonical keys (registry-backed):
- `app:tenant_id`, `app:company_id`, `app:industry_template_id`
- `app:capabilities`, `app:ui_theme`, `app:connector_manifest`, `app:registry_version`

Legacy aliases (deprecation window):
- `app:industry` (category), `app:industry_config`, `app:voice`, `app:greeting`

## Tool Scoping

All Firestore queries in tools MUST use `scoped_collection()` helper from `app/tools/scoped_query.py` — never query global collections directly.

New tools MUST be added to `TOOL_CAPABILITY_MAP` in `app/agents/ekaette_router/callbacks.py`.

## Frontend Rules

- No hardcoded industry/company lists. All onboarding data from `GET /api/onboarding/config`.
- Tailwind CSS v4 (CSS-first): OKLCH semantic tokens in `@theme`, CVA for variants, `cn()` utility. NO `tailwind.config.js`.
- React 19: ref as regular prop (no forwardRef), `useRef` for transient values (audio/WS).
- Package manager: **pnpm** (not npm or yarn).

## Naming Conventions

- Template IDs: kebab-case (`aviation-support`, `electronics`)
- Company IDs: kebab-case (`ekaette-electronics`, `acme-hotel`)
- Tenant IDs: lowercase alphanumeric (`public`)

## Provisioning

Use `python -m scripts.registry` CLI for template/company management — not manual Firestore edits.

Schema validation: `app/configs/registry_schema.py` (`validate_template`, `validate_company`, `validate_knowledge_entry`, `validate_product`, `validate_booking_slot`).

## Demo Data

Three-layer isolation prevents demo data from leaking into production:

1. **CLI flag** (`--include-runtime-data`): `seed-all` only seeds config (templates + companies) by default. Pass `--include-runtime-data` to also seed products, booking_slots, and knowledge.
2. **`data_tier: "demo"` markers**: All runtime demo fixtures are tagged. Run `purge-demo-data` to remove only tagged documents.
3. **Firestore emulator**: Set `FIRESTORE_EMULATOR_HOST=127.0.0.1:8080` for zero-leak local dev.

Commands:
```bash
# Seed config only (safe for production)
python -m scripts.registry seed-all

# Seed config + runtime demo data (dev/demo only)
python -m scripts.registry seed-all --include-runtime-data

# Purge all demo-tagged runtime data
python -m scripts.registry purge-demo-data --tenant=public

# Import individual collections
python -m scripts.registry import-products --tenant=public --company=ekaette-electronics --file=tests/fixtures/registry/products/ekaette-electronics.json
python -m scripts.registry import-booking-slots --tenant=public --company=ekaette-hotel --file=tests/fixtures/registry/booking_slots/ekaette-hotel.json
```

Fixture files: `tests/fixtures/registry/{products,booking_slots,knowledge}/*.json` (13 files, 94 items total).

## Shared Config Utilities

`app/configs/__init__.py` exports shared helpers used across all config modules:
- `RegistryDataMissingError` — raised when `REGISTRY_ENABLED=true` but data is absent
- `sanitize_log(value)` — strip control chars from user input before logging
- `env_flag(name, default)` — read boolean env vars
- `registry_enabled()` — check if registry is authoritative

Import from `app.configs` (canonical) or from individual modules (re-exported for backward compat).

## Git

- Commit format: `S{N}: {brief description}` (e.g., `S2: scaffold multi-agent backend`)
- NO AI attribution in commits. No `Co-Authored-By: Claude` lines.
- Branch strategy: `main` (stable) <- `dev` (active)

## Environment

- Python 3.13 with venv at `.venv/`
- Node 20+ (Vite 7 requirement), pnpm for frontend
- Key env vars: see `.env.example`
- `REGISTRY_ENABLED=TRUE` is the production default (Phase 7 cutover)
