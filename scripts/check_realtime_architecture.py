"""Static/runtime gates for realtime websocket modularization architecture."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REALTIME_ROOT = Path("app/api/v1/realtime")
MAIN_PATH = Path("main.py")


def _check_no_main_imports(errors: list[str]) -> None:
    pattern = re.compile(r"^\s*(from\s+main\s+import|import\s+main\b)")
    for file_path in sorted(REALTIME_ROOT.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                errors.append(f"{file_path}:{lineno}: forbidden main import")


def _check_main_ws_delegate(errors: list[str]) -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(MAIN_PATH))

    wrapper_ok = False
    for node in module.body:
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "websocket_endpoint":
            continue
        # Walk the AST body looking for the two required calls:
        #   1. _sync_realtime_runtime()
        #   2. realtime_ws.websocket_endpoint(...)
        found_sync = False
        found_delegate = False
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            # Match _sync_realtime_runtime()
            if isinstance(func, ast.Name) and func.id == "_sync_realtime_runtime":
                found_sync = True
            # Match realtime_ws.websocket_endpoint(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "websocket_endpoint"
                and isinstance(func.value, ast.Name)
                and func.value.id == "realtime_ws"
            ):
                found_delegate = True
        if found_sync and found_delegate:
            wrapper_ok = True
            break

    if not wrapper_ok:
        errors.append("realtime websocket delegate wrapper missing/changed")


def main() -> int:
    errors: list[str] = []
    if not REALTIME_ROOT.exists():
        errors.append(f"missing {REALTIME_ROOT}")
    if not MAIN_PATH.exists():
        errors.append(f"missing {MAIN_PATH}")

    if not errors:
        _check_no_main_imports(errors)
        _check_main_ws_delegate(errors)

    if errors:
        print("Realtime architecture check failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("Realtime architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
