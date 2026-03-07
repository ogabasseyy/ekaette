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

from app.agents.dedup import dedup_before_agent
from app.configs.agent_policy import (
    KNOWN_SUB_AGENT_NAMES,
    resolve_enabled_agents_from_state,
)
from app.tools.global_lessons import format_lessons_for_instruction

logger = logging.getLogger(__name__)

_PRICE_PATTERN = re.compile(r"\b\d[\d,]{2,}\b")

# ═══ Capability Guard ═══

TOOL_CAPABILITY_MAP: dict[str, str] = {
    "create_booking": "booking_reservations",
    "cancel_booking": "booking_reservations",
    "check_availability": "booking_reservations",
    "search_catalog": "catalog_lookup",
    "get_product_details": "catalog_lookup",
    "analyze_device_image_tool": "valuation_tradein",
    "grade_and_value_tool": "valuation_tradein",
    "grade_condition": "valuation_tradein",
    "calculate_trade_in_value": "valuation_tradein",
    "search_company_knowledge": "policy_qa",
    "get_company_profile_fact": "policy_qa",
    "query_company_system": "connector_dispatch",
    "send_whatsapp_message": "outbound_messaging",
    "get_device_questionnaire_tool": "valuation_tradein",
}

AGENT_NOT_ENABLED_ERROR_CODE = "AGENT_NOT_ENABLED"


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


def _state_get(state: Any, key: str, default: Any = None) -> Any:
    getter = getattr(state, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    return default


def _industry_scope_label(state: Any) -> str:
    template_id = _state_get(state, "app:industry_template_id")
    if isinstance(template_id, str) and template_id.strip():
        return template_id.strip()
    industry = _state_get(state, "app:industry")
    if isinstance(industry, str) and industry.strip():
        return industry.strip()
    return "current"


def _agent_not_enabled_message(scope_label: str, agent_name: str) -> str:
    return (
        f"This {scope_label} session is isolated and cannot switch to '{agent_name}'. "
        "I can only use the agents enabled for the current industry."
    )


def _agent_not_enabled_payload(
    *,
    state: Any,
    agent_name: str,
    allowed_agents: list[str],
) -> dict[str, Any]:
    scope_label = _industry_scope_label(state)
    payload: dict[str, Any] = {
        "type": "error",
        "code": AGENT_NOT_ENABLED_ERROR_CODE,
        "message": _agent_not_enabled_message(scope_label, agent_name),
        "agentName": agent_name,
        "allowedAgents": list(allowed_agents),
    }
    tenant = _state_get(state, "app:tenant_id")
    if isinstance(tenant, str) and tenant:
        payload["tenantId"] = tenant
    template_id = _state_get(state, "app:industry_template_id")
    if isinstance(template_id, str) and template_id:
        payload["industryTemplateId"] = template_id
    return payload


def _agent_not_enabled_content(scope_label: str, agent_name: str) -> types.Content:
    return types.Content(
        role="model",
        parts=[types.Part(text=_agent_not_enabled_message(scope_label, agent_name))],
    )


def _requested_transfer_agent_name(args: dict[str, Any]) -> str | None:
    raw = args.get("agent_name", args.get("agentName"))
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _is_transfer_tool_name(tool_name: str) -> bool:
    return tool_name == "transfer_to_agent" or tool_name.startswith("transfer_to_")


def _tool_transfer_target_agent_name(tool_name: str, args: dict[str, Any]) -> str | None:
    if not _is_transfer_tool_name(tool_name):
        return None
    requested = _requested_transfer_agent_name(args)
    if requested:
        return requested
    if tool_name.startswith("transfer_to_"):
        candidate = tool_name.removeprefix("transfer_to_").strip()
        if candidate in KNOWN_SUB_AGENT_NAMES:
            return candidate
    return None


async def before_agent_isolation_guard(
    callback_context: CallbackContext,
) -> types.Content | None:
    """Block sub-agent invocations not enabled for the current industry session."""
    agent_name = callback_context.agent_name
    if agent_name == "ekaette_router":
        return None

    state = callback_context.state
    if state is None or not hasattr(state, "get"):
        return None

    enabled_agents = resolve_enabled_agents_from_state(state)
    if enabled_agents is None or agent_name in enabled_agents:
        return None

    scope_label = _industry_scope_label(state)
    logger.warning(
        "agent_isolation_blocked phase=before_agent agent=%s industry=%s enabled_agents=%s",
        agent_name,
        scope_label,
        enabled_agents,
    )
    queue_server_message(
        state,
        _agent_not_enabled_payload(
            state=state,
            agent_name=agent_name,
            allowed_agents=enabled_agents,
        ),
    )
    return _agent_not_enabled_content(scope_label, agent_name)


async def before_agent_isolation_guard_and_dedup(
    callback_context: CallbackContext,
) -> types.Content | None:
    """Compose isolation guard and dedup mitigation for router sub-agent transfers."""
    blocked = await before_agent_isolation_guard(callback_context)
    if blocked is not None:
        return blocked
    return await dedup_before_agent(callback_context)


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


def _first_turn_greeting_instruction(
    *,
    industry_config: dict[str, Any],
    company_profile: dict[str, Any],
    state: State,
) -> str:
    """Build strict first-turn greeting guidance with company personalization."""
    company_name_raw = company_profile.get("name") if isinstance(company_profile, dict) else ""
    company_name = str(company_name_raw).strip() if isinstance(company_name_raw, str) else ""
    if not company_name:
        company_name = "our service desk"

    greeting_raw = industry_config.get("greeting") if isinstance(industry_config, dict) else ""
    greeting = str(greeting_raw).strip() if isinstance(greeting_raw, str) else ""
    if not greeting:
        greeting = "How can I help you today?"

    customer_name = ""
    for key in ("user:name", "user:first_name", "app:customer_name", "temp:customer_name"):
        value = state.get(key)
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.split()).strip()
        if normalized:
            customer_name = normalized[:60]
            break

    if customer_name:
        template = f"Welcome back, {customer_name}, to {company_name}. {greeting}"
    else:
        template = f"Welcome to {company_name}. {greeting}"

    return (
        "First-turn greeting policy: This is the first spoken response in the session. "
        f"Use this greeting template intent: '{template}'. "
        "Keep it short and end with exactly one actionable question."
    )


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
    parts.append(
        "Trust policy: For company-specific claims, ground responses in company facts, "
        "knowledge topics, or system query results. If data is unavailable, say so clearly."
    )
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

    company_profile = callback_context.state.get("app:company_profile")
    if not isinstance(company_profile, dict):
        company_profile = {}

    industry_config = callback_context.state.get("app:industry_config")
    if isinstance(industry_config, dict):
        instruction_lines.append(_industry_instruction(industry_config, include_greeting=False))
        if not already_greeted:
            instruction_lines.append(
                _first_turn_greeting_instruction(
                    industry_config=industry_config,
                    company_profile=company_profile,
                    state=callback_context.state,
                )
            )

    has_runtime_context = isinstance(industry_config, dict)

    if already_greeted:
        instruction_lines.append(
            "Conversation continuity: A greeting has already been delivered in this "
            "session. Do NOT greet again (no hello/hi/good morning). Continue "
            "directly with the answer or next question."
        )
        instruction_lines.append(
            "Style guard: Do not re-introduce your role (for example, never say "
            "'I am the support agent'). Keep responses task-focused."
        )
        has_runtime_context = True

    company_id_raw = callback_context.state.get("app:company_id")
    company_id = company_id_raw if isinstance(company_id_raw, str) else "default"

    company_knowledge_raw = callback_context.state.get("app:company_knowledge")
    company_knowledge: list[dict[str, Any]] = []
    if isinstance(company_knowledge_raw, list):
        company_knowledge = [
            item for item in company_knowledge_raw if isinstance(item, dict)
        ]

    company_line = _company_instruction(company_id, company_profile, company_knowledge)
    if company_line:
        instruction_lines.append(company_line)
        has_runtime_context = True

    # Inject global lessons (Tier 2 learning — cross-session behavioral rules)
    global_lessons = callback_context.state.get("app:global_lessons")
    if isinstance(global_lessons, list) and global_lessons:
        lessons_text = format_lessons_for_instruction(
            global_lessons, agent_name=callback_context.agent_name,
        )
        if lessons_text:
            instruction_lines.append(lessons_text)
            has_runtime_context = True

    if has_runtime_context:
        channel = _state_get(callback_context.state, "app:channel")
        if channel == "voice":
            instruction_lines.append(
                "CRITICAL latency policy: On a phone call, silence feels like a "
                "dropped connection. You MUST speak a brief filler phrase (e.g., "
                "'Let me check that for you') BEFORE any tool call or agent "
                "transfer. Generate spoken text FIRST, then the tool call, in "
                "the same turn. Never leave more than 2 seconds of silence."
            )

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


async def before_tool_capability_guard(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    """Block tool calls when the session lacks the required capability.

    Returns None to allow the call, or a dict error to short-circuit.
    Tools not in TOOL_CAPABILITY_MAP are always allowed.
    When ``app:capabilities`` is absent from state, all tools are allowed
    (backward-compatible / compat mode).
    """
    required_cap = TOOL_CAPABILITY_MAP.get(tool.name)
    if required_cap is None:
        return None

    capabilities = tool_context.state.get("app:capabilities")
    if not isinstance(capabilities, list):
        return None  # Compat mode — no guard

    if required_cap in capabilities:
        return None

    logger.warning(
        "capability_blocked agent=%s tool=%s required=%s capabilities=%s",
        tool_context.agent_name,
        tool.name,
        required_cap,
        capabilities,
    )
    return {
        "error": "capability_not_enabled",
        "tool": tool.name,
        "required": required_cap,
    }


async def before_tool_agent_transfer_guard(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    """Block transfer-to-agent tool calls that violate session isolation policy."""
    tool_name = str(getattr(tool, "name", "") or "")
    target_agent = _tool_transfer_target_agent_name(tool_name, args)
    if target_agent is None:
        return None

    enabled_agents = resolve_enabled_agents_from_state(tool_context.state)
    if enabled_agents is None or target_agent in enabled_agents:
        return None

    logger.warning(
        "agent_isolation_blocked phase=before_tool caller=%s tool=%s target_agent=%s enabled_agents=%s",
        tool_context.agent_name,
        tool_name,
        target_agent,
        enabled_agents,
    )
    payload = _agent_not_enabled_payload(
        state=tool_context.state,
        agent_name=target_agent,
        allowed_agents=enabled_agents,
    )
    payload["error"] = "agent_not_enabled"
    payload["tool"] = tool_name
    return payload


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


async def before_tool_capability_guard_and_log(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    """Enforce capability guards, then emit structured tool-start logs.

    Returning a dict short-circuits the tool call in ADK. Logging is skipped when
    the tool is blocked so denied operations do not emit a misleading tool_start.
    """
    blocked = await before_tool_agent_transfer_guard(tool, args, tool_context)
    if blocked is not None:
        return blocked
    blocked = await before_tool_capability_guard(tool, args, tool_context)
    if blocked is not None:
        return blocked
    await before_tool_log(tool, args, tool_context)
    return None


def _tool_error_server_message(effective_result: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool callback error payloads into structured server messages."""
    code_raw = effective_result.get("code")
    code = str(code_raw).strip() if isinstance(code_raw, str) and code_raw.strip() else ""
    error_raw = effective_result.get("error")
    message_raw = effective_result.get("message")

    if code:
        message = (
            str(message_raw).strip()
            if isinstance(message_raw, str) and str(message_raw).strip()
            else str(error_raw or "Tool error")
        )
        payload: dict[str, Any] = {
            "type": "error",
            "code": code,
            "message": message,
        }
        for key in (
            "agentName",
            "allowedAgents",
            "tenantId",
            "industryTemplateId",
            "tool",
            "required",
        ):
            if key in effective_result:
                payload[key] = effective_result[key]
        return payload

    if error_raw == "capability_not_enabled":
        payload = {
            "type": "error",
            "code": "CAPABILITY_NOT_ENABLED",
            "message": "This action is not enabled for the current session.",
        }
        for key in ("tool", "required"):
            if key in effective_result:
                payload[key] = effective_result[key]
        return payload

    return {
        "type": "error",
        "code": "TOOL_ERROR",
        "message": str(error_raw),
    }


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
        queue_server_message(tool_context.state, _tool_error_server_message(effective_result))
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

    if tool.name == "get_device_questionnaire_tool":
        questions = effective_result.get("questions", [])
        queue_server_message(
            tool_context.state,
            {
                "type": "questionnaire_started",
                "questionCount": len(questions) if isinstance(questions, list) else 0,
            },
        )
        return None

    if tool.name == "grade_and_value_tool":
        offer_amount = int(effective_result.get("offer_amount") or 0)
        tool_context.state["temp:last_offer_amount"] = offer_amount

        message: dict[str, Any] = {
            "type": "valuation_result",
            "deviceName": effective_result.get("device_name", "Unknown"),
            "condition": effective_result.get("grade", "Fair"),
            "price": offer_amount,
            "currency": effective_result.get("currency", "NGN"),
            "details": effective_result.get("summary", ""),
            "negotiable": offer_amount > 0,
        }
        # Include adjustment info when questionnaire was used
        if "original_vision_grade" in effective_result:
            message["originalGrade"] = effective_result["original_vision_grade"]
        if "adjustments" in effective_result:
            message["adjustments"] = effective_result["adjustments"]

        queue_server_message(tool_context.state, message)
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
            raw_price = item.get("price", 0)
            try:
                price_value = int(raw_price) if isinstance(raw_price, (int, float)) else 0
            except (TypeError, ValueError):
                price_value = 0
            products.append(
                {
                    "name": item.get("name", "Unknown"),
                    "price": str(raw_price) if isinstance(raw_price, str) else price_value,
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
