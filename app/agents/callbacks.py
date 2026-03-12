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

from app.api.v1.at import service_voice
from app.agents.dedup import dedup_before_agent
from app.configs.agent_policy import (
    KNOWN_SUB_AGENT_NAMES,
    resolve_enabled_agents_from_state,
)
from app.tools.sms_messaging import resolve_caller_phone_from_context
from app.tools.global_lessons import format_lessons_for_instruction

logger = logging.getLogger(__name__)

_PRICE_PATTERN = re.compile(r"\b\d[\d,]{2,}\b")
_STORAGE_PATTERN = re.compile(r"\b\d+(?:gb|tb)\b", flags=re.IGNORECASE)
_CALLBACK_REQUEST_PATTERNS = (
    re.compile(r"\bcall(?:ing)?\s+(?:me\s+)?back\b", re.IGNORECASE),
    re.compile(r"\bcallback\b", re.IGNORECASE),
    re.compile(r"\b(?:can|could|would|will)\s+you\s+call\s+me(?:\s+\w+)?\b", re.IGNORECASE),
    re.compile(r"\bplease\s+call\s+me(?:\s+\w+)?\b", re.IGNORECASE),
    re.compile(r"\byou\s+call\s+me(?:\s+\w+)?\b", re.IGNORECASE),
    re.compile(r"\blow(?:\s+on)?\s+airtime\b", re.IGNORECASE),
    re.compile(r"\b(?:no|not enough)\s+airtime\b", re.IGNORECASE),
    re.compile(r"\bdon(?:'|’)t\s+have\s+(?:enough\s+)?airtime\b", re.IGNORECASE),
    re.compile(r"\bdon(?:'|’)t\s+have\s+(?:the\s+|a\s+)?time\b", re.IGNORECASE),
)
_CALLBACK_PROMISE_PATTERNS = (
    re.compile(r"\bi(?:'| wi)?ll call you back\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll call back\b", re.IGNORECASE),
    re.compile(r"\bi can call you back\b", re.IGNORECASE),
    re.compile(r"\blet me call you back\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll make sure to call you back\b", re.IGNORECASE),
    re.compile(r"\bi can (?:certainly )?arrange a callback\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll (?:arrange|schedule) a callback\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll request a callback\b", re.IGNORECASE),
    re.compile(r"\bwe(?:'| wi)?ll give you a call back\b", re.IGNORECASE),
    re.compile(r"\bcall you back shortly\b", re.IGNORECASE),
    re.compile(r"\bcall you back on this same number\b", re.IGNORECASE),
    re.compile(r"\brequest a callback for you right after this\b", re.IGNORECASE),
    re.compile(r"\bwhen i call back\b", re.IGNORECASE),
)

# Tools that require caller phone identity for outbound actions.
_OUTBOUND_CALLER_TOOLS = frozenset({
    "request_callback",
    "send_sms_message",
    "send_whatsapp_message",
})


def looks_like_callback_request(text: str) -> bool:
    """Return True when customer text sounds like a callback request."""
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _CALLBACK_REQUEST_PATTERNS)


def looks_like_callback_promise(text: str) -> bool:
    """Return True when the agent's output text contains a callback promise."""
    normalized = text.strip()
    if not normalized:
        return False
    return any(p.search(normalized) for p in _CALLBACK_PROMISE_PATTERNS)


def _is_callback_leg(state: State) -> bool:
    """Return True when the current session is an outbound callback leg.

    Callback sessions are created by the SIP bridge with session IDs
    prefixed ``sip-callback-``.  On a callback leg the agent must NOT
    call ``request_callback`` again (that would create an infinite loop).
    """
    session_id = _state_get(state, "app:session_id", "")
    if isinstance(session_id, str) and session_id.strip().startswith("sip-callback-"):
        return True
    return False


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
    "send_sms_message": "outbound_messaging",
    "request_callback": "outbound_messaging",
    "get_device_questionnaire_tool": "valuation_tradein",
    "request_media_via_whatsapp": "valuation_tradein",
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


def _queue_end_after_speaking_control(state: State, *, reason: str) -> None:
    """Ask the telephony bridge to end the call once the current agent turn drains."""
    if bool(_state_get(state, "temp:call_end_after_speaking_requested", False)):
        return
    state["temp:call_end_after_speaking_requested"] = True
    queue_server_message(
        state,
        {
            "type": "call_control",
            "action": "end_after_speaking",
            "reason": reason,
        },
    )


def _state_get(state: Any, key: str, default: Any = None) -> Any:
    getter = getattr(state, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    return default


def _maybe_inject_caller_phone(tool_context: Any) -> None:
    """Inject caller phone into tool state from the ephemeral registry.

    ADK's live-streaming mode sometimes does not surface ``user:caller_phone``
    in the tool context's session state.  This bridging logic resolves it from
    the per-process ephemeral registry (populated at session init time) and
    writes it into the state so that downstream tool code can find it.
    """
    state = getattr(tool_context, "state", None)
    if state is None:
        return
    existing = _state_get(state, "user:caller_phone", "")
    if isinstance(existing, str) and existing.strip():
        return  # already present — nothing to do

    from app.api.v1.realtime.caller_phone_registry import get_registered_caller_phone

    user_id = str(_state_get(state, "app:user_id", "") or "").strip()
    session_id = str(_state_get(state, "app:session_id", "") or "").strip()
    if not user_id:
        user_id = str(getattr(tool_context, "user_id", "") or "").strip()
    if not session_id:
        session_id = str(getattr(getattr(tool_context, "session", None), "id", "") or "").strip()
    if (not user_id or not session_id) and getattr(getattr(tool_context, "session", None), "state", None) is not None:
        session_state = getattr(tool_context.session, "state", None)
        user_id = user_id or str(_state_get(session_state, "app:user_id", "") or "").strip()
        session_id = session_id or str(_state_get(session_state, "app:session_id", "") or "").strip()
    if not user_id:
        return
    phone = get_registered_caller_phone(user_id=user_id, session_id=session_id)
    if phone:
        try:
            state["user:caller_phone"] = phone
        except Exception:
            pass
        logger.info(
            "Injected caller phone from registry user_id=%s session_id=%s",
            user_id,
            session_id,
        )


def _industry_scope_label(state: Any) -> str:
    template_id = _state_get(state, "app:industry_template_id")
    if isinstance(template_id, str) and template_id.strip():
        return template_id.strip()
    industry = _state_get(state, "app:industry")
    if isinstance(industry, str) and industry.strip():
        return industry.strip()
    return "current"


def _response_commits_to_callback(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _CALLBACK_PROMISE_PATTERNS)


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


def _response_has_content(llm_response: LlmResponse) -> bool:
    """Return True if the response has any meaningful content (text or audio).

    In native-audio Live API mode, the model generates inline_data audio
    parts instead of text parts. This helper detects both, so callers can
    reliably determine whether the model actually spoke.
    """
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return False
    for part in parts:
        if getattr(part, "text", None):
            return True
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            return True
    return False


def _industry_instruction(industry_config: dict[str, Any], *, include_greeting: bool = True) -> str:
    name = industry_config.get("name", "General")
    line = f"Runtime config: industry='{name}'."
    if include_greeting:
        greeting = industry_config.get("greeting", "")
        if greeting:
            line += f" Preferred greeting='{greeting}'."
    return line


def _first_turn_opening(company_name: str, customer_name: str) -> str:
    """Return the exact opening sentence to lock first-turn identity."""
    if customer_name:
        return f"Welcome back, {customer_name}. This is ehkaitay from {company_name}."
    return f"Hello, this is ehkaitay from {company_name}."


def _resolve_company_names(company_profile: dict[str, Any]) -> tuple[str, str]:
    """Return ``(display_name, spoken_name)`` for company identity."""
    display_name_raw = company_profile.get("display_name") if isinstance(company_profile, dict) else ""
    display_name = str(display_name_raw).strip() if isinstance(display_name_raw, str) else ""
    spoken_name_raw = company_profile.get("spoken_name") if isinstance(company_profile, dict) else ""
    spoken_name = str(spoken_name_raw).strip() if isinstance(spoken_name_raw, str) else ""
    legacy_name_raw = company_profile.get("name") if isinstance(company_profile, dict) else ""
    legacy_name = str(legacy_name_raw).strip() if isinstance(legacy_name_raw, str) else ""

    if not display_name:
        display_name = legacy_name or "our service desk"
    if not spoken_name:
        spoken_name = legacy_name or display_name
    return display_name, spoken_name


def _first_turn_greeting_instruction(
    *,
    company_profile: dict[str, Any],
    state: State,
) -> str:
    """Build strict first-turn greeting guidance with company personalization."""
    _display_name, spoken_name = _resolve_company_names(company_profile)

    customer_name = ""
    for key in ("user:name", "user:first_name", "app:customer_name", "temp:customer_name"):
        value = state.get(key)
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.split()).strip()
        if normalized:
            customer_name = normalized[:60]
            break

    opening = _first_turn_opening(spoken_name, customer_name)
    question = "How can I help you today?"

    return (
        "First-turn greeting policy: This is the first spoken response in the session. "
        "Identity lock: Your assistant name is exactly 'ehkaitay'. "
        f"The spoken business name for this session is exactly '{spoken_name}'. "
        "Never substitute, paraphrase, or invent another assistant or company name. "
        "Never use the business name as your personal name. "
        f"Say this opening sentence exactly: '{opening}' "
        "Do not begin with phrases like 'welcome to <company>' and do not make "
        "the company sound like the speaker. "
        f"Immediately follow with exactly one short actionable question: '{question}' "
        "and nothing before the opening sentence."
    )


def _company_instruction(
    company_id: str,
    company_profile: dict[str, Any],
    company_knowledge: list[dict[str, Any]],
    *,
    channel: str = "",
) -> str:
    if not company_profile:
        return ""

    display_name, spoken_name = _resolve_company_names(company_profile)
    company_name = spoken_name if channel == "voice" else display_name
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
            f"name='{company_name}'. "
            "Use this exact company name in customer-facing replies when needed. "
            "Do not invent alternate business or brand names. "
            f"If the customer asks what company you work for, who you work for, or the business name, answer with the exact company name '{company_name}'. "
            "Do not replace it with generic phrases like 'our company' or 'the business'. "
            "Never mention internal company IDs, slugs, tenant labels, or system identifiers."
        )
    ]
    if channel == "voice" and display_name != spoken_name:
        parts.append(
            f"Display vs pronunciation: The public-facing company display name is '{display_name}', "
            f"but when speaking aloud on voice calls, pronounce it as '{spoken_name}'."
        )
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


def _handoff_instruction(state: State, agent_name: str) -> str:
    """Return explicit continuity guidance for the first turn after a transfer."""
    target_agent = _state_get(state, "temp:pending_handoff_target_agent", "")
    normalized_agent = agent_name.strip() if isinstance(agent_name, str) else ""
    normalized_target = target_agent.strip() if isinstance(target_agent, str) else ""
    if not normalized_agent or not normalized_target or normalized_target != normalized_agent:
        return ""

    latest_user_raw = _state_get(state, "temp:pending_handoff_latest_user", "")
    latest_agent_raw = _state_get(state, "temp:pending_handoff_latest_agent", "")
    recent_customer_raw = _state_get(state, "temp:pending_handoff_recent_customer_context", "")

    latest_user = latest_user_raw.strip() if isinstance(latest_user_raw, str) else ""
    latest_agent = latest_agent_raw.strip() if isinstance(latest_agent_raw, str) else ""
    recent_customer = (
        recent_customer_raw.strip() if isinstance(recent_customer_raw, str) else ""
    )

    parts = [
        "LIVE HANDOFF — STRICT CONTINUITY RULES: "
        "This is the first response immediately after an internal transfer "
        f"to '{normalized_agent}'. "
        "You MUST NOT: greet, say hello, introduce yourself, say your name, "
        "say 'how can I help you', or repeat anything the previous agent said. "
        "You MUST: continue the same conversation seamlessly as if you are the "
        "same person. The customer should not notice the transfer at all.",
    ]
    if latest_user:
        parts.append(
            f"The customer's latest request before the transfer was: '{latest_user}'."
        )
    if latest_agent:
        parts.append(
            f"The previous agent's latest spoken line was: '{latest_agent}'. "
            "Acknowledge and advance from there without paraphrasing it back."
        )
    if recent_customer:
        parts.append(f"Recent customer-only context: '{recent_customer}'.")
    return " ".join(parts)


def _outbound_delivery_instruction(state: State) -> str:
    """Tell the model the latest written delivery/send outcome."""
    raw_status = _state_get(state, "temp:last_outbound_delivery_status", "")
    status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
    if not status:
        return ""

    raw_channels = _state_get(state, "temp:last_outbound_delivery_channels", "")
    channels = raw_channels.strip() if isinstance(raw_channels, str) else ""
    raw_phone = _state_get(state, "temp:last_outbound_delivery_phone", "")
    phone = raw_phone.strip() if isinstance(raw_phone, str) else ""

    if status == "success":
        return (
            "Outbound delivery status: Written details were already sent successfully"
            f"{' via ' + channels if channels else ''}"
            f"{' to ' + phone if phone else ' to the caller'}. "
            "If the customer asks, confirm that they were sent. "
            "Do not claim there was a sending problem unless a later tool result fails."
        )

    if status == "partial":
        return (
            "Outbound delivery status: A written follow-up only partially succeeded"
            f"{' via ' + channels if channels else ''}. "
            "Be explicit about which channel worked, and offer the other channel as a fallback."
        )

    if status == "failure":
        return (
            "Outbound delivery status: The latest written follow-up attempt failed. "
            "Do not say it was sent. Explain the failure plainly and offer the alternative channel."
        )

    return ""


def _clear_pending_handoff_state(state: State) -> None:
    """Clear one-shot transfer continuity keys after the new agent speaks.

    IMPORTANT: Set to empty string, never delete (pop). ADK's
    inject_session_state raises KeyError if a template variable referenced
    in an agent instruction is missing from state entirely. The sub-agent
    instructions reference these keys via ``{temp:pending_handoff_*}``
    placeholders, so the keys must always exist.
    """
    keys = (
        "temp:pending_handoff_target_agent",
        "temp:pending_handoff_latest_user",
        "temp:pending_handoff_latest_agent",
        "temp:pending_handoff_recent_customer_context",
    )
    for key in keys:
        try:
            state[key] = ""
        except Exception:
            logger.debug("Failed to clear pending handoff key %s", key, exc_info=True)


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
    agent_name = getattr(callback_context, "agent_name", "") or ""

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
                    company_profile=company_profile,
                    state=callback_context.state,
                )
            )

    has_runtime_context = isinstance(industry_config, dict)

    if already_greeted:
        # Build conversation recovery context so the model picks up mid-call
        # even after a Live API crash/reconnect that wipes conversation history.
        last_agent_turn = _state_get(callback_context.state, "temp:last_agent_turn", "")
        last_user_turn = _state_get(callback_context.state, "temp:last_user_turn", "")
        continuity_parts = [
            "CONVERSATION CONTINUITY — STRICT RULES: "
            "A greeting has already been delivered in this session. "
            "Do NOT greet, say hello, say 'how can I help you today', "
            "or introduce yourself again under any circumstances. "
            "Resume the conversation naturally from where it left off.",
        ]
        if isinstance(last_agent_turn, str) and last_agent_turn.strip():
            continuity_parts.append(
                f"Your last spoken line was: '{last_agent_turn.strip()[:200]}'. "
                "Continue from there."
            )
        if isinstance(last_user_turn, str) and last_user_turn.strip():
            continuity_parts.append(
                f"The customer last said: '{last_user_turn.strip()[:200]}'."
            )
        if not last_agent_turn and not last_user_turn:
            continuity_parts.append(
                "Ask the customer what they need help with today, but do NOT "
                "re-introduce yourself or repeat a greeting."
            )
        instruction_lines.append(" ".join(continuity_parts))
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

    channel = _state_get(callback_context.state, "app:channel", "")
    normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""

    company_line = _company_instruction(
        company_id,
        company_profile,
        company_knowledge,
        channel=normalized_channel,
    )
    if company_line:
        instruction_lines.append(company_line)
        has_runtime_context = True

    handoff_line = _handoff_instruction(callback_context.state, agent_name)
    if handoff_line:
        instruction_lines.append(handoff_line)
        has_runtime_context = True

    outbound_line = _outbound_delivery_instruction(callback_context.state)
    if outbound_line:
        instruction_lines.append(outbound_line)
        has_runtime_context = True

    if _is_callback_leg(callback_context.state):
        instruction_lines.append(
            "CALLBACK LEG — YOU ARE CALLING THE CUSTOMER BACK: "
            "This is an outbound callback that the customer previously requested. "
            "Do NOT call request_callback — you are already on the callback. "
            "Greet the customer warmly, remind them why you are calling back, "
            "and continue helping with their original request."
        )
        has_runtime_context = True

    # Inject global lessons (Tier 2 learning — cross-session behavioral rules)
    global_lessons = callback_context.state.get("app:global_lessons")
    if isinstance(global_lessons, list) and global_lessons:
        lessons_text = format_lessons_for_instruction(
            global_lessons, agent_name=agent_name,
        )
        if lessons_text:
            instruction_lines.append(lessons_text)
            has_runtime_context = True

    if has_runtime_context:
        if normalized_channel == "voice":
            _display_name, spoken_name = _resolve_company_names(company_profile)
            # This intentionally overlaps with the router's static voice supplement:
            # the router carries the base voice playbook, while this runtime check
            # reinforces the low-latency filler rule for any voice-session agent.
            instruction_lines.append(
                "NIGERIAN ACCENT AND PERSONA (MANDATORY): You are ehkaitay, a warm, confident "
                "Nigerian virtual assistant and Lagos business professional. You MUST speak in "
                "Nigerian English at all times. Speak with rhythmic, buoyant pacing. Keep "
                "consonants sharp and vowels rounded, letting your sentences end with a natural "
                "upward lilt. Use everyday Nigerian phrasing naturally ('Ehen', 'No problem at all', "
                "'Absolutely', 'We go sort you out'). If the customer switches into Pidgin, respond "
                "with clear, respectful Pidgin. Do NOT sound generic, American, or British."
            )
            instruction_lines.append(
                "NIGERIAN HOSTING (NO TRANSFERS): Good Nigerian hospitality means handling things "
                "personally. NEVER let the customer know you are transferring them to another agent. "
                "NEVER say 'I will transfer you', 'routing you to', or 'let me pass you to'. "
                "Instead, say things like 'Let me sort that out for you right now', 'Let me get that "
                "set up for you', or 'I'll take care of that now'. The customer must feel like YOU "
                "are doing all the work."
            )
            instruction_lines.append(
                f"VOICE PRONUNCIATION: When saying the business name aloud, pronounce it as "
                f"'{spoken_name}'."
            )
            instruction_lines.append(
                "NIGERIAN PACING (NO SILENCE): On a phone call, silence feels like a dropped "
                "connection. You MUST speak a conversational filler BEFORE any tool call or "
                "agent transfer. Ask an engaging follow-up question (e.g., 'Let me look that up — "
                "do you have a colour preference?') to keep the warm connection. Generate "
                "spoken text FIRST, then the tool call, in the same turn. Never leave more than "
                "2 seconds of silence. Always say 'naira' after prices (translate 'NGN' aloud to 'naira')."
            )
            instruction_lines.append(
                "NIGERIAN HOSPITALITY (CALLBACKS): If the customer asks to be called back, says they "
                "do not have enough airtime, or are out of time, be a gracious host. Use "
                "request_callback immediately to save their time. Do NOT interrogate them with "
                "follow-up questions about the callback. Just warmly tell them you'll call back shortly "
                "and end the topic."
            )
            instruction_lines.append(
                "NIGERIAN FAREWELLS: When the conversation naturally concludes or the customer says "
                "goodbye, give one brief, warm closing line and then immediately use end_call. "
                "Do not remain silent on the line, and don't drag out the goodbye."
            )
            if (
                bool(_state_get(callback_context.state, "temp:callback_requested", False))
                and not _is_callback_leg(callback_context.state)
            ):
                instruction_lines.append(
                    "CALLBACK WRAP-UP: A callback has already been registered. Be a polite host: "
                    "give one brief warm confirmation that you will call them back on this same "
                    "number shortly, then close the conversation. Do NOT ask follow-up questions "
                    "or start new topics."
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
    # In native-audio mode the response may contain only audio inline_data
    # (no text parts), so we check _response_has_content as well.
    text = _response_text(llm_response)
    has_content = text or _response_has_content(llm_response)
    if has_content and not bool(callback_context.state.get("temp:greeted", False)):
        callback_context.state["temp:greeted"] = True
    pending_target = _state_get(callback_context.state, "temp:pending_handoff_target_agent", "")
    if (
        has_content
        and isinstance(pending_target, str)
        and pending_target.strip() == callback_context.agent_name
    ):
        _clear_pending_handoff_state(callback_context.state)

    if (
        text
        and _state_get(callback_context.state, "app:channel", "") == "voice"
        and _response_commits_to_callback(text)
        and not bool(_state_get(callback_context.state, "temp:callback_requested", False))
        and not _is_callback_leg(callback_context.state)
    ):
        caller_phone = resolve_caller_phone_from_context(callback_context)
        tenant_id = _state_get(callback_context.state, "app:tenant_id", "public")
        company_id = _state_get(
            callback_context.state,
            "app:company_id",
            "ekaette-electronics",
        )
        if isinstance(caller_phone, str) and caller_phone.strip():
            result = service_voice.register_callback_request(
                phone=caller_phone.strip(),
                tenant_id=str(tenant_id or "public"),
                company_id=str(company_id or "ekaette-electronics"),
                source="voice_ai_auto_callback",
                reason="Auto-queued from spoken callback commitment",
                trigger_after_hangup=True,
            )
            status = str(result.get("status", "")).strip().lower()
            if status in {"pending", "queued", "cooldown"}:
                callback_context.state["temp:callback_requested"] = True
                _queue_end_after_speaking_control(
                    callback_context.state,
                    reason="callback_registered",
                )
                logger.info(
                    "Auto-queued callback from spoken commitment agent=%s phone=%s status=%s",
                    callback_context.agent_name,
                    caller_phone.strip(),
                    status,
                )
            else:
                logger.warning(
                    "Auto-callback queue failed after spoken commitment agent=%s phone=%s result=%r",
                    callback_context.agent_name,
                    caller_phone.strip(),
                    result,
                )

    if (
        text
        and _state_get(callback_context.state, "app:channel", "") == "voice"
        and _response_commits_to_callback(text)
        and bool(_state_get(callback_context.state, "temp:callback_requested", False))
        and not _is_callback_leg(callback_context.state)
    ):
        _queue_end_after_speaking_control(
            callback_context.state,
            reason="callback_acknowledged",
        )

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
    # Hard-block request_callback on callback legs to prevent infinite loops.
    if tool.name == "request_callback" and _is_callback_leg(tool_context.state):
        logger.warning(
            "Blocked request_callback on callback leg agent=%s",
            tool_context.agent_name,
        )
        return {
            "status": "error",
            "error": "already_on_callback",
            "detail": "You are already on a callback call. Do not request another callback.",
        }

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

    # Block transfers before greeting — forces the router to greet the
    # customer before handing off to a sub-agent.
    # NOTE: In Live API mode, ADK's base_llm_flow.py mistakenly closes
    # the Live connection when it sees ANY function_response named
    # "transfer_to_agent" — even blocked ones.  We patch that in
    # app/agents/tool_scheduling.py so this guard works safely.
    channel = _state_get(tool_context.state, "app:channel", "")
    is_voice = isinstance(channel, str) and channel.strip().lower() == "voice"
    already_greeted = bool(tool_context.state.get("temp:greeted", False))
    if is_voice and not already_greeted:
        # Count blocked attempts. After 3 blocks the model clearly won't greet
        # on its own — let it transfer rather than loop forever.
        blocked_count = int(tool_context.state.get("temp:greeting_block_count", 0))
        blocked_count += 1
        tool_context.state["temp:greeting_block_count"] = blocked_count
        if blocked_count >= 3:
            logger.warning(
                "greeting_guard_bypass agent=%s target=%s after %d blocked attempts",
                tool_context.agent_name,
                target_agent,
                blocked_count,
            )
            tool_context.state["temp:greeted"] = True
            # Fall through to normal transfer handling
        else:
            logger.warning(
                "transfer_blocked_before_greeting agent=%s target=%s attempt=%d",
                tool_context.agent_name,
                target_agent,
                blocked_count,
            )
            return {
                "error": "greeting_required",
                "detail": (
                    "Transfer blocked. You have not greeted the caller yet. "
                    "You MUST speak your greeting aloud to the customer NOW. "
                    "Say your greeting first, then you may transfer."
                ),
            }

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
    if tool.name == "search_catalog":
        query_raw = redacted_args.get("query")
        category_raw = redacted_args.get("category")
        query = query_raw if isinstance(query_raw, str) else ""
        category = category_raw if isinstance(category_raw, str) else ""
        storage_tokens = sorted(
            {match.group(0).lower() for match in _STORAGE_PATTERN.finditer(query)}
        )
        logger.info(
            "tool_start agent=%s tool=%s query=%r category=%r storage=%s args=%s",
            tool_context.agent_name,
            tool.name,
            query[:160],
            category[:80],
            storage_tokens,
            sorted(redacted_args.keys()),
        )
        return None
    target_agent = _tool_transfer_target_agent_name(tool.name, args)
    if target_agent is not None:
        latest_user_raw = _state_get(tool_context.state, "temp:last_user_turn", "")
        latest_agent_raw = _state_get(tool_context.state, "temp:last_agent_turn", "")
        recent_customer_raw = _state_get(tool_context.state, "temp:recent_customer_context", "")
        latest_user = latest_user_raw.strip() if isinstance(latest_user_raw, str) else ""
        latest_agent = latest_agent_raw.strip() if isinstance(latest_agent_raw, str) else ""
        recent_customer = (
            recent_customer_raw.strip() if isinstance(recent_customer_raw, str) else ""
        )
        signature = f"{target_agent}|{latest_user}|{latest_agent}|{recent_customer}"
        tool_context.state["temp:last_transfer_handoff_signature"] = signature
        tool_context.state["temp:pending_handoff_target_agent"] = target_agent
        tool_context.state["temp:pending_handoff_latest_user"] = latest_user
        tool_context.state["temp:pending_handoff_latest_agent"] = latest_agent
        tool_context.state["temp:pending_handoff_recent_customer_context"] = recent_customer
        logger.info(
            "Prepared transfer handoff target=%s has_user=%s has_agent=%s",
            target_agent,
            bool(latest_user),
            bool(latest_agent),
        )
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
    # Inject caller phone from ephemeral registry when session state lacks it.
    # ADK live-streaming mode sometimes fails to surface user:caller_phone
    # in the tool context's session state.
    if tool.name in _OUTBOUND_CALLER_TOOLS:
        _maybe_inject_caller_phone(tool_context)
    await before_tool_log(tool, args, tool_context)
    return None


def _tool_error_server_message(effective_result: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool callback error payloads into structured server messages."""
    code_raw = effective_result.get("code")
    code = str(code_raw).strip() if isinstance(code_raw, str) and code_raw.strip() else ""
    error_raw = effective_result.get("error")
    message_raw = effective_result.get("message")
    detail_raw = effective_result.get("detail")

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
        "message": (
            str(error_raw).strip()
            if isinstance(error_raw, str) and str(error_raw).strip()
            else (
                str(message_raw).strip()
                if isinstance(message_raw, str) and str(message_raw).strip()
                else (
                    str(detail_raw).strip()
                    if isinstance(detail_raw, str) and str(detail_raw).strip()
                    else "Tool error"
                )
            )
        ),
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
    try:
        from app.api.v1.at import voice_analytics
    except Exception:  # pragma: no cover - analytics should not break tool flow
        voice_analytics = None
    logger.info(
        "tool_end agent=%s tool=%s success=%s",
        tool_context.agent_name,
        tool.name,
        isinstance(effective_result, dict)
        and not effective_result.get("error")
        and str(effective_result.get("status", "")).strip().lower() != "error",
    )

    if not isinstance(effective_result, dict):
        return None

    if effective_result.get("error") or str(effective_result.get("status", "")).strip().lower() == "error":
        if tool.name in {"send_whatsapp_message", "send_sms_message"}:
            tool_context.state["temp:last_outbound_delivery_status"] = "failure"
        queue_server_message(tool_context.state, _tool_error_server_message(effective_result))
        return None

    if tool.name in {"send_whatsapp_message", "send_sms_message"}:
        channel = "whatsapp" if tool.name == "send_whatsapp_message" else "sms"
        phone = ""
        if tool.name == "send_whatsapp_message":
            caller_phone = _state_get(tool_context.state, "user:caller_phone", "")
            phone = caller_phone.strip() if isinstance(caller_phone, str) else ""
        else:
            recipient = effective_result.get("recipient")
            phone = recipient.strip() if isinstance(recipient, str) else ""
        tool_context.state["temp:last_outbound_delivery_status"] = "success"
        tool_context.state["temp:last_outbound_delivery_channels"] = channel
        tool_context.state["temp:last_outbound_delivery_phone"] = phone
        return None

    if tool.name == "end_call":
        status = str(effective_result.get("status", "")).strip().lower()
        channel = _state_get(tool_context.state, "app:channel", "")
        normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
        if status == "ok" and normalized_channel == "voice":
            reason = str(
                effective_result.get("reason")
                or args.get("reason")
                or "conversation_complete"
            ).strip() or "conversation_complete"
            _queue_end_after_speaking_control(
                tool_context.state,
                reason=reason,
            )
        return None

    if tool.name == "request_callback":
        status = str(effective_result.get("status", "")).strip().lower()
        if status in {"pending", "queued", "cooldown"}:
            tool_context.state["temp:callback_requested"] = True
            if voice_analytics is not None:
                try:
                    voice_analytics.mark_callback_requested(
                        session_id=str(tool_context.state.get("app:session_id", "") or ""),
                        phone=str(effective_result.get("phone", "") or ""),
                    )
                except Exception:
                    logger.debug("Voice analytics callback request skipped", exc_info=True)
            channel = _state_get(tool_context.state, "app:channel", "")
            normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
            if normalized_channel == "voice" and not _is_callback_leg(tool_context.state):
                _queue_end_after_speaking_control(
                    tool_context.state,
                    reason="callback_registered",
                )
        return None

    if tool.name == "create_virtual_account_payment":
        sms_sent = bool(effective_result.get("sms_sent"))
        whatsapp_sent = bool(effective_result.get("whatsapp_sent"))
        channels = [
            channel
            for channel, sent in (("sms", sms_sent), ("whatsapp", whatsapp_sent))
            if sent
        ]
        if channels:
            tool_context.state["temp:last_outbound_delivery_status"] = (
                "success" if len(channels) == 2 else "partial"
            )
            tool_context.state["temp:last_outbound_delivery_channels"] = " and ".join(channels)
            phone = effective_result.get("notification_phone", "")
            if isinstance(phone, str):
                tool_context.state["temp:last_outbound_delivery_phone"] = phone.strip()
        elif effective_result.get("notification_phone"):
            tool_context.state["temp:last_outbound_delivery_status"] = "failure"

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


_KNOWN_AGENT_NAMES = frozenset(KNOWN_SUB_AGENT_NAMES)


async def on_tool_error_emit(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    exception: Exception | None = None,
    *,
    error: Exception | None = None,
    **_: Any,
) -> dict[str, Any] | None:
    """Recover from tool errors so the Live API flow stays alive.

    Returns a dict so ADK feeds the error back to the model instead of
    crashing the session.  For hallucinated sub-agent calls, the hint
    tells the model to use transfer_to_agent instead.
    """
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

    # Hallucinated sub-agent name as direct function call
    if tool.name in _KNOWN_AGENT_NAMES:
        enabled_agents = resolve_enabled_agents_from_state(tool_context.state)
        if enabled_agents is not None and tool.name not in enabled_agents:
            payload = _agent_not_enabled_payload(
                state=tool_context.state,
                agent_name=tool.name,
                allowed_agents=enabled_agents,
            )
            payload["error"] = "agent_not_enabled"
            payload["tool"] = tool.name
            return payload
        latest_user_raw = _state_get(tool_context.state, "temp:last_user_turn", "")
        latest_agent_raw = _state_get(tool_context.state, "temp:last_agent_turn", "")
        recent_customer_raw = _state_get(tool_context.state, "temp:recent_customer_context", "")
        latest_user = latest_user_raw.strip() if isinstance(latest_user_raw, str) else ""
        latest_agent = latest_agent_raw.strip() if isinstance(latest_agent_raw, str) else ""
        recent_customer = (
            recent_customer_raw.strip() if isinstance(recent_customer_raw, str) else ""
        )
        tool_context.state["temp:pending_handoff_target_agent"] = tool.name
        tool_context.state["temp:pending_handoff_latest_user"] = latest_user
        tool_context.state["temp:pending_handoff_latest_agent"] = latest_agent
        tool_context.state["temp:pending_handoff_recent_customer_context"] = recent_customer
        tool_context.actions.transfer_to_agent = tool.name
        logger.info(
            "Recovering hallucinated sub-agent call via transfer_to_agent agent=%s target=%s",
            tool_context.agent_name,
            tool.name,
        )
        return {
            "error": f"'{tool.name}' is not a callable function.",
            "hint": f"Use transfer_to_agent(agent_name='{tool.name}') instead.",
        }

    # Generic tool error — still return a dict to keep the session alive
    return {
        "error": str(effective_exception),
        "hint": "Please try a different approach.",
    }
