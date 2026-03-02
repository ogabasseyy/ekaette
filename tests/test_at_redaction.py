"""Tests for secret/PII redaction in AT modules.

V2 mandate: Never log API keys, SIP credentials, SMS full payloads with PII.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

AT_ROOT = Path("app/api/v1/at")
SIP_ROOT = Path("sip_bridge")

# Patterns that should NEVER appear in log calls
FORBIDDEN_LOG_PATTERNS = [
    r"at_api_key",
    r"api_key",
    r"AT_API_KEY",
    r"GOOGLE_API_KEY",
    r"webhook_shared_secret",
    r"AT_WEBHOOK_SHARED_SECRET",
    r"sip_credential",
    r"password",
]


class TestNoSecretsInLogs:
    """Ensure log calls don't include secret values."""

    def _get_log_lines(self, root: Path) -> list[tuple[Path, int, str]]:
        """Extract all lines containing logger calls from Python files."""
        results = []
        if not root.exists():
            return results
        for py_file in root.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(content.splitlines(), start=1):
                stripped = line.strip()
                if any(
                    stripped.startswith(f"logger.{level}")
                    for level in ("debug", "info", "warning", "error", "critical", "exception")
                ):
                    results.append((py_file, lineno, stripped))
        return results

    def test_at_package_logs_no_secrets(self) -> None:
        """No AT package log statement references secret field names."""
        log_lines = self._get_log_lines(AT_ROOT)
        violations = []
        for path, lineno, line in log_lines:
            for pattern in FORBIDDEN_LOG_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    violations.append(f"{path}:{lineno}: logs '{pattern}' — {line[:80]}")
        assert violations == [], f"Secret fields in logs:\n" + "\n".join(violations)

    def test_sip_bridge_logs_no_secrets(self) -> None:
        """No SIP bridge log statement references secret field names."""
        if not SIP_ROOT.exists():
            pytest.skip("sip_bridge not yet created")
        log_lines = self._get_log_lines(SIP_ROOT)
        violations = []
        for path, lineno, line in log_lines:
            for pattern in FORBIDDEN_LOG_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    violations.append(f"{path}:{lineno}: logs '{pattern}' — {line[:80]}")
        assert violations == [], f"Secret fields in logs:\n" + "\n".join(violations)


class TestStructuredLogFields:
    """Verify log calls use structured extra fields, not f-string interpolation of PII."""

    def test_no_caller_number_in_log_message_string(self) -> None:
        """Caller numbers should be in extra dict, not interpolated into message."""
        violations = []
        for py_file in AT_ROOT.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(content.splitlines(), start=1):
                stripped = line.strip()
                # Check for f-string log messages containing phone-like patterns
                if "logger." in stripped and "callerNumber" in stripped and "extra" not in stripped:
                    violations.append(f"{py_file}:{lineno}: phone number in log message string")
        assert violations == [], "PII in log message strings:\n" + "\n".join(violations)


class TestRecordingDisclosureConfig:
    """Data governance: recording must be opt-in with disclosure."""

    def test_recording_disabled_by_default(self) -> None:
        from app.api.v1.at.settings import AT_RECORDING_ENABLED
        # Default must be False (opt-in)
        assert AT_RECORDING_ENABLED is False

    def test_recording_disclosure_has_default_text(self) -> None:
        from app.api.v1.at.settings import AT_RECORDING_DISCLOSURE
        assert len(AT_RECORDING_DISCLOSURE) > 0
        assert "recorded" in AT_RECORDING_DISCLOSURE.lower()

    def test_dial_xml_no_recording_by_default(self) -> None:
        """With recording disabled, record attribute should be false."""
        from app.api.v1.at.service_voice import build_dial_xml

        xml = build_dial_xml("sip:test@example.com", "+234")
        assert 'record="false"' in xml
        assert "<Say>" not in xml  # No disclosure if not recording

    def test_dial_xml_recording_includes_disclosure(self) -> None:
        """With recording enabled, XML should include <Say> disclosure before <Dial>."""
        from unittest.mock import patch
        import app.api.v1.at.service_voice as svc

        with (
            patch.object(svc, "AT_RECORDING_ENABLED", True),
            patch.object(svc, "AT_RECORDING_DISCLOSURE", "This call is recorded."),
        ):
            xml = svc.build_dial_xml("sip:test@example.com", "+234")
            assert 'record="true"' in xml
            assert "<Say>This call is recorded.</Say>" in xml
            # Disclosure must come BEFORE Dial
            say_pos = xml.index("<Say>")
            dial_pos = xml.index("<Dial")
            assert say_pos < dial_pos


class TestRetentionConfig:
    """Data governance: retention windows must be configured."""

    def test_call_metadata_retention_has_default(self) -> None:
        from app.api.v1.at.settings import AT_CALL_METADATA_RETENTION_DAYS
        assert AT_CALL_METADATA_RETENTION_DAYS > 0

    def test_sms_retention_has_default(self) -> None:
        from app.api.v1.at.settings import AT_SMS_RETENTION_DAYS
        assert AT_SMS_RETENTION_DAYS > 0
