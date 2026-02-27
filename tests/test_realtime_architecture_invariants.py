"""Architecture invariants for realtime websocket modularization."""

from __future__ import annotations

from pathlib import Path
import re


def test_no_direct_main_imports_under_realtime_package():
    pattern = re.compile(r"^\s*(from\s+main\s+import|import\s+main\b)")
    realtime_root = Path("app/api/v1/realtime")
    assert realtime_root.exists()

    violations: list[str] = []
    for file_path in sorted(realtime_root.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{file_path}:{lineno}")
    assert not violations, f"forbidden main imports found: {violations}"


def test_websocket_route_still_delegates_to_realtime_module():
    from main import app

    matches = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path != "/ws/{user_id}/{session_id}":
            continue
        module = getattr(route.endpoint, "__module__", "")
        name = getattr(route.endpoint, "__name__", "")
        matches.append((path, module, name))

    assert matches, "websocket route not mounted"
    assert all(
        module == "main" and name == "websocket_endpoint"
        for _, module, name in matches
    )
