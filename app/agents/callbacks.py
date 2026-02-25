"""Shared ADK callbacks for model/tool lifecycle and structured events."""

from __future__ import annotations

import logging
import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions.state import State
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)

_PRICE_PATTERN = re.compile(r"\b\d[\d,]{2,}\b")


def _next_server_message_id(state: State) -> int:
    """Return monotonically increasing ID for websocket server messages."""
    raw = state.get("temp:server_message_seq", 0)
    try:
        current = int(raw)
    except (TypeError, ValueError):
        current = 0
    return current + 1


def queue_server_message(state: State, payload: dict[str, Any]) -> None:
    """Queue one structured server message in state delta for downstream emit."""
    message_id = _next_server_message_id(state)
    message = dict(payload)
    message["id"] = message_id
    state["temp:server_message_seq"] = message_id
    state["temp:last_server_message"] = message


def _response_text(llm_response: LlmResponse) -> str:
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return ""
    chunks = [part.text for part in parts if getattr(part, "text", None)]
    return " ".join(chunks).strip()


def _industry_instruction(industry_config: dict[str, Any], *, include_greeting: bool = True) -> str:
    name = industry_config.get("name", "General")
    line = f"Runtime config: industry='{name}'."
    if include_greeting:
        greeting = industry_config.get("greeting", "")
        if greeting:
            line += f" Preferred greeting='{greeting}'."
    return line


def _company_instruction(
    company_id: str,
    company_profile: dict[str, Any],
    company_knowledge: list[dict[str, Any]],
) -> str:
    if not company_profile:
        return ""

    company_name = str(company_profile.get("name", "")).strip() or "Company"
    overview = str(company_profile.get("overview", "")).strip()

    fact_pairs: list[str] = []
    facts = company_profile.get("facts")
    if isinstance(facts, dict):
        for key, value in facts.items():
            key_text = str(key).strip()
            value_text = str(value).strip()
            if not key_text or not value_text:
                continue
            fact_pairs.append(f"{key_text}={value_text}")
            if len(fact_pairs) >= 6:
                break

    knowledge_topics: list[str] = []
    for item in company_knowledge:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        text = str(item.get("text", "")).strip()
        excerpt = text[:100]
        if excerpt:
            knowledge_topics.append(f"{title}: {excerpt}")
        else:
            knowledge_topics.append(title)
        if len(knowledge_topics) >= 3:
            break

    parts = [
        (
            "Company context: "
            f"id='{company_id or 'default'}', name='{company_name}'."
        )
    ]
    if overview:
        parts.append(f"Overview='{overview[:320]}'.")
    if fact_pairs:
        parts.append("Facts: " + "; ".join(fact_pairs) + ".")
    if knowledge_topics:
        parts.append("Knowledge topics: " + "; ".join(knowledge_topics) + ".")
    return " ".join(parts)
async def before_model_inject_config(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Inject runtime industry + company context into system instruction.

    The greeting is only injected on the first model turn (before
    ``temp:greeted`` is set) to prevent the model from re-greeting
    every turn.
    """
    instruction_lines: list[str] = []

    already_greeted = bool(callback_context.state.get("temp:greeted", False))

    industry_config = callback_context.state.get("app:industry_config")
    if isinstance(industry_config, dict):
        instruction_lines.append(
            _industry_instruction(industry_config, include_greeting=not already_greeted)
        )

    if already_greeted:
        instruction_lines.append(
            "Conversation continuity: A greeting has already been delivered in this "
            "session. Do NOT greet again (no hello/hi/good morning). Continue "
            "directly with the answer or next question."
        )

    company_id_raw = callback_context.state.get("app:company_id")
    company_id = company_id_raw if isinstance(company_id_raw, str) else "default"

    company_profile = callback_context.state.get("app:company_profile")
    if not isinstance(company_profile, dict):
        company_profile = {}

    company_knowledge_raw = callback_context.state.get("app:company_knowledge")
    company_knowledge: list[dict[str, Any]] = []
    if isinstance(company_knowledge_raw, list):
        company_knowledge = [
            item for item in company_knowledge_raw if isinstance(item, dict)
        ]

    company_line = _company_instruction(company_id, company_profile, company_knowledge)
    if company_line:
        instruction_lines.append(company_line)

    if not instruction_lines:
        return None

    instruction_line = "\n".join(instruction_lines)
    if llm_request.config is None:
        llm_request.config = types.GenerateContentConfig(
            system_instruction=instruction_line
        )
        return None

    existing = llm_request.config.system_instruction
    if existing is None:
        llm_request.config.system_instruction = instruction_line
    elif isinstance(existing, str) and instruction_line not in existing:
        llm_request.config.system_instruction = (
            f"{existing}\n\n{instruction_line}"
        )
    return None


async def after_model_valuation_sanity(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> None:
    """Soft checks for valuation responses to reduce pricing drift."""
    # Lock greeting only after the first real model response, so agent transfers
    # before speaking do not accidentally suppress the initial greeting.
    text = _response_text(llm_response)
    if text and not bool(callback_context.state.get("temp:greeted", False)):
        callback_context.state["temp:greeted"] = True

    if callback_context.agent_name != "valuation_agent":
        return None

    offer_amount = callback_context.state.get("temp:last_offer_amount")
    if not isinstance(offer_amount, (int, float)) or offer_amount <= 0:
        return None

    if not text:
        return None

    if "₦" not in text and "NGN" not in text.upper():
        logger.warning(
            "valuation_agent response missing NGN marker (offer=%s)",
            offer_amount,
        )

    parsed_values: list[int] = []
    for token in _PRICE_PATTERN.findall(text):
        parsed = token.replace(",", "")
        if parsed.isdigit():
            parsed_values.append(int(parsed))
    if any(value > int(offer_amount) * 2 for value in parsed_values):
        logger.warning(
            "valuation_agent response contains unusually high number(s): %s",
            parsed_values,
        )
    return None


async def before_tool_log(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> None:
    """Structured log before each tool invocation."""
    redacted_args = dict(args)
    if "image_base64" in redacted_args:
        redacted_args["image_base64"] = "<redacted>"
    logger.info(
        "tool_start agent=%s tool=%s args=%s",
        tool_context.agent_name,
        tool.name,
        sorted(redacted_args.keys()),
    )
    return None


def _format_product_description(product: dict[str, Any]) -> str:
    features = product.get("features")
    if isinstance(features, list) and features:
        return ", ".join(str(item) for item in features[:3])
    description = product.get("description")
    if isinstance(description, str):
        return description
    return ""


async def after_tool_emit_messages(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    result: dict[str, Any] | None = None,
    *,
    tool_response: dict[str, Any] | None = None,
    **_: Any,
) -> None:
    """Emit structured server messages after successful tool calls."""
    effective_result = tool_response if isinstance(tool_response, dict) else result
    logger.info(
        "tool_end agent=%s tool=%s success=%s",
        tool_context.agent_name,
        tool.name,
        isinstance(effective_result, dict) and not effective_result.get("error"),
    )

    if not isinstance(effective_result, dict):
        return None

    if effective_result.get("error"):
        queue_server_message(
            tool_context.state,
            {
                "type": "error",
                "code": "TOOL_ERROR",
                "message": str(effective_result["error"]),
            },
        )
        return None

    if tool.name == "analyze_device_image_tool":
        tool_context.state["temp:last_analysis"] = {
            "device_name": effective_result.get("device_name", "Unknown"),
            "condition": effective_result.get("condition", "Unknown"),
            "details": effective_result.get("details", {}),
        }
        message: dict[str, Any] = {
            "type": "image_received",
            "status": "complete",
        }
        gcs_uri = effective_result.get("gcs_uri")
        if isinstance(gcs_uri, str) and gcs_uri:
            message["previewUrl"] = gcs_uri
        queue_server_message(tool_context.state, message)
        return None

    if tool.name == "grade_and_value_tool":
        offer_amount = int(effective_result.get("offer_amount", 0))
        tool_context.state["temp:last_offer_amount"] = offer_amount

        queue_server_message(
            tool_context.state,
            {
                "type": "valuation_result",
                "deviceName": effective_result.get("device_name", "Unknown"),
                "condition": effective_result.get("grade", "Fair"),
                "price": offer_amount,
                "currency": effective_result.get("currency", "NGN"),
                "details": effective_result.get("summary", ""),
                "negotiable": offer_amount > 0,
            },
        )
        return None

    if tool.name == "create_booking":
        queue_server_message(
            tool_context.state,
            {
                "type": "booking_confirmation",
                "confirmationId": effective_result.get("confirmation_id", ""),
                "date": effective_result.get("date", ""),
                "time": effective_result.get("time", ""),
                "location": effective_result.get("location", ""),
                "service": effective_result.get("service_type", ""),
            },
        )
        return None

    if tool.name == "search_catalog":
        products_raw = effective_result.get("products")
        if not isinstance(products_raw, list):
            return None
        products: list[dict[str, Any]] = []
        for item in products_raw[:3]:
            if not isinstance(item, dict):
                continue
            products.append(
                {
                    "name": item.get("name", "Unknown"),
                    "price": int(item.get("price", 0) or 0),
                    "currency": item.get("currency", "NGN"),
                    "available": bool(item.get("in_stock", False)),
                    "description": _format_product_description(item),
                }
            )
        queue_server_message(
            tool_context.state,
            {
                "type": "product_recommendation",
                "products": products,
            },
        )
        return None

    return None


async def on_tool_error_emit(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    exception: Exception | None = None,
    *,
    error: Exception | None = None,
    **_: Any,
) -> None:
    """Emit standard error ServerMessage when a tool throws."""
    effective_exception = error or exception or Exception("Unknown tool error")
    logger.error(
        "tool_exception agent=%s tool=%s error=%s",
        tool_context.agent_name,
        tool.name,
        effective_exception,
    )
    queue_server_message(
        tool_context.state,
        {
            "type": "error",
            "code": "TOOL_EXCEPTION",
            "message": f"{tool.name} failed. Please try again.",
        },
    )
    return None
