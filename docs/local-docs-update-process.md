# Local Ignored Docs Update Process

## Why This Exists
The `Ekaette_*.md` / `Ekaette_*.html` docs are intentionally git-ignored, so we use a tracked manifest + checker to prevent silent drift.

Tracked controls:
- `docs/local-docs-manifest.json`
- `scripts/check_local_docs.py`

## Local Docs Covered
- `Ekaette_Architecture.md`
- `Ekaette_Architecture.html`
- `Ekaette_Build_Plan_v3.md`
- `Ekaette_Setup_Guide.md`

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
1. Edit the local ignored docs.
2. Review changes for accuracy.
3. Recompute hashes and update `docs/local-docs-manifest.json`.
4. Commit the manifest update (the local docs remain ignored).
5. Re-run the checker to confirm a clean state.

Example helper (manual hash refresh workflow):
```bash
./.venv/bin/python - <<'PY'
import hashlib, json, subprocess
from pathlib import Path
manifest = Path('docs/local-docs-manifest.json')
data = json.loads(manifest.read_text())
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
for item in data['files']:
    p = Path(item['path'])
    item['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
    item['last_reviewed_commit'] = head
    item['last_reviewed_date'] = '2026-02-26'
manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
PY
```

## Release Checklist Integration
Before release/cutover:
- [ ] run `scripts/check_local_docs.py`
- [ ] confirm manifest phase marker matches current rollout phase
- [ ] confirm docs hashes match manifest
- [ ] confirm updated diagrams are reflected in the phase marker / review notes
