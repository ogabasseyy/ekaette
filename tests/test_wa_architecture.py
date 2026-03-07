"""Tests for WhatsApp SIP bridge architecture invariants.

Verifies:
- No app.* imports in sip_bridge/
- State-boundary ownership respected
- File-size caps
- Required files exist
- Architecture check script returns 0
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SIP_ROOT = Path("sip_bridge")


class TestArchitectureScript:
    """The check_wa_architecture.py script passes."""

    def test_script_passes(self):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.check_wa_architecture"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Architecture check failed:\n{result.stdout}\n{result.stderr}"


class TestNoAppImports:
    """sip_bridge/ must not import from app.*"""

    def test_no_app_imports_in_sip_bridge(self):
        import re

        pattern = re.compile(r"^\s*(from\s+app[\.\s]|import\s+app\b)")
        violations = []
        for file_path in sorted(SIP_ROOT.rglob("*.py")):
            content = file_path.read_text(encoding="utf-8")
            for lineno, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    violations.append(f"{file_path}:{lineno}")
        assert not violations, f"Forbidden app.* imports:\n" + "\n".join(violations)


class TestFileSizeCaps:
    """WhatsApp modules stay within LOC limits."""

    @pytest.mark.parametrize(
        "filename,cap",
        [
            ("codec_bridge.py", 250),
            ("srtp_context.py", 200),
            ("wa_sip_client.py", 400),
            ("wa_session.py", 650),
            ("wa_config.py", 400),
            ("wa_main.py", 450),
            ("sip_tls.py", 400),
            ("sip_auth.py", 400),
        ],
    )
    def test_file_within_cap(self, filename, cap):
        path = SIP_ROOT / filename
        if not path.exists():
            pytest.skip(f"{path} does not exist yet")
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
        assert count <= cap, f"{path}: {count} LOC exceeds cap of {cap}"


class TestRequiredFiles:
    """All required WhatsApp SIP bridge files exist."""

    @pytest.mark.parametrize(
        "filename",
        [
            "codec_bridge.py",
            "srtp_context.py",
            "sip_auth.py",
            "sip_tls.py",
            "wa_sip_client.py",
            "wa_session.py",
            "wa_config.py",
            "wa_main.py",
        ],
    )
    def test_file_exists(self, filename):
        assert (SIP_ROOT / filename).exists(), f"Missing: {SIP_ROOT / filename}"
