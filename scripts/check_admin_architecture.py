"""Static/runtime gates for admin modularization architecture."""

from __future__ import annotations

import re
from pathlib import Path
import sys


ADMIN_ROOT = Path("app/api/v1/admin")


def _check_no_main_imports(errors: list[str]) -> None:
    pattern = re.compile(r"^\s*(from\s+main\s+import|import\s+main\b)")
    for file_path in sorted(ADMIN_ROOT.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                errors.append(f"{file_path}:{lineno}: forbidden main import")


def _check_no_deleted_services_imports(errors: list[str]) -> None:
    pattern = "app.api.v1.admin.services"
    for file_path in sorted(Path("app").rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        if pattern in content:
            errors.append(f"{file_path}: references deleted package '{pattern}'")


def _check_admin_route_parity(errors: list[str]) -> None:
    try:
        from main import app
    except Exception as exc:
        errors.append(f"cannot import main app: {exc}")
        return

    found: list[tuple[str, str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not isinstance(path, str) or not path.startswith("/api/v1/admin"):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            found.append((method, path, getattr(route.endpoint, "__module__", "")))

    if len(found) != 20:
        errors.append(f"expected 20 admin routes, found {len(found)}")

    bad_modules = [item for item in found if not item[2].startswith("app.api.v1.admin.routes")]
    if bad_modules:
        errors.append(f"admin routes served from unexpected modules: {bad_modules}")


def main() -> int:
    errors: list[str] = []
    if not ADMIN_ROOT.exists():
        errors.append(f"missing {ADMIN_ROOT}")

    _check_no_main_imports(errors)
    _check_no_deleted_services_imports(errors)
    _check_admin_route_parity(errors)

    if errors:
        print("Admin architecture check failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("Admin architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
