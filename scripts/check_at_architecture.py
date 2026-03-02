"""Static gates for AT channel modularization architecture.

Checks:
1. No `import main` under app/api/v1/at/ or sip_bridge/
2. Dependency direction: routes → services → providers (no reverse)
3. File-size caps: routes ≤250, services/providers ≤350, bridge ≤400 LOC
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys


AT_ROOT = Path("app/api/v1/at")
SIP_ROOT = Path("sip_bridge")

# File-size caps (lines of code, excluding blanks/comments)
ROUTE_MAX_LOC = 250
SERVICE_MAX_LOC = 350
PROVIDER_MAX_LOC = 350
BRIDGE_MAX_LOC = 400

# Route modules (thin handlers)
ROUTE_MODULES = {"voice.py", "sms.py"}
# Service/provider modules
SERVICE_MODULES = {"service_voice.py", "service_sms.py", "providers.py", "bridge_text.py"}


def _count_loc(path: Path) -> int:
    """Count non-blank, non-comment lines."""
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def _check_no_main_imports(errors: list[str]) -> None:
    """No module under AT or sip_bridge may import main (AST-based)."""
    for root in (AT_ROOT, SIP_ROOT):
        if not root.exists():
            continue
        for file_path in sorted(root.rglob("*.py")):
            source = file_path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(file_path))
            except SyntaxError:
                errors.append(f"{file_path}: syntax error, cannot parse")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "main" or alias.name.startswith("main."):
                            errors.append(f"{file_path}:{node.lineno}: forbidden main import")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "main" or module.startswith("main."):
                        errors.append(f"{file_path}:{node.lineno}: forbidden main import")


def _extract_relative_imports(file_path: Path) -> list[tuple[int, str]]:
    """Extract relative import module names via AST. Returns (lineno, module_name) pairs."""
    source = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            module = node.module or ""
            results.append((node.lineno, module))
    return results


# Forbidden relative import targets per module
_PROVIDER_FORBIDDEN_IMPORTS = {"voice", "sms", "service_voice", "service_sms"}
_SERVICE_FORBIDDEN_IMPORTS = {"voice", "sms"}


def _check_dependency_direction(errors: list[str]) -> None:
    """Enforce route -> service -> provider direction (AST-based).

    - providers.py must NOT import from voice, sms, service_voice, service_sms
    - service_*.py must NOT import from voice, sms
    """
    if not AT_ROOT.exists():
        return

    # Providers must not import from routes or services
    providers_path = AT_ROOT / "providers.py"
    if providers_path.exists():
        for lineno, module in _extract_relative_imports(providers_path):
            if module in _PROVIDER_FORBIDDEN_IMPORTS:
                errors.append(
                    f"{providers_path}:{lineno}: providers.py imports "
                    f"from route/service module .{module} (forbidden)"
                )

    # Services must not import from routes
    for svc_name in ("service_voice.py", "service_sms.py"):
        svc_path = AT_ROOT / svc_name
        if not svc_path.exists():
            continue
        for lineno, module in _extract_relative_imports(svc_path):
            if module in _SERVICE_FORBIDDEN_IMPORTS:
                errors.append(
                    f"{svc_path}:{lineno}: service imports from route module .{module} (forbidden)"
                )


def _check_file_size_caps(errors: list[str]) -> None:
    """Enforce LOC caps per module type."""
    if not AT_ROOT.exists():
        return

    for file_path in sorted(AT_ROOT.glob("*.py")):
        name = file_path.name
        if name == "__init__.py" or name == "__pycache__":
            continue
        loc = _count_loc(file_path)

        if name in ROUTE_MODULES and loc > ROUTE_MAX_LOC:
            errors.append(f"{file_path}: {loc} LOC exceeds route cap of {ROUTE_MAX_LOC}")
        elif name in SERVICE_MODULES and loc > SERVICE_MAX_LOC:
            errors.append(f"{file_path}: {loc} LOC exceeds service/provider cap of {SERVICE_MAX_LOC}")

    # SIP bridge modules
    if SIP_ROOT.exists():
        for file_path in sorted(SIP_ROOT.glob("*.py")):
            if file_path.name == "__init__.py":
                continue
            loc = _count_loc(file_path)
            if loc > BRIDGE_MAX_LOC:
                errors.append(f"{file_path}: {loc} LOC exceeds bridge cap of {BRIDGE_MAX_LOC}")


def main() -> int:
    errors: list[str] = []

    if not AT_ROOT.exists():
        errors.append(f"missing {AT_ROOT}")

    _check_no_main_imports(errors)
    _check_dependency_direction(errors)
    _check_file_size_caps(errors)

    if errors:
        print("AT architecture check failed:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("AT architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
