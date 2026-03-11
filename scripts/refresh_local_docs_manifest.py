"""Refresh docs/local-docs-manifest.json for the current set of local docs."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path


DEFAULT_FILES: list[dict[str, str]] = [
    {
        "path": "Ekaette_Architecture.md",
        "purpose": "Canonical architecture narrative (local ignored doc)",
    },
    {
        "path": "Ekaette_Architecture.html",
        "purpose": "Rendered architecture reference (local ignored doc)",
    },
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh local docs manifest hashes.")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument(
        "--phase-marker",
        default="phase8-reference-architecture-refresh",
        help="Phase marker to stamp into the manifest",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    manifest_path = repo_root / "docs" / "local-docs-manifest.json"
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("Error: Unable to determine HEAD commit. Is this a git repository?")
    today = date.today().isoformat()

    files: list[dict[str, str]] = []
    for item in DEFAULT_FILES:
        path = repo_root / item["path"]
        if not path.exists():
            continue
        files.append(
            {
                "last_reviewed_commit": head,
                "last_reviewed_date": today,
                "path": item["path"],
                "phase_marker": args.phase_marker,
                "purpose": item["purpose"],
                "sha256": _sha256(path),
            }
        )

    manifest = {
        "files": files,
        "generated_by": "scripts/refresh_local_docs_manifest.py",
        "generated_date": today,
        "last_reviewed_commit": head,
        "phase_marker": args.phase_marker,
        "version": 1,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
