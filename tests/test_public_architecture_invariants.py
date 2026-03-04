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
    public_root = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "public"
    assert public_root.exists(), f"public package not found at {public_root}"

    violations: list[str] = []
    for file_path in sorted(public_root.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{file_path}:{lineno}")
    assert not violations, f"forbidden main imports found: {violations}"


def test_public_routes_still_mounted_with_expected_paths_and_methods():
    from main import app

    # Prefixes that identify routes served by the public package
    PUBLIC_PREFIXES = ("/api/token", "/api/onboarding/", "/api/v1/runtime/", "/api/upload/")

    found: set[tuple[str, str]] = set()
    unexpected: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not isinstance(path, str):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            pair = (method, path)
            if pair in PUBLIC_ROUTES:
                found.add(pair)
            elif any(path.startswith(prefix) or path == prefix.rstrip("/") for prefix in PUBLIC_PREFIXES):
                unexpected.add(pair)

    assert found == PUBLIC_ROUTES, f"Missing public routes: {PUBLIC_ROUTES - found}"
    assert not unexpected, (
        f"Unexpected routes found under public prefixes — add to PUBLIC_ROUTES or remove: {unexpected}"
    )
