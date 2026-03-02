"""Tests for PII redaction utility."""

import pytest
from app.tools.pii_redaction import redact_pii, redact_dict_pii


class TestRedactPii:
    """Test PII redaction on plain text."""

    def test_redacts_nigerian_phone_number(self):
        """Nigerian phone numbers (+234...) should be masked."""
        text = "Call me at +2348012345678"
        result = redact_pii(text)
        assert "+2348012345678" not in result
        assert "+234" in result  # prefix preserved
        assert "***" in result

    def test_redacts_local_nigerian_phone_number(self):
        """Local Nigerian numbers (0801...) should be masked."""
        text = "My number is 08012345678"
        result = redact_pii(text)
        assert "08012345678" not in result
        assert "080" in result  # first 3 digits preserved
        assert "***" in result

    def test_redacts_international_phone_number(self):
        """International phone numbers (+1, +44, etc.) should be masked."""
        text = "Reach me at +14155551234"
        result = redact_pii(text)
        assert "+14155551234" not in result
        assert "***" in result

    def test_redacts_email_address(self):
        """Email addresses should be masked."""
        text = "Send to bassey@gmail.com"
        result = redact_pii(text)
        assert "bassey@gmail.com" not in result
        assert "***@gmail.com" in result

    def test_redacts_email_preserves_domain(self):
        """Email redaction preserves the domain for debugging."""
        text = "user@example.org"
        result = redact_pii(text)
        assert "user" not in result
        assert "@example.org" in result

    def test_preserves_non_pii_text(self):
        """Non-PII text should pass through unchanged."""
        text = "The product is a Samsung Galaxy S24"
        result = redact_pii(text)
        assert result == text

    def test_redacts_multiple_pii_in_same_text(self):
        """Multiple PII items in one string should all be redacted."""
        text = "Call +2348012345678 or email bassey@gmail.com"
        result = redact_pii(text)
        assert "+2348012345678" not in result
        assert "bassey@gmail.com" not in result
        assert "***" in result

    def test_handles_empty_string(self):
        """Empty string returns empty."""
        assert redact_pii("") == ""

    def test_handles_none_gracefully(self):
        """None input returns empty string."""
        assert redact_pii(None) == ""

    def test_redacts_phone_with_spaces(self):
        """Phone numbers with spaces should be detected."""
        text = "Call +234 801 234 5678"
        result = redact_pii(text)
        assert "234 5678" not in result

    def test_redacts_phone_with_dashes(self):
        """Phone numbers with dashes should be detected."""
        text = "Call +234-801-234-5678"
        result = redact_pii(text)
        assert "234-5678" not in result


class TestRedactDictPii:
    """Test PII redaction on dictionary fields."""

    def test_redacts_specified_fields(self):
        """Only specified fields should be redacted."""
        data = {"name": "Bassey", "phone": "+2348012345678", "item": "iPhone 15"}
        result = redact_dict_pii(data, fields=["phone"])
        assert "+2348012345678" not in result["phone"]
        assert result["item"] == "iPhone 15"

    def test_preserves_unspecified_fields(self):
        """Fields not in the redaction list should be untouched."""
        data = {"email": "test@example.com", "status": "active"}
        result = redact_dict_pii(data, fields=["email"])
        assert "test@example.com" not in result["email"]
        assert result["status"] == "active"

    def test_handles_missing_fields_gracefully(self):
        """Fields in redaction list but not in dict should not raise."""
        data = {"name": "Bassey"}
        result = redact_dict_pii(data, fields=["phone", "email"])
        assert result == data

    def test_does_not_mutate_original(self):
        """Original dict should not be modified."""
        data = {"phone": "+2348012345678"}
        original_phone = data["phone"]
        redact_dict_pii(data, fields=["phone"])
        assert data["phone"] == original_phone

    def test_handles_non_string_values(self):
        """Non-string values in redaction fields should pass through."""
        data = {"phone": 12345, "count": 5}
        result = redact_dict_pii(data, fields=["phone"])
        assert result["phone"] == 12345
