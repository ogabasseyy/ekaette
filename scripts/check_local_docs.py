"""Verify hashes and presence of locally ignored Ekaette docs against a tracked manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("manifest root must be an object")
    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest must contain a 'files' array")
    return data


def _check_manifest(manifest_path: Path, repo_root: Path) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    entries = manifest.get("files", [])
    results: list[dict[str, Any]] = []
    failures = 0

    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            failures += 1
            results.append({"path": "<invalid>", "status": "invalid_entry"})
            continue

        rel_path = raw_entry.get("path")
        expected_hash = raw_entry.get("sha256")
        if not isinstance(rel_path, str) or not rel_path:
            failures += 1
            results.append({"path": "<missing>", "status": "missing_path"})
            continue

        abs_path = (repo_root / rel_path).resolve()
        if not abs_path.exists():
            failures += 1
            results.append({"path": rel_path, "status": "missing"})
            continue

        actual_hash = _sha256(abs_path)
        if not isinstance(expected_hash, str) or not expected_hash:
            failures += 1
            results.append({
                "path": rel_path,
                "status": "missing_hash",
                "actual_sha256": actual_hash,
            })
            continue

        if actual_hash != expected_hash:
            failures += 1
            results.append({
                "path": rel_path,
                "status": "hash_mismatch",
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
            })
            continue

        results.append({
            "path": rel_path,
            "status": "ok",
            "sha256": actual_hash,
        })

    return {
        "manifest": str(manifest_path),
        "phase_marker": manifest.get("phase_marker", ""),
        "checked": len(results),
        "failures": failures,
        "results": results,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="check_local_docs",
        description="Verify local ignored Ekaette docs against docs/local-docs-manifest.json",
    )
    parser.add_argument(
        "--manifest",
        default="docs/local-docs-manifest.json",
        help="Path to local docs manifest JSON",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root used to resolve manifest paths",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON only",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    repo_root = Path(args.repo_root).resolve()

    try:
        result = _check_manifest(manifest_path, repo_root)
    except Exception as exc:
        error = {"error": str(exc), "manifest": str(manifest_path)}
        print(json.dumps(error, indent=2, sort_keys=True))
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Local docs check: checked={result['checked']} failures={result['failures']}")
        print(f"Manifest: {result['manifest']}")
        if result.get("phase_marker"):
            print(f"Phase marker: {result['phase_marker']}")
        for item in result["results"]:
            status = item.get("status", "unknown")
            path = item.get("path", "<unknown>")
            print(f"- {status}: {path}")
            if status == "hash_mismatch":
                print(f"  expected={item.get('expected_sha256')}")
                print(f"  actual  ={item.get('actual_sha256')}")
            elif status == "missing_hash":
                print(f"  actual  ={item.get('actual_sha256')}")

    if result["failures"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
