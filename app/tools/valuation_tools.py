"""Valuation tools — device grading, pricing, and negotiation.

Pure logic functions — no API calls. These tools implement the pricing
math for trade-in valuations. The valuation agent calls them as ADK tools.
"""

import copy
import json
import logging
import re
from typing import Any

from google.adk.tools.tool_context import ToolContext

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

# ─── Trade-in multipliers (percentage of retail price) ────────

TRADE_IN_MULTIPLIERS: dict[str, float] = {
    "Excellent": 0.68,
    "Good": 0.58,
    "Fair": 0.45,
    "Poor": 0.29,
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
    details = analysis.get("details") or {}
    if not isinstance(details, dict):
        details = {}

    # Map condition to valid grade
    if condition in VALID_GRADES:
        grade = condition
    else:
        grade = "Fair"  # Default for unknown conditions

    # Build summary from details — handle both string and dict formats
    detail_parts = []
    for key in ("screen", "body", "battery", "functionality"):
        val = details.get(key)
        if not val:
            continue
        if isinstance(val, dict):
            desc = val.get("description", "")
            if desc:
                detail_parts.append(f"{key}: {desc}")
        else:
            detail_parts.append(f"{key}: {val}")

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
    retail_price: int | None = None,
) -> dict[str, Any]:
    """Calculate trade-in value for a graded device.

    Args:
        device_name: Identified device model name.
        grade: Condition grade (Excellent/Good/Fair/Poor).
        pricing_table: Optional pricing table override. Uses DEFAULT_PRICING if None.
        retail_price: Optional retail price for percentage-based fallback.

    Returns:
        Dict with offer_amount, currency, formatted price, device_name, grade.
    """
    table = pricing_table if pricing_table is not None else DEFAULT_PRICING

    # Check hardcoded/custom pricing table first
    if device_name != "Unknown" and device_name in table:
        device_prices = table[device_name]
        if isinstance(device_prices, dict):
            raw_offer = device_prices.get(grade, 0)
            try:
                offer = int(raw_offer)
            except (TypeError, ValueError):
                offer = 0
            return {
                "device_name": device_name,
                "grade": grade,
                "offer_amount": offer,
                "currency": "NGN",
                "formatted": f"₦{offer:,}",
            }

    # Percentage-based fallback from retail price
    if isinstance(retail_price, int) and retail_price > 0:
        multiplier = TRADE_IN_MULTIPLIERS.get(grade, TRADE_IN_MULTIPLIERS["Fair"])
        offer = round(retail_price * multiplier)
        return {
            "device_name": device_name,
            "grade": grade,
            "offer_amount": offer,
            "currency": "NGN",
            "formatted": f"₦{offer:,}",
            "pricing_method": "percentage",
            "retail_price": retail_price,
        }

    return {
        "device_name": device_name,
        "grade": grade,
        "offer_amount": 0,
        "currency": "NGN",
        "formatted": "₦0",
        "error": f"Device '{device_name}' not found in pricing table",
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


# ─── Grade adjustment from questionnaire ──────────────────────

GRADE_ORDER = ["Excellent", "Good", "Fair", "Poor"]

_INTEGER_RE = re.compile(r"^\s*-?\d+\s*$")


def _downgrade(grade: str, steps: int) -> str:
    """Downgrade a grade by N steps, clamped at Poor."""
    try:
        idx = GRADE_ORDER.index(grade)
    except ValueError:
        idx = 2  # Default to Fair
    return GRADE_ORDER[min(idx + steps, len(GRADE_ORDER) - 1)]


def _parse_battery(value: Any) -> int | None:
    """Parse battery_health_pct. Returns int 0-100 or None if invalid."""
    # Reject bool first (isinstance(True, int) is True in Python)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if 0 <= value <= 100:
            return value
        return None
    # Reject float
    if isinstance(value, float):
        return None
    # Accept integer strings only (no decimals)
    if isinstance(value, str):
        if not _INTEGER_RE.match(value):
            return None
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        if 0 <= parsed <= 100:
            return parsed
    return None


def adjust_grade_from_questionnaire(
    vision_grade: str,
    questionnaire_answers: dict[str, Any],
) -> dict[str, Any]:
    """Adjust vision grade based on questionnaire answers.

    Precedence (short-circuits on force-Poor):
    1. Force-Poor: does_not_power_on, water_damage, account_locked
    2. Battery: <60 → -2 steps, 60-79 → -1 step
    3. Other downgrades: previous_repair, biometric_not_working, screen_burn_in, buttons_not_functional
    """
    adjustments: list[str] = []

    # Force-Poor rules (checked first, short-circuit)
    for key, label in [
        ("does_not_power_on", "Device does not power on"),
        ("water_damage", "Water damage detected"),
        ("account_locked", "Account locked"),
    ]:
        if questionnaire_answers.get(key) is True:
            return {
                "final_grade": "Poor",
                "original_vision_grade": vision_grade,
                "adjustments": [f"{label} → forced Poor"],
                "adjustment_count": 1,
            }

    grade = vision_grade if vision_grade in GRADE_ORDER else "Fair"

    # Battery
    battery_val = questionnaire_answers.get("battery_health_pct")
    battery_pct = _parse_battery(battery_val)
    if battery_pct is not None:
        if battery_pct < 60:
            grade = _downgrade(grade, 2)
            adjustments.append(f"Battery at {battery_pct}% → -2 steps")
        elif battery_pct < 80:
            grade = _downgrade(grade, 1)
            adjustments.append(f"Battery at {battery_pct}% → -1 step")

    # Other downgrades (compound)
    for key, label in [
        ("previous_repair", "Previous repair"),
        ("biometric_not_working", "Biometric not working"),
        ("screen_burn_in", "Screen burn-in"),
        ("buttons_not_functional", "Buttons not functional"),
    ]:
        if questionnaire_answers.get(key) is True:
            grade = _downgrade(grade, 1)
            adjustments.append(f"{label} → -1 step")

    return {
        "final_grade": grade,
        "original_vision_grade": vision_grade,
        "adjustments": adjustments,
        "adjustment_count": len(adjustments),
    }


# ─── Device questionnaire ─────────────────────────────────────

UNIVERSAL_QUESTIONS: list[dict[str, Any]] = [
    {"id": "does_not_power_on", "question": "Does the device power on and hold a charge?", "type": "boolean", "invert": True},
    {"id": "water_damage", "question": "Has the device ever been exposed to water damage?", "type": "boolean", "invert": False},
    {"id": "buttons_not_functional", "question": "Are all physical buttons working?", "type": "boolean", "invert": True},
    {"id": "previous_repair", "question": "Has the device been repaired before?", "type": "boolean", "invert": False},
]

BRAND_QUESTIONS: dict[str, list[dict[str, Any]]] = {
    "apple": [
        {"id": "battery_health_pct", "question": "What's the battery health %? (Settings → Battery → Battery Health)", "type": "number", "invert": False},
        {"id": "account_locked", "question": "Have you signed out of iCloud and disabled Find My?", "type": "boolean", "invert": True},
        {"id": "biometric_not_working", "question": "Does Face ID / Touch ID work?", "type": "boolean", "invert": True},
    ],
    "samsung": [
        {"id": "screen_burn_in", "question": "Do you notice any ghost images or burn-in on the display?", "type": "boolean", "invert": False},
        {"id": "account_locked", "question": "Have you removed your Samsung account?", "type": "boolean", "invert": True},
    ],
}


def get_device_questionnaire(
    device_brand: str,
    analysis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Get brand-specific questionnaire questions.

    Args:
        device_brand: Device brand (e.g. "Apple", "Samsung").
        analysis: Optional tool-backed vision analysis used to skip questions
            already resolved from visible evidence.

    Returns:
        List of question dicts with id, question, type, invert fields.
    """
    questions = copy.deepcopy(UNIVERSAL_QUESTIONS)
    brand_key = (device_brand.strip().lower() if isinstance(device_brand, str) else "")
    brand_specific = BRAND_QUESTIONS.get(brand_key, [])
    questions.extend(copy.deepcopy(brand_specific))
    return _filter_questions_from_analysis(questions, analysis)


def _normalize_power_state_from_analysis(analysis: dict[str, Any] | None) -> str:
    if not isinstance(analysis, dict):
        return "unknown"
    raw = analysis.get("power_state", "unknown")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"on", "off", "unknown"}:
            return normalized
    return "unknown"


def _question_ids_omitted_from_analysis(analysis: dict[str, Any] | None) -> list[str]:
    omitted: list[str] = []
    if _normalize_power_state_from_analysis(analysis) == "on":
        omitted.append("does_not_power_on")
    return omitted


def _filter_questions_from_analysis(
    questions: list[dict[str, Any]],
    analysis: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    omitted = set(_question_ids_omitted_from_analysis(analysis))
    if not omitted:
        return questions
    return [question for question in questions if question.get("id") not in omitted]


# ─── Questionnaire answer normalization ───────────────────────

_YES_VALUES = {"yes", "true", "1", "y"}
_NO_VALUES = {"no", "false", "0", "n"}


def _coerce_bool(value: Any) -> bool | None:
    """Coerce string/bool to bool. Returns None if unparseable."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _YES_VALUES:
        return True
    if isinstance(value, str) and value.strip().lower() in _NO_VALUES:
        return False
    return None


def normalize_questionnaire_answers(
    raw_answers: dict[str, Any],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply invert flags and type coercion deterministically.

    Expects RAW customer answers ("yes"/"no"/True/False).
    The agent instruction tells the model to pass raw answers without
    pre-interpreting. Inversion is applied HERE, not by the agent.
    """
    invert_map = {q["id"]: q.get("invert", False) for q in questions}
    type_map = {q["id"]: q.get("type", "boolean") for q in questions}
    result: dict[str, Any] = {}
    for key, value in raw_answers.items():
        if type_map.get(key) == "number":
            result[key] = value
        else:
            coerced = _coerce_bool(value)
            if coerced is None:
                continue
            if invert_map.get(key, False):
                coerced = not coerced
            result[key] = coerced
    return result


# ─── ADK Tool Wrappers ─────────────────────────────────────────


def grade_and_value_tool(
    analysis: str,
    pricing_table: str | None = None,
    questionnaire_answers: str | None = None,
    retail_price: int | None = None,
) -> dict[str, Any]:
    """ADK tool: Grade a device and calculate its trade-in value.

    Args:
        analysis: JSON string of vision analysis from vision_agent.
            Live API tool schemas reject free-form dict parameters (`additionalProperties`),
            so the public schema is string-based. Direct Python callers may still pass a dict.
        pricing_table: Optional JSON string pricing override. Direct dicts are also accepted
            for backward compatibility in tests/non-ADK calls.
        questionnaire_answers: Optional JSON string of raw customer answers keyed by question ID.
        retail_price: Optional retail price (NGN) for percentage-based pricing when device
            is not in the hardcoded pricing table.

    Returns:
        Combined grade + valuation result, with optional adjustments.
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

    # Apply questionnaire adjustments if provided
    parsed_answers: dict[str, Any] | None = None
    if isinstance(questionnaire_answers, str) and questionnaire_answers.strip():
        try:
            parsed = json.loads(questionnaire_answers)
            if isinstance(parsed, dict):
                parsed_answers = parsed
        except json.JSONDecodeError:
            logger.debug("Invalid questionnaire_answers JSON; falling back to vision-only grade")

    if parsed_answers is not None:
        brand = safe_analysis.get("brand", "Unknown")
        questions = get_device_questionnaire(brand)
        normalized = normalize_questionnaire_answers(parsed_answers, questions)
        adjustment = adjust_grade_from_questionnaire(grade_result["grade"], normalized)
        grade_result["grade"] = adjustment["final_grade"]
        grade_result["original_vision_grade"] = adjustment["original_vision_grade"]
        grade_result["adjustments"] = adjustment["adjustments"]
        grade_result["adjustment_count"] = adjustment["adjustment_count"]

    value_result = calculate_trade_in_value(
        device_name=grade_result["device_name"],
        grade=grade_result["grade"],
        pricing_table=safe_pricing_table,
        retail_price=retail_price,
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


def get_device_questionnaire_tool(
    device_brand: str = "",
    analysis: str | dict[str, Any] | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """ADK tool: Get brand-specific diagnostic questions for trade-in evaluation.

    Args:
        device_brand: Device brand (e.g. "Apple", "Samsung").
        analysis: Optional JSON string or dict of latest tool-backed vision analysis.
        tool_context: Optional ADK tool context for session-backed analysis fallback.

    Returns:
        Dict with 'questions' key containing list of question objects.
    """
    resolved_analysis: dict[str, Any] | None = None
    if isinstance(analysis, dict):
        resolved_analysis = analysis
    elif isinstance(analysis, str) and analysis.strip():
        try:
            parsed = json.loads(analysis)
        except json.JSONDecodeError:
            logger.debug("Invalid questionnaire analysis JSON; ignoring")
        else:
            if isinstance(parsed, dict):
                resolved_analysis = parsed

    if resolved_analysis is None and tool_context is not None:
        state_analysis = tool_context.state.get("temp:last_analysis")
        if isinstance(state_analysis, dict):
            resolved_analysis = state_analysis

    omitted_question_ids = _question_ids_omitted_from_analysis(resolved_analysis)
    questions = get_device_questionnaire(device_brand or "", analysis=resolved_analysis)
    result: dict[str, Any] = {"questions": questions}
    if omitted_question_ids:
        result["omitted_question_ids"] = omitted_question_ids
    return result
