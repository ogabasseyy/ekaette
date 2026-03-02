"""Static/runtime gates for public HTTP modularization architecture."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PUBLIC_ROOT = Path("app/api/v1/public")
MAIN_PATH = Path("main.py")


def _check_no_main_imports(errors: list[str]) -> None:
    pattern = re.compile(r"^\s*(from\s+main\s+import|import\s+main\b)")
    for file_path in sorted(PUBLIC_ROOT.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                errors.append(f"{file_path}:{lineno}: forbidden main import")


def _check_main_route_delegates(errors: list[str]) -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(MAIN_PATH))
    expected = {
        "create_ephemeral_token": "public_http.create_ephemeral_token",
        "get_onboarding_config": "public_http.get_onboarding_config",
        "get_runtime_bootstrap": "public_http.get_runtime_bootstrap",
        "validate_upload": "public_http.validate_upload",
    }

    found: dict[str, bool] = {name: False for name in expected}
    for node in module.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name not in expected:
            continue
        body_src = ast.get_source_segment(source, node) or ""
        if expected[node.name] in body_src and "_sync_public_runtime()" in body_src:
            found[node.name] = True

    missing = [name for name, ok in found.items() if not ok]
    if missing:
        errors.append(f"public delegate wrappers missing/changed: {missing}")


def main() -> int:
    errors: list[str] = []
    if not PUBLIC_ROOT.exists():
        errors.append(f"missing {PUBLIC_ROOT}")
    if not MAIN_PATH.exists():
        errors.append(f"missing {MAIN_PATH}")

    if not errors:
        _check_no_main_imports(errors)
        _check_main_route_delegates(errors)

    if errors:
        print("Public architecture check failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("Public architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
