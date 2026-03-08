"""Canonical phone-based user identity — shared across all channels."""

from __future__ import annotations

import hashlib
import logging

import phonenumbers

logger = logging.getLogger(__name__)

_FALLBACK_REGION = "NG"


def mask_phone(raw: str) -> str:
    """Mask phone for safe logging: '+234***4567' or '***' if too short.

    Guarantees at least 3 characters are always hidden. The head and tail
    are capped so that ``head + tail <= len - 3``, preventing full-value
    leakage for short or mid-length inputs.
    """
    stripped = raw.strip()
    n = len(stripped)
    if n <= 4:
        return "***"
    # Desired: show up to 4 head + up to 4 tail, but hide >= 3 chars.
    max_visible = n - 3  # total chars we're allowed to show
    tail = min(4, max_visible)
    head = min(4, max_visible - tail)
    return f"{stripped[:head]}***{stripped[-tail:]}" if head > 0 else f"***{stripped[-tail:]}"


def normalize_phone(raw: str, default_region: str | None = None) -> str | None:
    """Normalize to E.164 using libphonenumber. Returns None if invalid."""
    if not raw or not raw.strip():
        return None
    region = default_region or _FALLBACK_REGION
    try:
        parsed = phonenumbers.parse(raw.strip(), region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
    except phonenumbers.NumberParseException:
        pass
    return None


def canonical_phone_user_id(
    tenant_id: str,
    company_id: str,
    raw_phone: str,
    default_region: str | None = None,
) -> str | None:
    """Canonical user_id for any phone-bearing channel.

    Returns phone-{sha256[:24]} or None if phone is invalid.
    Scoped by tenant+company to prevent cross-tenant memory leaks.
    """
    phone = normalize_phone(raw_phone, default_region=default_region)
    if phone is None:
        return None
    seed = f"{tenant_id}:{company_id}:caller:{phone}"
    return f"phone-{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
