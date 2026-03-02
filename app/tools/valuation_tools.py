"""Valuation tools — device grading, pricing, and negotiation.

Pure logic functions — no API calls. These tools implement the pricing
math for trade-in valuations. The valuation agent calls them as ADK tools.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─── Valid condition grades ─────────────────────────────────────

VALID_GRADES = ("Excellent", "Good", "Fair", "Poor")

# ─── Default pricing table (Nigerian Naira) ─────────────────────
# Used when no industry-specific pricing table is provided.

DEFAULT_PRICING: dict[str, dict[str, int]] = {
    "iPhone 15 Pro Max": {"Excellent": 650_000, "Good": 550_000, "Fair": 430_000, "Poor": 280_000},
    "iPhone 15 Pro": {"Excellent": 520_000, "Good": 440_000, "Fair": 340_000, "Poor": 220_000},
    "iPhone 15": {"Excellent": 380_000, "Good": 320_000, "Fair": 250_000, "Poor": 160_000},
    "iPhone 14 Pro Max": {"Excellent": 420_000, "Good": 350_000, "Fair": 270_000, "Poor": 175_000},
    "iPhone 14 Pro": {"Excellent": 350_000, "Good": 290_000, "Fair": 225_000, "Poor": 145_000},
    "iPhone 14": {"Excellent": 280_000, "Good": 230_000, "Fair": 180_000, "Poor": 115_000},
    "iPhone 13": {"Excellent": 200_000, "Good": 165_000, "Fair": 125_000, "Poor": 80_000},
    "Samsung S24 Ultra": {"Excellent": 550_000, "Good": 460_000, "Fair": 360_000, "Poor": 235_000},
    "Samsung S24": {"Excellent": 320_000, "Good": 270_000, "Fair": 210_000, "Poor": 140_000},
    "Samsung S23": {"Excellent": 230_000, "Good": 190_000, "Fair": 150_000, "Poor": 95_000},
    "Google Pixel 8 Pro": {"Excellent": 280_000, "Good": 230_000, "Fair": 180_000, "Poor": 115_000},
    "Google Pixel 8": {"Excellent": 200_000, "Good": 165_000, "Fair": 130_000, "Poor": 85_000},
}

# ─── Negotiation thresholds ─────────────────────────────────────

ACCEPT_THRESHOLD = 0.05   # Accept if customer ask is within 5% of offer
REJECT_THRESHOLD_ABOVE_MAX = 0.15  # Reject only if ask is >15% above max_amount


def grade_device(analysis: dict[str, Any]) -> dict[str, Any]:
    """Map vision analysis to a condition grade.

    Args:
        analysis: Vision analysis dict with device_name, condition, details.

    Returns:
        Dict with grade, device_name, and summary.
    """
    device_name = analysis.get("device_name", "Unknown")
    condition = analysis.get("condition", "Unknown")
    details = analysis.get("details", {})

    # Map condition to valid grade
    if condition in VALID_GRADES:
        grade = condition
    else:
        grade = "Fair"  # Default for unknown conditions

    # Build summary from details
    detail_parts = []
    for key in ("screen", "body", "battery", "functionality"):
        if key in details and details[key]:
            detail_parts.append(f"{key}: {details[key]}")

    summary = "; ".join(detail_parts) if detail_parts else f"Graded as {grade}"

    return {
        "device_name": device_name,
        "grade": grade,
        "summary": summary,
    }


def calculate_trade_in_value(
    device_name: str,
    grade: str,
    pricing_table: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Calculate trade-in value for a graded device.

    Args:
        device_name: Identified device model name.
        grade: Condition grade (Excellent/Good/Fair/Poor).
        pricing_table: Optional pricing table override. Uses DEFAULT_PRICING if None.

    Returns:
        Dict with offer_amount, currency, formatted price, device_name, grade.
    """
    table = pricing_table if pricing_table is not None else DEFAULT_PRICING

    # Check for unknown or missing device
    if device_name == "Unknown" or device_name not in table:
        return {
            "device_name": device_name,
            "grade": grade,
            "offer_amount": 0,
            "currency": "NGN",
            "formatted": "₦0",
            "error": f"Device '{device_name}' not found in pricing table",
        }

    device_prices = table[device_name]
    offer = device_prices.get(grade, 0)

    return {
        "device_name": device_name,
        "grade": grade,
        "offer_amount": offer,
        "currency": "NGN",
        "formatted": f"₦{offer:,}",
    }


def process_negotiation(
    offer_amount: int,
    customer_ask: int,
    max_amount: int,
) -> dict[str, Any]:
    """Process a customer's counter-offer.

    Logic:
    - If customer_ask <= offer_amount or within 5% above: ACCEPT
    - If customer_ask > (max_amount * 1.15): REJECT
    - Otherwise: COUNTER at midpoint (capped at max_amount)

    Args:
        offer_amount: Our initial offer.
        customer_ask: What the customer wants.
        max_amount: Maximum we can pay for this device+grade.

    Returns:
        Dict with decision, amounts, and explanation.
    """
    # Handle edge cases: zero or negative ask
    if customer_ask <= 0:
        return {
            "decision": "accept",
            "offer_amount": offer_amount,
            "final_amount": offer_amount,
        }

    # Customer asks at or below our offer — accept at our offer
    if customer_ask <= offer_amount:
        return {
            "decision": "accept",
            "offer_amount": offer_amount,
            "final_amount": offer_amount,
        }

    # Customer ask within acceptance threshold (5% above offer)
    threshold = offer_amount * (1 + ACCEPT_THRESHOLD)
    if customer_ask <= threshold:
        return {
            "decision": "accept",
            "offer_amount": offer_amount,
            "final_amount": customer_ask,
        }

    # Customer ask is far above max (>15%) — reject
    hard_reject_ceiling = int(max_amount * (1 + REJECT_THRESHOLD_ABOVE_MAX))
    if customer_ask > hard_reject_ceiling:
        return {
            "decision": "reject",
            "offer_amount": offer_amount,
            "max_amount": max_amount,
            "reject_threshold": hard_reject_ceiling,
        }

    # Moderate ask — counter at midpoint, capped at max
    counter = min((offer_amount + customer_ask) // 2, max_amount)
    return {
        "decision": "counter",
        "offer_amount": offer_amount,
        "counter_amount": counter,
    }


# ─── ADK Tool Wrappers ─────────────────────────────────────────


def grade_and_value_tool(
    analysis: str,
    pricing_table: str | None = None,
) -> dict[str, Any]:
    """ADK tool: Grade a device and calculate its trade-in value.

    Args:
        analysis: JSON string of vision analysis from vision_agent.
            Live API tool schemas reject free-form dict parameters (`additionalProperties`),
            so the public schema is string-based. Direct Python callers may still pass a dict.
        pricing_table: Optional JSON string pricing override. Direct dicts are also accepted
            for backward compatibility in tests/non-ADK calls.

    Returns:
        Combined grade + valuation result.
    """
    safe_analysis: dict[str, Any]
    if isinstance(analysis, dict):  # Backward compatibility for direct callers/tests.
        safe_analysis = analysis
    elif isinstance(analysis, str):
        try:
            parsed_analysis = json.loads(analysis)
        except json.JSONDecodeError as exc:
            return {
                "device_name": "Unknown",
                "grade": "Fair",
                "offer_amount": 0,
                "currency": "NGN",
                "formatted": "₦0",
                "error": f"Invalid analysis JSON: {exc}",
            }
        if not isinstance(parsed_analysis, dict):
            return {
                "device_name": "Unknown",
                "grade": "Fair",
                "offer_amount": 0,
                "currency": "NGN",
                "formatted": "₦0",
                "error": "analysis must decode to a JSON object",
            }
        safe_analysis = parsed_analysis
    else:
        return {
            "device_name": "Unknown",
            "grade": "Fair",
            "offer_amount": 0,
            "currency": "NGN",
            "formatted": "₦0",
            "error": "analysis must be a JSON string",
        }

    safe_pricing_table: dict[str, dict[str, int]] | None = None
    if isinstance(pricing_table, dict):  # Backward compatibility for direct callers/tests.
        safe_pricing_table = pricing_table
    elif isinstance(pricing_table, str) and pricing_table.strip():
        try:
            parsed_table = json.loads(pricing_table)
        except json.JSONDecodeError as exc:
            return {
                "device_name": str(safe_analysis.get("device_name", "Unknown")),
                "grade": str(safe_analysis.get("condition", "Fair")),
                "offer_amount": 0,
                "currency": "NGN",
                "formatted": "₦0",
                "error": f"Invalid pricing_table JSON: {exc}",
            }
        if isinstance(parsed_table, dict):
            safe_pricing_table = parsed_table  # Runtime validation happens downstream.

    grade_result = grade_device(safe_analysis)
    value_result = calculate_trade_in_value(
        device_name=grade_result["device_name"],
        grade=grade_result["grade"],
        pricing_table=safe_pricing_table,
    )
    # Merge both results
    return {**grade_result, **value_result}


def negotiate_tool(
    offer_amount: int,
    customer_ask: int,
    max_amount: int,
) -> dict[str, Any]:
    """ADK tool: Process a customer's counter-offer.

    Args:
        offer_amount: Our initial offer amount.
        customer_ask: Customer's requested amount.
        max_amount: Maximum acceptable amount.

    Returns:
        Negotiation decision dict.
    """
    return process_negotiation(offer_amount, customer_ask, max_amount)
