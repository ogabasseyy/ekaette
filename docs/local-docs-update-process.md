# Local Ignored Docs Update Process

## Why This Exists

The `Ekaette_*.md` / `Ekaette_*.html` docs are intentionally git-ignored, so we use a tracked manifest + checker to prevent silent drift.

Tracked controls:
- `docs/local-docs-manifest.json`
- `scripts/check_local_docs.py`

## Local Docs Covered

- `Ekaette_Architecture.md`
- `Ekaette_Architecture.html`

## When To Run The Checker

Run before:
- release/cutover
- architecture or setup changes
- demo recording if docs are part of handoff/review material

Command:
```bash
./.venv/bin/python scripts/check_local_docs.py
```

## How To Refresh The Manifest After Intentional Doc Edits

1. Edit the local ignored markdown docs.
2. Re-render local HTML artifacts.
3. Recompute hashes and update `docs/local-docs-manifest.json`.
4. Commit the tracked manifest update (the local docs remain ignored).
5. Re-run the checker to confirm a clean state.

Commands:
```bash
./.venv/bin/python scripts/render_local_docs.py
./.venv/bin/python scripts/refresh_local_docs_manifest.py
./.venv/bin/python scripts/check_local_docs.py
```

## Release Checklist Integration

Before release/cutover:
- [ ] run `scripts/check_local_docs.py`
- [ ] confirm manifest phase marker matches current rollout phase
- [ ] confirm docs hashes match manifest
- [ ] confirm updated diagrams are reflected in the phase marker / review notes
