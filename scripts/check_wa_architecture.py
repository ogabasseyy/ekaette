"""Static gates for WhatsApp SIP bridge architecture.

Checks:
1. sip_bridge/ must NOT import from app.*
2. State-boundary ownership (cross-module import restrictions)
3. File-size caps: wa_*.py ≤400 LOC (except wa_session.py ≤500),
   codec_bridge ≤250, srtp_context ≤200
4. Required files exist
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
import sys

SIP_ROOT = Path("sip_bridge")

# File-size caps (lines of code, excluding blanks/comments)
# Explicit caps for known modules; wa_*.py modules discovered at runtime default to 400.
_EXPLICIT_FILE_SIZE_CAPS = {
    "wa_sip_client.py": 400,
    "wa_session.py": 500,
    "wa_config.py": 400,
    "wa_main.py": 400,
    "sip_tls.py": 400,
    "sip_auth.py": 400,
    "codec_bridge.py": 250,
    "srtp_context.py": 200,
}
WA_DEFAULT_LOC_CAP = 400


def _build_file_size_caps() -> dict[str, int]:
    """Build file-size caps, auto-discovering wa_*.py modules."""
    caps = dict(_EXPLICIT_FILE_SIZE_CAPS)
    if SIP_ROOT.exists():
        for p in SIP_ROOT.glob("wa_*.py"):
            if p.name not in caps:
                caps[p.name] = WA_DEFAULT_LOC_CAP
    return caps


FILE_SIZE_CAPS = _build_file_size_caps()

# State-boundary ownership: module -> forbidden import patterns
# Each module owns a specific concern and must not cross boundaries
BOUNDARY_RULES: dict[str, list[str]] = {
    # wa_sip_client: owns SIP dialog, must NOT touch codec/SRTP/Gemini/Firestore
    "wa_sip_client.py": [
        r"from\s+\.codec_bridge\s+import\s+(?!CodecBridge).*(?:decode|encode)",
        r"from\s+\.wa_session\s+import",
        r"import\s+google\.cloud\.firestore",
        r"import\s+google\.genai",
    ],
    # wa_session: owns media pipeline, must NOT touch SIP signaling/TLS
    "wa_session.py": [
        r"from\s+\.sip_tls\s+import",
        r"from\s+\.wa_sip_client\s+import",
    ],
    # srtp_context: owns SRTP, must NOT touch SIP headers/codecs/Gemini
    "srtp_context.py": [
        r"from\s+\.sip_tls\s+import",
        r"from\s+\.sip_auth\s+import",
        r"from\s+\.codec_bridge\s+import",
        r"import\s+google\.genai",
    ],
    # codec_bridge: owns codec, must NOT touch SIP/SRTP/network
    "codec_bridge.py": [
        r"from\s+\.sip_tls\s+import",
        r"from\s+\.sip_auth\s+import",
        r"from\s+\.srtp_context\s+import",
        r"import\s+socket",
    ],
    # sip_tls: owns TLS connection, must NOT touch SIP semantics/dialog
    "sip_tls.py": [
        r"from\s+\.wa_sip_client\s+import",
        r"from\s+\.wa_session\s+import",
        r"from\s+\.codec_bridge\s+import",
    ],
    # sip_auth: owns digest auth, must NOT touch transport/dialog
    "sip_auth.py": [
        r"from\s+\.sip_tls\s+import",
        r"from\s+\.wa_sip_client\s+import",
    ],
}

# Required WA files
REQUIRED_FILES = [
    "codec_bridge.py",
    "srtp_context.py",
    "sip_auth.py",
    "sip_tls.py",
    "wa_sip_client.py",
    "wa_session.py",
    "wa_config.py",
    "wa_main.py",
]


def _count_loc(path: Path) -> int:
    """Count non-blank, non-comment lines."""
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def _check_no_app_imports(errors: list[str]) -> None:
    """sip_bridge/ must NOT import from app.*"""
    pattern = re.compile(r"^\s*(from\s+app[\.\s]|import\s+app\b)")
    for file_path in sorted(SIP_ROOT.rglob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                errors.append(f"{file_path}:{lineno}: forbidden app.* import")


def _check_state_boundaries(errors: list[str]) -> None:
    """Enforce cross-module import restrictions using AST inspection."""
    for module_name, forbidden_patterns in BOUNDARY_RULES.items():
        file_path = SIP_ROOT / module_name
        if not file_path.exists():
            continue
        source = file_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            errors.append(f"{file_path}: syntax error, cannot parse")
            continue
        # Extract actual import statements via AST for accurate matching
        import_lines: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_lines.append((node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    import_lines.append(
                        (node.lineno, f"from .{module} import {alias.name}"
                         if node.level else f"from {module} import {alias.name}")
                    )
        for pat in forbidden_patterns:
            compiled = re.compile(pat)
            for lineno, import_str in import_lines:
                if compiled.search(import_str):
                    errors.append(
                        f"{file_path}:{lineno}: boundary violation — "
                        f"matches forbidden pattern: {pat}"
                    )


def _check_file_size_caps(errors: list[str]) -> None:
    """Enforce LOC caps per module."""
    for name, cap in FILE_SIZE_CAPS.items():
        file_path = SIP_ROOT / name
        if not file_path.exists():
            continue
        loc = _count_loc(file_path)
        if loc > cap:
            errors.append(f"{file_path}: {loc} LOC exceeds cap of {cap}")


def _check_required_files(errors: list[str]) -> None:
    """Check all required WA files exist."""
    for name in REQUIRED_FILES:
        file_path = SIP_ROOT / name
        if not file_path.exists():
            errors.append(f"missing required file: {file_path}")


def main() -> int:
    errors: list[str] = []

    if not SIP_ROOT.exists():
        errors.append(f"missing {SIP_ROOT}")
        print("WA architecture check failed:")
        for err in errors:
            print(f"  - {err}")
        return 1

    _check_required_files(errors)
    _check_no_app_imports(errors)
    _check_state_boundaries(errors)
    _check_file_size_caps(errors)

    if errors:
        print("WA architecture check failed:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("WA architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
