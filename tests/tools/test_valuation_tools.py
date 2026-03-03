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
