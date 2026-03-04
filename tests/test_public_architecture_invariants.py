"""Architecture invariants for public HTTP modularization."""

from __future__ import annotations

from pathlib import Path
import re


PUBLIC_ROUTES = {
    ("POST", "/api/token"),
    ("GET", "/api/onboarding/config"),
    ("GET", "/api/v1/runtime/bootstrap"),
    ("POST", "/api/upload/validate"),
}


def test_no_direct_main_imports_under_public_package():
    pattern = re.compile(r"^\s*(from\s+main\s+import|import\s+main\b)")
    public_root = Path("app/api/v1/public")
    assert public_root.exists()

    violations: list[str] = []
    for file_path in sorted(public_root.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{file_path}:{lineno}")
    assert not violations, f"forbidden main imports found: {violations}"


def test_public_routes_still_mounted_with_expected_paths_and_methods():
    from main import app

    found: set[tuple[str, str]] = set()
    unexpected_public_routes: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not isinstance(path, str):
            continue
        module = getattr(route.endpoint, "__module__", "")
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            if (method, path) in PUBLIC_ROUTES:
                found.add((method, path))
                continue
            if isinstance(module, str) and module.startswith("app.api.v1.public"):
                unexpected_public_routes.add((method, path))

    assert not unexpected_public_routes, (
        "unexpected public routes mounted: "
        f"{sorted(unexpected_public_routes)}"
    )
    assert found == PUBLIC_ROUTES
