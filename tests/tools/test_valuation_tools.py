"""Tests for valuation tools — TDD for S9.

MOST THOROUGH TESTS — pricing math must be correct.
"""

import json

# ─── Pricing table used across tests ───────────────────────────

SAMPLE_PRICING = {
    "iPhone 14 Pro": {"Excellent": 280_000, "Good": 230_000, "Fair": 175_000, "Poor": 110_000},
    "iPhone 13": {"Excellent": 200_000, "Good": 165_000, "Fair": 125_000, "Poor": 80_000},
    "Samsung S24": {"Excellent": 320_000, "Good": 270_000, "Fair": 210_000, "Poor": 140_000},
}


class TestGradeDevice:
    """Test mapping vision analysis to condition grades."""

    def test_grades_excellent_condition(self):
        """Device with no damage should grade Excellent."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "iPhone 14 Pro",
            "condition": "Excellent",
            "details": {
                "screen": "Pristine, no scratches",
                "body": "No dents or marks",
                "battery": "98% health",
                "functionality": "All features working",
            },
        }
        result = grade_device(analysis)
        assert result["grade"] == "Excellent"
        assert result["device_name"] == "iPhone 14 Pro"

    def test_grades_good_condition(self):
        """Device with minor wear should grade Good."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "iPhone 14 Pro",
            "condition": "Good",
            "details": {
                "screen": "Minor scratches",
                "body": "Small scuff on corner",
                "battery": "85% health",
                "functionality": "All features working",
            },
        }
        result = grade_device(analysis)
        assert result["grade"] == "Good"

    def test_grades_fair_condition(self):
        """Device with visible damage should grade Fair."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "Samsung S24",
            "condition": "Fair",
            "details": {
                "screen": "Visible scratches across display",
                "body": "Dent on back panel",
                "battery": "72% health",
                "functionality": "Camera has minor issue",
            },
        }
        result = grade_device(analysis)
        assert result["grade"] == "Fair"

    def test_grades_poor_condition(self):
        """Device with significant damage should grade Poor."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "iPhone 13",
            "condition": "Poor",
            "details": {
                "screen": "Cracked screen",
                "body": "Multiple dents",
                "battery": "60% health",
                "functionality": "Speaker not working",
            },
        }
        result = grade_device(analysis)
        assert result["grade"] == "Poor"

    def test_defaults_to_fair_for_unknown_condition(self):
        """Unknown condition from vision should default to Fair."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "iPhone 14 Pro",
            "condition": "Unknown",
            "details": {},
        }
        result = grade_device(analysis)
        assert result["grade"] == "Fair"

    def test_returns_device_name_in_result(self):
        """Grade result should include the device name."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "Samsung S24",
            "condition": "Good",
            "details": {},
        }
        result = grade_device(analysis)
        assert result["device_name"] == "Samsung S24"

    def test_returns_summary_in_result(self):
        """Grade result should include a human-readable summary."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "iPhone 14 Pro",
            "condition": "Good",
            "details": {
                "screen": "Minor scratches",
                "body": "Small dent",
            },
        }
        result = grade_device(analysis)
        assert "summary" in result
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0


class TestCalculateTradeInValue:
    """Test trade-in price calculations — pricing math must be correct."""

    def test_returns_correct_price_for_excellent_iphone(self):
        """iPhone 14 Pro Excellent should return ₦280,000."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPhone 14 Pro",
            grade="Excellent",
            pricing_table=SAMPLE_PRICING,
        )
        assert result["offer_amount"] == 280_000
        assert result["currency"] == "NGN"

    def test_returns_correct_price_for_good_samsung(self):
        """Samsung S24 Good should return ₦270,000."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="Samsung S24",
            grade="Good",
            pricing_table=SAMPLE_PRICING,
        )
        assert result["offer_amount"] == 270_000

    def test_returns_correct_price_for_poor_iphone13(self):
        """iPhone 13 Poor should return ₦80,000."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPhone 13",
            grade="Poor",
            pricing_table=SAMPLE_PRICING,
        )
        assert result["offer_amount"] == 80_000

    def test_uses_ngn_currency(self):
        """All prices should be in Nigerian Naira (NGN)."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPhone 14 Pro",
            grade="Good",
            pricing_table=SAMPLE_PRICING,
        )
        assert result["currency"] == "NGN"

    def test_returns_formatted_price(self):
        """Should return a human-readable formatted price string."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPhone 14 Pro",
            grade="Excellent",
            pricing_table=SAMPLE_PRICING,
        )
        assert "formatted" in result
        assert "₦" in result["formatted"]
        assert "280,000" in result["formatted"]

    def test_handles_unknown_device_gracefully(self):
        """Unknown device should return zero offer with error message."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="Unknown",
            grade="Good",
            pricing_table=SAMPLE_PRICING,
        )
        assert result["offer_amount"] == 0
        assert "error" in result

    def test_handles_unknown_device_name_not_in_table(self):
        """Device not in pricing table should return zero with error."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="Nokia 3310",
            grade="Good",
            pricing_table=SAMPLE_PRICING,
        )
        assert result["offer_amount"] == 0
        assert "error" in result

    def test_returns_device_name_and_grade(self):
        """Result should echo back device_name and grade."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="Samsung S24",
            grade="Fair",
            pricing_table=SAMPLE_PRICING,
        )
        assert result["device_name"] == "Samsung S24"
        assert result["grade"] == "Fair"

    def test_uses_default_pricing_when_no_table_provided(self):
        """Should use built-in default pricing when no table given."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPhone 14 Pro",
            grade="Good",
        )
        # Should not crash and should return a valid result
        assert "offer_amount" in result
        assert isinstance(result["offer_amount"], (int, float))


class TestProcessNegotiation:
    """Test counter-offer negotiation logic."""

    def test_accepts_offer_within_5_percent(self):
        """Customer ask within 5% of offer should be accepted."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=230_000,
            customer_ask=235_000,
            max_amount=280_000,
        )
        assert result["decision"] == "accept"
        assert result["final_amount"] == 235_000

    def test_accepts_offer_at_exact_amount(self):
        """Customer accepting exact offer should be accepted."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=230_000,
            customer_ask=230_000,
            max_amount=280_000,
        )
        assert result["decision"] == "accept"
        assert result["final_amount"] == 230_000

    def test_accepts_offer_below_offer_amount(self):
        """Customer asking less than offer should accept at offer amount."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=230_000,
            customer_ask=200_000,
            max_amount=280_000,
        )
        assert result["decision"] == "accept"
        assert result["final_amount"] == 230_000

    def test_counters_moderate_ask(self):
        """Moderate ask (5-15% above offer) should trigger counter-offer."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=200_000,
            customer_ask=225_000,  # 12.5% above
            max_amount=280_000,
        )
        assert result["decision"] == "counter"
        # Counter should be midpoint between offer and customer ask
        expected_counter = (200_000 + 225_000) // 2
        assert result["counter_amount"] == expected_counter

    def test_counter_is_midpoint(self):
        """Counter offer should be the midpoint of offer and customer ask."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=100_000,
            customer_ask=120_000,  # 20% above but within max
            max_amount=150_000,
        )
        assert result["decision"] == "counter"
        assert result["counter_amount"] == 110_000  # midpoint of 100k and 120k

    def test_rejects_ask_above_max(self):
        """Ask exceeding max amount should be rejected."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=200_000,
            customer_ask=350_000,  # Way above max
            max_amount=280_000,
        )
        assert result["decision"] == "reject"
        assert result["max_amount"] == 280_000

    def test_rejects_ask_more_than_15_percent_above_max(self):
        """Ask >15% above max should be firmly rejected."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=200_000,
            customer_ask=330_000,  # >15% above max of 280k
            max_amount=280_000,
        )
        assert result["decision"] == "reject"
        assert "reject_threshold" in result

    def test_counters_when_ask_is_above_max_but_within_15_percent(self):
        """Ask slightly above max should counter, not reject."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=200_000,
            customer_ask=300_000,  # Above max of 280k, but within +15% ceiling
            max_amount=280_000,
        )
        assert result["decision"] == "counter"
        assert result["counter_amount"] <= 280_000

    def test_handles_zero_customer_ask(self):
        """Zero customer ask should accept at offer amount."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=200_000,
            customer_ask=0,
            max_amount=280_000,
        )
        assert result["decision"] == "accept"
        assert result["final_amount"] == 200_000

    def test_handles_negative_customer_ask(self):
        """Negative customer ask should accept at offer amount."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=200_000,
            customer_ask=-50_000,
            max_amount=280_000,
        )
        assert result["decision"] == "accept"
        assert result["final_amount"] == 200_000

    def test_result_includes_offer_amount(self):
        """All results should include the original offer amount."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=200_000,
            customer_ask=250_000,
            max_amount=280_000,
        )
        assert result["offer_amount"] == 200_000

    def test_counter_capped_at_max(self):
        """Counter should never exceed max_amount."""
        from app.tools.valuation_tools import process_negotiation

        result = process_negotiation(
            offer_amount=260_000,
            customer_ask=290_000,  # midpoint=275k, under max of 280k
            max_amount=280_000,
        )
        if result["decision"] == "counter":
            assert result["counter_amount"] <= 280_000


class TestValuationToolWrapper:
    """Test the ADK-compatible tool wrapper for valuation."""

    def test_grade_and_value_tool_returns_full_result(self):
        """ADK tool should return complete valuation result."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = {
            "device_name": "iPhone 14 Pro",
            "condition": "Good",
            "details": {
                "screen": "Minor scratches",
                "body": "Small dent on corner",
                "battery": "85% health",
                "functionality": "All features working",
            },
        }
        result = grade_and_value_tool(analysis)
        assert "grade" in result
        assert "offer_amount" in result
        assert "device_name" in result
        assert result["device_name"] == "iPhone 14 Pro"

    def test_grade_and_value_tool_accepts_json_string_payload(self):
        """Live-safe schema uses JSON strings and should still work."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = {
            "device_name": "iPhone 14 Pro",
            "condition": "Good",
            "details": {"screen": "Minor scratches"},
        }
        result = grade_and_value_tool(json.dumps(analysis))
        assert result["device_name"] == "iPhone 14 Pro"
        assert result["grade"] == "Good"
        assert "offer_amount" in result

    def test_grade_and_value_tool_rejects_invalid_json(self):
        """Invalid JSON should return an error payload, not raise."""
        from app.tools.valuation_tools import grade_and_value_tool

        result = grade_and_value_tool("{not-json")
        assert result["device_name"] == "Unknown"
        assert "error" in result
        assert "Invalid analysis JSON" in result["error"]

    def test_negotiate_tool_returns_decision(self):
        """ADK negotiate tool should return decision dict."""
        from app.tools.valuation_tools import negotiate_tool

        result = negotiate_tool(
            offer_amount=230_000,
            customer_ask=240_000,
            max_amount=280_000,
        )
        assert "decision" in result
        assert result["decision"] in ("accept", "counter", "reject")


# ─── Phase 1: Enhanced grade_device tests ─────────────────────


class TestGradeDeviceEnhanced:
    """Test grade_device() handles new dict-style details."""

    def test_dict_style_details_extracts_description_for_summary(self):
        """Dict-style screen/body details should use .description in summary."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "iPhone 15 Pro",
            "condition": "Good",
            "details": {
                "screen": {"description": "Light scratches near edges", "scratches": "light", "cracks": "none", "defect_locations": ["top-left"]},
                "body": {"description": "Small dent on corner", "dents": "minor", "scratches": "none", "defect_locations": ["bottom-right"]},
                "battery": "Not visible",
                "functionality": "No visible damage",
            },
        }
        result = grade_device(analysis)
        assert "Light scratches near edges" in result["summary"]
        assert "Small dent on corner" in result["summary"]

    def test_mixed_string_and_dict_details_backward_compat(self):
        """grade_device handles both string and dict format details."""
        from app.tools.valuation_tools import grade_device

        analysis = {
            "device_name": "iPhone 14",
            "condition": "Fair",
            "details": {
                "screen": "Cracked",
                "body": {"description": "Dented", "dents": "moderate", "scratches": "none", "defect_locations": []},
                "battery": "70% health",
                "functionality": "Speaker not working",
            },
        }
        result = grade_device(analysis)
        assert "Cracked" in result["summary"]
        assert "Dented" in result["summary"]


# ─── Phase 2: Grade adjustment from questionnaire ─────────────


class TestAdjustGradeFromQuestionnaire:
    """Test adjust_grade_from_questionnaire() — 18 tests."""

    def test_no_adjustments_preserves_grade(self):
        """Empty questionnaire should preserve the vision grade."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {})
        assert result["final_grade"] == "Good"
        assert result["original_vision_grade"] == "Good"
        assert result["adjustment_count"] == 0

    def test_water_damage_forces_poor(self):
        """water_damage=True should force Poor (short-circuit)."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Excellent", {"water_damage": True})
        assert result["final_grade"] == "Poor"

    def test_does_not_power_on_forces_poor(self):
        """does_not_power_on=True should force Poor."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"does_not_power_on": True})
        assert result["final_grade"] == "Poor"

    def test_account_locked_forces_poor(self):
        """account_locked=True should force Poor."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Excellent", {"account_locked": True})
        assert result["final_grade"] == "Poor"

    def test_battery_75_downgrades_1_step(self):
        """battery_health_pct=75 (60-79) → downgrade 1 step."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": 75})
        assert result["final_grade"] == "Fair"

    def test_battery_55_downgrades_2_steps(self):
        """battery_health_pct=55 (<60) → downgrade 2 steps, not 3."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Excellent", {"battery_health_pct": 55})
        assert result["final_grade"] == "Fair"

    def test_battery_0_downgrades_2_steps(self):
        """battery_health_pct=0 is valid, triggers <60 penalty (2 steps)."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Excellent", {"battery_health_pct": 0})
        assert result["final_grade"] == "Fair"

    def test_battery_string_78_coerced_to_int(self):
        """battery_health_pct='78' (string) → coerced to int, downgrade 1."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": "78"})
        assert result["final_grade"] == "Fair"

    def test_battery_bool_true_rejected(self):
        """battery_health_pct=True (bool) → rejected, no penalty."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": True})
        assert result["final_grade"] == "Good"

    def test_battery_float_rejected(self):
        """battery_health_pct=79.9 (float) → rejected, no penalty."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": 79.9})
        assert result["final_grade"] == "Good"

    def test_battery_decimal_string_rejected(self):
        """battery_health_pct='79.9' (decimal string) → rejected, no penalty."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": "79.9"})
        assert result["final_grade"] == "Good"

    def test_battery_whitespace_string_accepted(self):
        """battery_health_pct=' 78 ' (whitespace) → accepted as 78, downgrade 1."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": " 78 "})
        assert result["final_grade"] == "Fair"

    def test_battery_negative_out_of_range_skipped(self):
        """battery_health_pct=-5 → out of range, no penalty."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": -5})
        assert result["final_grade"] == "Good"

    def test_battery_over_100_out_of_range_skipped(self):
        """battery_health_pct=150 → out of range, no penalty."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": 150})
        assert result["final_grade"] == "Good"

    def test_multiple_non_critical_issues_compound(self):
        """Multiple -1 step issues should compound."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Excellent", {
            "previous_repair": True,
            "biometric_not_working": True,
        })
        assert result["final_grade"] == "Fair"

    def test_poor_cannot_downgrade_further(self):
        """Poor + more issues should stay Poor."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Poor", {
            "previous_repair": True,
            "screen_burn_in": True,
        })
        assert result["final_grade"] == "Poor"

    def test_missing_keys_ignored(self):
        """Missing keys should not trigger any penalty."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Excellent", {
            "unknown_key": True,
        })
        assert result["final_grade"] == "Excellent"

    def test_non_parseable_battery_ignored(self):
        """Non-parseable battery_health_pct (e.g. 'bad') ignored gracefully."""
        from app.tools.valuation_tools import adjust_grade_from_questionnaire

        result = adjust_grade_from_questionnaire("Good", {"battery_health_pct": "bad"})
        assert result["final_grade"] == "Good"


# ─── Phase 3: Device questionnaire ────────────────────────────


class TestGetDeviceQuestionnaire:
    """Test get_device_questionnaire() — 9 tests."""

    def test_unknown_brand_returns_universal_only(self):
        """Unknown brand → 4 universal questions only."""
        from app.tools.valuation_tools import get_device_questionnaire

        questions = get_device_questionnaire("Unknown")
        assert len(questions) == 4

    def test_apple_returns_7_questions(self):
        """Apple → 4 universal + 3 brand-specific = 7."""
        from app.tools.valuation_tools import get_device_questionnaire

        questions = get_device_questionnaire("Apple")
        assert len(questions) == 7

    def test_samsung_returns_6_questions(self):
        """Samsung → 4 universal + 2 brand-specific = 6."""
        from app.tools.valuation_tools import get_device_questionnaire

        questions = get_device_questionnaire("Samsung")
        assert len(questions) == 6

    def test_case_insensitive_brand_matching(self):
        """Brand matching should be case-insensitive."""
        from app.tools.valuation_tools import get_device_questionnaire

        assert len(get_device_questionnaire("apple")) == 7
        assert len(get_device_questionnaire("APPLE")) == 7
        assert len(get_device_questionnaire("Apple")) == 7

    def test_all_questions_have_required_fields(self):
        """Every question should have id, question, type, invert fields."""
        from app.tools.valuation_tools import get_device_questionnaire

        for brand in ("Apple", "Samsung", "Unknown"):
            questions = get_device_questionnaire(brand)
            for q in questions:
                assert "id" in q, f"Missing 'id' in question for {brand}"
                assert "question" in q, f"Missing 'question' in question for {brand}"
                assert "type" in q, f"Missing 'type' in question for {brand}"
                assert "invert" in q, f"Missing 'invert' in question for {brand}"

    def test_all_question_ids_exist_in_adjustment_table(self):
        """All question IDs should map to known adjustment keys."""
        from app.tools.valuation_tools import get_device_questionnaire

        known_keys = {
            "water_damage", "does_not_power_on", "battery_health_pct",
            "previous_repair", "biometric_not_working", "account_locked",
            "screen_burn_in", "buttons_not_functional",
        }
        for brand in ("Apple", "Samsung", "Unknown"):
            questions = get_device_questionnaire(brand)
            for q in questions:
                assert q["id"] in known_keys, f"Orphan question ID: {q['id']}"

    def test_does_not_power_on_has_invert_true(self):
        """'Does the device power on?' is positive framing → invert: true."""
        from app.tools.valuation_tools import get_device_questionnaire

        questions = get_device_questionnaire("Unknown")
        power_q = [q for q in questions if q["id"] == "does_not_power_on"]
        assert len(power_q) == 1
        assert power_q[0]["invert"] is True

    def test_water_damage_has_invert_false(self):
        """'Has device been exposed to water damage?' → invert: false."""
        from app.tools.valuation_tools import get_device_questionnaire

        questions = get_device_questionnaire("Unknown")
        water_q = [q for q in questions if q["id"] == "water_damage"]
        assert len(water_q) == 1
        assert water_q[0]["invert"] is False


class TestGetDeviceQuestionnaireTool:
    """Test the ADK tool wrapper for questionnaire."""

    def test_tool_delegates_correctly(self):
        """Tool wrapper should delegate to get_device_questionnaire."""
        from app.tools.valuation_tools import get_device_questionnaire_tool

        result = get_device_questionnaire_tool(device_brand="Apple")
        assert "questions" in result
        assert len(result["questions"]) == 7

    def test_tool_handles_missing_brand(self):
        """Missing brand should return universal questions."""
        from app.tools.valuation_tools import get_device_questionnaire_tool

        result = get_device_questionnaire_tool(device_brand="")
        assert "questions" in result
        assert len(result["questions"]) == 4

    def test_tool_returns_correct_shape(self):
        """Tool should return dict with 'questions' key containing a list."""
        from app.tools.valuation_tools import get_device_questionnaire_tool

        result = get_device_questionnaire_tool(device_brand="Samsung")
        assert isinstance(result, dict)
        assert isinstance(result["questions"], list)
        assert all(isinstance(q, dict) for q in result["questions"])


class TestNormalizeQuestionnaireAnswers:
    """Test normalize_questionnaire_answers() — 7 tests."""

    def test_invert_true_with_raw_true_becomes_false(self):
        """invert=True + raw True → False (no penalty)."""
        from app.tools.valuation_tools import normalize_questionnaire_answers

        questions = [{"id": "does_not_power_on", "type": "boolean", "invert": True}]
        result = normalize_questionnaire_answers({"does_not_power_on": True}, questions)
        assert result["does_not_power_on"] is False

    def test_invert_false_with_raw_true_stays_true(self):
        """invert=False + raw True → True (penalty applies)."""
        from app.tools.valuation_tools import normalize_questionnaire_answers

        questions = [{"id": "water_damage", "type": "boolean", "invert": False}]
        result = normalize_questionnaire_answers({"water_damage": True}, questions)
        assert result["water_damage"] is True

    def test_invert_true_with_raw_yes_string_becomes_false(self):
        """invert=True + raw 'yes' string → False."""
        from app.tools.valuation_tools import normalize_questionnaire_answers

        questions = [{"id": "does_not_power_on", "type": "boolean", "invert": True}]
        result = normalize_questionnaire_answers({"does_not_power_on": "yes"}, questions)
        assert result["does_not_power_on"] is False

    def test_invert_false_with_raw_no_string_stays_false(self):
        """invert=False + raw 'no' → False (no penalty)."""
        from app.tools.valuation_tools import normalize_questionnaire_answers

        questions = [{"id": "water_damage", "type": "boolean", "invert": False}]
        result = normalize_questionnaire_answers({"water_damage": "no"}, questions)
        assert result["water_damage"] is False

    def test_passes_through_numeric_battery(self):
        """battery_health_pct (number type) should pass through unchanged."""
        from app.tools.valuation_tools import normalize_questionnaire_answers

        questions = [{"id": "battery_health_pct", "type": "number", "invert": False}]
        result = normalize_questionnaire_answers({"battery_health_pct": 78}, questions)
        assert result["battery_health_pct"] == 78

    def test_skips_unparseable_string_values(self):
        """Unparseable string values should be skipped."""
        from app.tools.valuation_tools import normalize_questionnaire_answers

        questions = [{"id": "water_damage", "type": "boolean", "invert": False}]
        result = normalize_questionnaire_answers({"water_damage": "maybe"}, questions)
        assert "water_damage" not in result

    def test_end_to_end_face_id_yes_no_penalty(self):
        """E2E: 'yes' to 'Does Face ID work?' → biometric_not_working: False → no penalty."""
        from app.tools.valuation_tools import (
            normalize_questionnaire_answers,
            adjust_grade_from_questionnaire,
        )

        questions = [{"id": "biometric_not_working", "type": "boolean", "invert": True, "question": "Does Face ID work?"}]
        raw = {"biometric_not_working": "yes"}
        normalized = normalize_questionnaire_answers(raw, questions)
        assert normalized["biometric_not_working"] is False

        result = adjust_grade_from_questionnaire("Good", normalized)
        assert result["final_grade"] == "Good"


# ─── Phase 4: Composite grading integration ────────────────────


class TestGradeAndValueToolWithQuestionnaire:
    """Test grade_and_value_tool with optional questionnaire_answers."""

    def test_without_questionnaire_unchanged_behavior(self):
        """No questionnaire → backward-compatible behavior."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = {
            "device_name": "iPhone 14 Pro",
            "brand": "Apple",
            "condition": "Good",
            "details": {"screen": "Minor scratches"},
        }
        result = grade_and_value_tool(json.dumps(analysis))
        assert result["grade"] == "Good"
        assert "adjustments" not in result

    def test_with_questionnaire_adjusts_grade(self):
        """Questionnaire answers should adjust the grade."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = {
            "device_name": "iPhone 14 Pro",
            "brand": "Apple",
            "condition": "Good",
            "details": {"screen": "Minor scratches"},
        }
        answers = json.dumps({"battery_health_pct": 75, "biometric_not_working": "yes"})
        result = grade_and_value_tool(json.dumps(analysis), questionnaire_answers=answers)
        # battery_health_pct=75 → -1 step; biometric_not_working="yes" inverted to False (no penalty)
        assert result["grade"] == "Fair"
        assert "adjustments" in result
        assert result["original_vision_grade"] == "Good"

    def test_adjusted_grade_produces_lower_price(self):
        """Downgraded grade should produce a lower offer than unadjusted."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = json.dumps({
            "device_name": "iPhone 14 Pro",
            "brand": "Apple",
            "condition": "Good",
            "details": {},
        })
        unadjusted = grade_and_value_tool(analysis)
        adjusted = grade_and_value_tool(
            analysis,
            questionnaire_answers=json.dumps({"water_damage": True}),
        )
        assert adjusted["offer_amount"] < unadjusted["offer_amount"]

    def test_malformed_questionnaire_falls_back_to_vision_only(self):
        """Malformed JSON questionnaire → ignored, vision-only grade."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = json.dumps({
            "device_name": "iPhone 14 Pro",
            "brand": "Apple",
            "condition": "Good",
            "details": {},
        })
        result = grade_and_value_tool(analysis, questionnaire_answers="{bad-json")
        assert result["grade"] == "Good"
        assert "adjustments" not in result

    def test_partial_questionnaire_applies_only_present_penalties(self):
        """Only keys present in questionnaire should trigger penalties."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = json.dumps({
            "device_name": "iPhone 14 Pro",
            "brand": "Apple",
            "condition": "Excellent",
            "details": {},
        })
        result = grade_and_value_tool(
            analysis,
            questionnaire_answers=json.dumps({"previous_repair": True}),
        )
        assert result["grade"] == "Good"  # Excellent -1 = Good
        assert result["adjustment_count"] == 1


# ─── Phase 5: Percentage-based trade-in pricing ──────────────


class TestTradeInMultipliers:
    """Test TRADE_IN_MULTIPLIERS constants."""

    def test_multipliers_exist(self):
        """TRADE_IN_MULTIPLIERS should be defined with all 4 grades."""
        from app.tools.valuation_tools import TRADE_IN_MULTIPLIERS

        assert "Excellent" in TRADE_IN_MULTIPLIERS
        assert "Good" in TRADE_IN_MULTIPLIERS
        assert "Fair" in TRADE_IN_MULTIPLIERS
        assert "Poor" in TRADE_IN_MULTIPLIERS

    def test_multipliers_are_decreasing(self):
        """Multipliers should decrease from Excellent to Poor."""
        from app.tools.valuation_tools import TRADE_IN_MULTIPLIERS

        assert TRADE_IN_MULTIPLIERS["Excellent"] > TRADE_IN_MULTIPLIERS["Good"]
        assert TRADE_IN_MULTIPLIERS["Good"] > TRADE_IN_MULTIPLIERS["Fair"]
        assert TRADE_IN_MULTIPLIERS["Fair"] > TRADE_IN_MULTIPLIERS["Poor"]

    def test_multipliers_are_between_0_and_1(self):
        """All multipliers should be between 0 and 1."""
        from app.tools.valuation_tools import TRADE_IN_MULTIPLIERS

        for grade, mult in TRADE_IN_MULTIPLIERS.items():
            assert 0 < mult < 1, f"{grade} multiplier {mult} not in (0, 1)"


class TestCalculateTradeInWithRetailPrice:
    """Test percentage-based pricing from retail_price fallback."""

    def test_retail_price_used_when_device_not_in_table(self):
        """Device not in pricing table + retail_price → percentage-based pricing."""
        from app.tools.valuation_tools import calculate_trade_in_value, TRADE_IN_MULTIPLIERS

        result = calculate_trade_in_value(
            device_name="iPad Air M2",
            grade="Good",
            retail_price=680_000,
        )
        expected = round(680_000 * TRADE_IN_MULTIPLIERS["Good"])
        assert result["offer_amount"] == expected
        assert result["currency"] == "NGN"
        assert "error" not in result
        assert result["pricing_method"] == "percentage"

    def test_retail_price_excellent_grade(self):
        """Excellent grade should use 68% of retail price."""
        from app.tools.valuation_tools import calculate_trade_in_value, TRADE_IN_MULTIPLIERS

        result = calculate_trade_in_value(
            device_name="MacBook Air M3",
            grade="Excellent",
            retail_price=1_250_000,
        )
        assert result["offer_amount"] == round(1_250_000 * TRADE_IN_MULTIPLIERS["Excellent"])
        assert result["pricing_method"] == "percentage"

    def test_retail_price_poor_grade(self):
        """Poor grade should use 29% of retail price."""
        from app.tools.valuation_tools import calculate_trade_in_value, TRADE_IN_MULTIPLIERS

        result = calculate_trade_in_value(
            device_name="AirPods Pro 2",
            grade="Poor",
            retail_price=180_000,
        )
        assert result["offer_amount"] == round(180_000 * TRADE_IN_MULTIPLIERS["Poor"])

    def test_hardcoded_table_takes_precedence_over_retail_price(self):
        """Device in DEFAULT_PRICING should use table price, not retail_price."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPhone 15 Pro Max",
            grade="Excellent",
            retail_price=950_000,  # Retail price provided but should be ignored
        )
        # Should use DEFAULT_PRICING value (650,000), not 68% of 950k (646,000)
        assert result["offer_amount"] == 650_000
        assert result.get("pricing_method") != "percentage"

    def test_no_retail_price_and_not_in_table_returns_error(self):
        """Device not in table and no retail_price → error."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPad Air M2",
            grade="Good",
        )
        assert result["offer_amount"] == 0
        assert "error" in result

    def test_retail_price_zero_returns_error(self):
        """Zero retail_price should not be used for calculation."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPad Air M2",
            grade="Good",
            retail_price=0,
        )
        assert result["offer_amount"] == 0
        assert "error" in result

    def test_retail_price_negative_returns_error(self):
        """Negative retail_price should not be used for calculation."""
        from app.tools.valuation_tools import calculate_trade_in_value

        result = calculate_trade_in_value(
            device_name="iPad Air M2",
            grade="Good",
            retail_price=-100_000,
        )
        assert result["offer_amount"] == 0
        assert "error" in result

    def test_formatted_price_with_retail_fallback(self):
        """Percentage-based pricing should still return formatted price."""
        from app.tools.valuation_tools import calculate_trade_in_value, TRADE_IN_MULTIPLIERS

        result = calculate_trade_in_value(
            device_name="iPad Air M2",
            grade="Fair",
            retail_price=680_000,
        )
        expected = round(680_000 * TRADE_IN_MULTIPLIERS["Fair"])
        assert result["formatted"] == f"₦{expected:,}"


class TestGradeAndValueToolWithRetailPrice:
    """Test retail_price parameter wired into grade_and_value_tool."""

    def test_retail_price_passed_through(self):
        """retail_price param should be forwarded to calculate_trade_in_value."""
        from app.tools.valuation_tools import grade_and_value_tool

        analysis = json.dumps({
            "device_name": "iPad Air M2",
            "brand": "Apple",
            "condition": "Good",
            "details": {},
        })
        result = grade_and_value_tool(analysis, retail_price=680_000)
        assert result["offer_amount"] > 0
        assert "error" not in result
        assert result["pricing_method"] == "percentage"

    def test_retail_price_with_questionnaire(self):
        """retail_price + questionnaire should both work together."""
        from app.tools.valuation_tools import grade_and_value_tool, TRADE_IN_MULTIPLIERS

        analysis = json.dumps({
            "device_name": "iPad Air M2",
            "brand": "Apple",
            "condition": "Good",
            "details": {},
        })
        answers = json.dumps({"previous_repair": True})
        result = grade_and_value_tool(
            analysis,
            retail_price=680_000,
            questionnaire_answers=answers,
        )
        # Good -1 step = Fair
        assert result["grade"] == "Fair"
        expected = round(680_000 * TRADE_IN_MULTIPLIERS["Fair"])
        assert result["offer_amount"] == expected
