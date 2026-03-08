"""Tests for shared.phone_identity — canonical phone-based user identity."""

from __future__ import annotations

import hashlib

from shared.phone_identity import (
    canonical_phone_user_id,
    mask_phone,
    normalize_phone,
)

# ---------------------------------------------------------------------------
# mask_phone
# ---------------------------------------------------------------------------


class TestMaskPhone:
    def test_masks_full_number(self):
        assert mask_phone("+2348001234567") == "+234***4567"

    def test_short_input_returns_stars(self):
        assert mask_phone("123") == "***"

    def test_exactly_four_chars_returns_stars(self):
        assert mask_phone("1234") == "***"

    def test_five_chars_hides_three(self):
        # 5 chars: show last 2, hide 3
        assert mask_phone("12345") == "***45"

    def test_seven_chars_hides_three(self):
        # 7 chars: show last 4, hide 3
        assert mask_phone("1234567") == "***4567"

    def test_eight_chars_hides_three(self):
        # 8 chars: show 1 head + 4 tail = 5, hide 3
        assert mask_phone("12345678") == "1***5678"

    def test_nine_chars_hides_three(self):
        # 9 chars: show 2 head + 4 tail = 6, hide 3
        assert mask_phone("123456789") == "12***6789"

    def test_ten_chars_hides_three(self):
        # 10 chars: show 3 head + 4 tail = 7, hide 3
        assert mask_phone("1234567890") == "123***7890"

    def test_eleven_chars_full_mask(self):
        # 11+ chars: full 4+4 split, hide ≥ 3
        assert mask_phone("12345678901") == "1234***8901"

    def test_empty_string_returns_stars(self):
        assert mask_phone("") == "***"

    def test_whitespace_stripped(self):
        assert mask_phone("  +2348001234567  ") == "+234***4567"


# ---------------------------------------------------------------------------
# normalize_phone
# ---------------------------------------------------------------------------


class TestNormalizePhone:
    def test_already_e164(self):
        assert normalize_phone("+2348001234567") == "+2348001234567"

    def test_ng_without_plus(self):
        assert normalize_phone("2348001234567") == "+2348001234567"

    def test_local_ng_format(self):
        assert normalize_phone("08001234567") == "+2348001234567"

    def test_international_uk(self):
        result = normalize_phone("+447911123456")
        assert result == "+447911123456"

    def test_international_with_spaces(self):
        result = normalize_phone("+44 7911 123456")
        assert result == "+447911123456"

    def test_invalid_short_number(self):
        assert normalize_phone("123") is None

    def test_empty_string(self):
        assert normalize_phone("") is None

    def test_none_like_empty(self):
        assert normalize_phone("   ") is None

    def test_respects_default_region_parameter(self):
        # UK local number only works with GB region
        result = normalize_phone("07911123456", default_region="GB")
        assert result == "+447911123456"

    def test_default_region_ng_fallback(self):
        # Without explicit region, NG is the default
        result = normalize_phone("08001234567")
        assert result == "+2348001234567"


# ---------------------------------------------------------------------------
# canonical_phone_user_id
# ---------------------------------------------------------------------------


class TestCanonicalPhoneUserId:
    TENANT = "public"
    COMPANY = "ekaette-electronics"

    def test_deterministic(self):
        uid1 = canonical_phone_user_id(self.TENANT, self.COMPANY, "+2348001234567")
        uid2 = canonical_phone_user_id(self.TENANT, self.COMPANY, "+2348001234567")
        assert uid1 == uid2

    def test_same_number_different_formats(self):
        uid_e164 = canonical_phone_user_id(self.TENANT, self.COMPANY, "+2348001234567")
        uid_no_plus = canonical_phone_user_id(self.TENANT, self.COMPANY, "2348001234567")
        assert uid_e164 == uid_no_plus

    def test_local_format_matches_e164(self):
        uid_local = canonical_phone_user_id(self.TENANT, self.COMPANY, "08001234567")
        uid_e164 = canonical_phone_user_id(self.TENANT, self.COMPANY, "+2348001234567")
        assert uid_local == uid_e164

    def test_different_tenant_different_uid(self):
        uid1 = canonical_phone_user_id("tenant-a", self.COMPANY, "+2348001234567")
        uid2 = canonical_phone_user_id("tenant-b", self.COMPANY, "+2348001234567")
        assert uid1 != uid2

    def test_different_company_different_uid(self):
        uid1 = canonical_phone_user_id(self.TENANT, "company-a", "+2348001234567")
        uid2 = canonical_phone_user_id(self.TENANT, "company-b", "+2348001234567")
        assert uid1 != uid2

    def test_invalid_phone_returns_none(self):
        assert canonical_phone_user_id(self.TENANT, self.COMPANY, "123") is None

    def test_empty_phone_returns_none(self):
        assert canonical_phone_user_id(self.TENANT, self.COMPANY, "") is None

    def test_output_format(self):
        uid = canonical_phone_user_id(self.TENANT, self.COMPANY, "+2348001234567")
        assert uid is not None
        assert uid.startswith("phone-")
        assert len(uid) == 30  # "phone-" (6) + sha256[:24] (24)

    def test_passes_default_region(self):
        uid_gb = canonical_phone_user_id(
            self.TENANT, self.COMPANY, "07911123456", default_region="GB"
        )
        assert uid_gb is not None
        assert uid_gb.startswith("phone-")

    def test_hash_matches_expected_formula(self):
        phone = "+2348001234567"
        seed = f"{self.TENANT}:{self.COMPANY}:caller:{phone}"
        expected = f"phone-{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
        actual = canonical_phone_user_id(self.TENANT, self.COMPANY, phone)
        assert actual == expected


# ---------------------------------------------------------------------------
# Cross-channel equivalence
# ---------------------------------------------------------------------------


class TestCrossChannelEquivalence:
    """All phone-bearing channels must produce the same user_id for the same phone."""

    TENANT = "public"
    COMPANY = "ekaette-electronics"
    PHONE_E164 = "+2348001234567"
    PHONE_NO_PLUS = "2348001234567"
    PHONE_LOCAL = "08001234567"

    def test_sip_wa_text_wa_call_same_uid(self):
        """SIP call, WA text, and WA call from the same phone → identical user_id."""
        uid_sip = canonical_phone_user_id(self.TENANT, self.COMPANY, self.PHONE_E164)
        uid_wa_text = canonical_phone_user_id(self.TENANT, self.COMPANY, self.PHONE_NO_PLUS)
        uid_wa_call = canonical_phone_user_id(self.TENANT, self.COMPANY, self.PHONE_E164)

        assert uid_sip is not None
        assert uid_sip == uid_wa_text
        assert uid_sip == uid_wa_call

    def test_local_format_cross_channel(self):
        """Local NG format also resolves to the same user_id."""
        uid_e164 = canonical_phone_user_id(self.TENANT, self.COMPANY, self.PHONE_E164)
        uid_local = canonical_phone_user_id(self.TENANT, self.COMPANY, self.PHONE_LOCAL)
        assert uid_e164 == uid_local
