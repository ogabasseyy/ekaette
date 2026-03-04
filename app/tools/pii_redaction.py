"""PII redaction utility for masking personal data in text and dicts.

Regex-based redaction for phone numbers, email addresses, etc.
No external dependencies — stdlib re module only.
"""

from __future__ import annotations

import re

# ── Phone patterns ──────────────────────────────────────────────────────
# International: +{country code}{number} with optional spaces/dashes
_PHONE_INTL_RE = re.compile(
    r"\+(\d{1,3})[\s\-]?(\d{1,4})[\s\-]?\d[\d\s\-]{4,10}\d"
)
# Nigerian local: 0{7-9}0{8 digits}
_PHONE_NG_LOCAL_RE = re.compile(
    r"\b(0[7-9]\d)[\s\-]?\d[\d\s\-]{5,8}\d\b"
)

# ── Email pattern ───────────────────────────────────────────────────────
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+-]{1,64}(@[a-zA-Z0-9.-]{1,253}\.[a-zA-Z]{2,})"
)


def _mask_intl_phone(match: re.Match) -> str:
    """Mask international phone: preserve +{country} + first few digits."""
    country = match.group(1)
    area = match.group(2)
    return f"+{country}{area}***"


def _mask_ng_local_phone(match: re.Match) -> str:
    """Mask Nigerian local phone: preserve first 3 digits."""
    prefix = match.group(1)
    return f"{prefix}***"


def _mask_email(match: re.Match) -> str:
    """Mask email: replace local part, preserve domain."""
    domain = match.group(1)
    return f"***{domain}"


def redact_pii(text: str | None) -> str:
    """Redact PII from a text string.

    Masks phone numbers and email addresses.  Returns empty string for
    None input.
    """
    if not text:
        return ""
    result = _PHONE_INTL_RE.sub(_mask_intl_phone, text)
    result = _PHONE_NG_LOCAL_RE.sub(_mask_ng_local_phone, result)
    result = _EMAIL_RE.sub(_mask_email, result)
    return result


def redact_dict_pii(data: dict, fields: list[str]) -> dict:
    """Redact PII from specified dict fields.  Returns a new dict."""
    result = dict(data)
    for field in fields:
        if field in result and isinstance(result[field], str):
            result[field] = redact_pii(result[field])
    return result
