"""Architecture invariants for admin modularization."""

from __future__ import annotations

from pathlib import Path
import re


def test_no_direct_main_imports_under_admin_package():
    pattern = re.compile(r"^\s*(from\s+main\s+import|import\s+main\b)")
    admin_root = Path("app/api/v1/admin")
    assert admin_root.exists()

    violations: list[str] = []
    for file_path in sorted(admin_root.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{file_path}:{lineno}")
    assert not violations, f"forbidden main imports found: {violations}"


def test_admin_routes_still_mounted_from_router_modules():
    from main import app

    routes = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not isinstance(path, str) or not path.startswith("/api/v1/admin"):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.append((method, path, getattr(route.endpoint, "__module__", "")))

    assert len(routes) == 25
    assert all(module.startswith("app.api.v1.admin.routes") for _, _, module in routes)
