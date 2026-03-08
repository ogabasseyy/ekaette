"""ekaette_router — Root Agent with Multi-Agent Dispatch.

S11 additions:
- before_agent_callback: industry isolation + dedup mitigation for ADK Bug #3395
- after_agent_callback: memory save + token telemetry
- generate_content_config: thinking budget (256 for fast routing)
"""

import asyncio
import logging

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types

from app.agents.callbacks import (
    after_model_valuation_sanity,
    after_tool_emit_messages,
    before_agent_isolation_guard_and_dedup,
    before_model_inject_config,
    before_tool_capability_guard_and_log,
    on_tool_error_emit,
)
from app.agents.dedup import telemetry_after_agent
from app.tools.global_lessons import classify_lesson_scope, submit_global_lesson
from app.tools.wa_messaging import send_whatsapp_message
from app.configs.model_resolver import resolve_live_model_id
from app.agents.vision_agent.agent import create_vision_agent
from app.agents.valuation_agent.agent import create_valuation_agent
from app.agents.booking_agent.agent import create_booking_agent
from app.agents.catalog_agent.agent import create_catalog_agent
from app.agents.support_agent.agent import create_support_agent

LIVE_MODEL_ID = resolve_live_model_id()
logger = logging.getLogger(__name__)
MEMORY_SAVE_BATCH_SIZE = 25


def _state_int(state: dict[str, object], key: str, default: int = 0) -> int:
    raw = state.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _log_background_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as exc:
        logger.warning("Background memory save task failed: %s", exc)


def _schedule_memory_save(callback_context: CallbackContext) -> None:
    events = list(getattr(callback_context.session, "events", None) or [])
    if not events:
        return

    state = callback_context.state
    if bool(state.get("temp:memory_save_in_flight", False)):
        return

    cursor = _state_int(state, "temp:memory_event_cursor", 0)
    if cursor < 0:
        cursor = 0
    if cursor > len(events):
        cursor = len(events)
    if cursor >= len(events):
        return

    next_cursor = min(len(events), cursor + MEMORY_SAVE_BATCH_SIZE)
    events_to_save = events[cursor:next_cursor]
    if not events_to_save:
        return

    async def _save() -> None:
        try:
            await callback_context.add_events_to_memory(events=events_to_save)
            state["temp:memory_event_cursor"] = next_cursor
        finally:
            state["temp:memory_save_in_flight"] = False

    state["temp:memory_save_in_flight"] = True
    task = asyncio.create_task(_save())
    task.add_done_callback(_log_background_task_result)


def _check_for_global_lessons(
    events: list,
    cursor: int,
    tenant_id: str,
    company_id: str,
) -> int:
    """Scan recent user messages for behavioral corrections and submit as global lessons.

    Thread-safe: operates only on pre-captured snapshots, never touches shared state.
    Returns the new cursor value.
    """
    if not events:
        return cursor

    if cursor >= len(events):
        return cursor

    # Resolve Firestore client once before the loop
    try:
        from app.api.v1.admin import shared as admin_shared

        db = admin_shared.company_config_client or admin_shared.industry_config_client
    except Exception as exc:
        logger.debug("Firestore client resolution failed: %s", exc)
        db = None

    # Scan recent user messages for global corrections
    for event in events[cursor:]:
        author = getattr(event, "author", None)
        if author != "user":
            continue
        content = getattr(event, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None)
        if not parts:
            continue
        text = " ".join(
            getattr(part, "text", "") for part in parts
            if getattr(part, "text", None)
        ).strip()
        if not text or len(text) < 20:
            continue

        try:
            scope = classify_lesson_scope(text)
        except Exception as exc:
            logger.debug("Lesson scope classification failed: %s", exc)
            continue

        if scope != "global":
            continue

        # Found a global correction — submit as pending_review
        if db is not None:
            try:
                submit_global_lesson(
                    db,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    lesson_text=text,
                    category="general",
                    source="customer_feedback",
                )
                logger.info("Global lesson candidate submitted from user feedback")
            except Exception as exc:
                logger.debug("Global lesson submission failed: %s", exc)

    return len(events)


def _log_lesson_check_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as exc:
        logger.warning("Global lesson check task failed: %s", exc)


async def save_session_and_telemetry_callback(callback_context: CallbackContext):
    """Save conversation insights to Memory Bank + log token telemetry.

    Runs async — never blocks the live audio stream.
    """
    # Token telemetry (non-blocking)
    try:
        await telemetry_after_agent(callback_context=callback_context)
    except Exception as exc:
        logger.debug("Telemetry callback failed (non-blocking): %s", exc)

    # Memory Bank save (non-blocking, incremental via cursor)
    try:
        _schedule_memory_save(callback_context)
    except Exception as exc:
        logger.debug("Memory save scheduling failed (non-blocking): %s", exc)

    # Global lesson extraction (background task — never blocks audio)
    # Capture state values before threading to avoid race conditions
    lesson_cursor = _state_int(callback_context.state, "temp:lesson_check_cursor", 0)
    events_snapshot = list(getattr(callback_context.session, "events", None) or [])
    tenant_id = callback_context.state.get("app:tenant_id")
    company_id = callback_context.state.get("app:company_id")

    if isinstance(tenant_id, str) and isinstance(company_id, str) and not callback_context.state.get("temp:lesson_check_in_flight", False):
        callback_context.state["temp:lesson_check_in_flight"] = True

        async def _run_lesson_check() -> None:
            try:
                new_cursor = await asyncio.to_thread(
                    _check_for_global_lessons, events_snapshot, lesson_cursor, tenant_id, company_id,
                )
                callback_context.state["temp:lesson_check_cursor"] = new_cursor
            finally:
                callback_context.state["temp:lesson_check_in_flight"] = False

        task = asyncio.create_task(_run_lesson_check())
        task.add_done_callback(_log_lesson_check_result)
    return None


_CORE_INSTRUCTION = """You are the company's virtual assistant named ehkaitay (spelled Ekaette).
    Your name is ehkaitay — always say it exactly like that when speaking.
    Always identify yourself as the company's virtual assistant in your opening greeting.
    If a customer asks to speak with a human, acknowledge the request and
    explain that human support can be reached via the company's direct
    contact channels.

    PRIMARY GOAL — CLOSE THE DEAL:
    Your overarching objective is to help the customer complete a purchase,
    trade-in, or booking. Every interaction should move the conversation
    toward a transaction. Be warm and helpful, never pushy — but always
    guide the customer toward a decision:
    - If they're browsing, recommend specific products and highlight value.
    - If they're comparing, help them narrow down and suggest the best fit.
    - If they're hesitating, address concerns proactively and offer alternatives.
    - If they have a device to trade in, emphasize how trade-in value reduces
      the cost of their upgrade — frame it as savings, not expense.
    - If they seem ready, move to next steps: "Shall I get that set up for you?"
      or "Want me to check availability for pickup today?"
    - After answering a question, always follow up with a soft next step:
      "Would you like to go ahead with this?" or "Can I help you with anything
      else to decide?"
    Never end a conversation without either closing a transaction or leaving
    a clear path for the customer to return and complete one.

    You have specialist sub-agents for different tasks:
    - vision_agent: When the customer sends photos or videos, or needs visual analysis.
      You support BOTH photo and video — always offer both options when asking for media.
      Videos are especially useful because they show the device from multiple angles.
    - valuation_agent: When assessing condition, calculating trade-in/market value,
      or when the customer wants to swap/upgrade their device (e.g. "swap my 14 Pro
      for a 15 Pro Max", "trade in and upgrade", "what would I pay to switch to...")
    - booking_agent: When scheduling appointments, reservations, or pickups
    - catalog_agent: When searching for products, rooms, vehicles, or items,
      especially for store-specific availability, pricing, and recommendations
    - support_agent: When answering general questions, FAQs, tracking orders,
      or public product/specification comparisons not requiring live inventory

    IMPORTANT — CONVERSATIONAL DEPTH:
    Before routing to a sub-agent, gather the information needed first.
    Do NOT jump to sub-agent routing until you understand what the customer needs.
    Examples:
    - "I want to swap my phone" → Ask: "What phone do you currently have, and
      what are you looking to upgrade to?"
    - "I want to book" → Ask: "What would you like to book, and when works for you?"
    - "Check my device" → Ask: "Could you send a photo or a short video of your
      device? A video walkthrough is great because it shows all angles."
    Only route to a sub-agent once you have enough context.

    MEDIA CAPABILITIES:
    You can analyze BOTH photos AND videos. When asking customers to send media
    of their device, ALWAYS mention both options. Prefer suggesting video when
    the customer wants a thorough assessment, as it captures multiple angles.
    Examples:
    - "Send me a photo or a short video of your device"
    - "A quick video walkthrough would help me see all sides of the device"
    Never say "send me a photo" without also mentioning video as an option.

    NEVER mention internal system details to the customer. Do NOT say things like
    "let me transfer you to the valuation agent" or "I'll route you to a specialist".
    Instead, say things like "Let me look into that for you" or "I'll check the
    trade-in value for you now."

    Route to the appropriate sub-agent based on customer intent.
    For product questions:
    - Use catalog_agent for "do you have...", price, stock, availability,
      product lookup, and store recommendations.
    - Treat likely ASR variants for CCTV (for example "CTV", "CT scan" in a
      hardware-shopping context) as product lookup intent and route to catalog_agent.
    - Use support_agent for general product comparisons/specs (e.g. "which has
      a better camera, iPhone 13 or 12?") when the customer is asking general
      knowledge rather than this company's live catalog data.
    - If the customer is not sure of the exact device/model, suggest image upload
      and route image analysis tasks to vision_agent for identification.
    Always be warm, professional, and helpful.
    If unsure which agent to use, ask the customer to clarify.

    Current industry configuration is loaded in session state under app:industry_config.
    Use the configured persona and greeting for the current industry.
    Company grounding context is loaded under:
    - app:company_profile
    - app:company_knowledge
    - app:company_id
    All specialist agents can call company grounding tools:
    - search_company_knowledge
    - get_company_profile_fact
    - query_company_system
    Prefer company context first for company-specific answers.

    MEMORY BEHAVIOR:
    At the start of each conversation, you receive memories about this customer
    from past interactions. Use them naturally:
    - If you remember their name, greet them by name
    - If they've traded in devices before, reference the history
    - If they had a complaint last time, proactively address it
    - If you know their preferences (pickup time, location), apply them
    Never say "I have a memory about you" — just use the knowledge naturally.

    GREETING RULES:
    Greet the customer ONLY at the very start of the conversation.
    After the initial greeting, NEVER greet again — no "hello", no "good
    morning", no "how can I help you". Just respond naturally to whatever
    the customer says.
    """

# The router keeps the broad voice-playbook in its static instruction. The
# before_model_inject_config callback separately reasserts the latency rule at
# runtime so transferred voice turns and non-router agents get the same guardrail.
_VOICE_SUPPLEMENT = """
    You handle real-time voice conversations with customers.

    MANDATORY FILLER — ZERO TOLERANCE FOR SILENCE:
    On a phone call, silence longer than 2 seconds feels like the call dropped.
    You MUST speak a brief filler phrase BEFORE every tool call and BEFORE
    every agent transfer. This is non-negotiable.

    BEFORE transferring to any sub-agent, ALWAYS say one of these first:
    - "Let me check that for you."
    - "One moment while I look into that."
    - "Let me pull up the details."
    - "Give me just a second to check."
    Generate the spoken filler FIRST, then the tool call, in the SAME turn.
    NEVER generate a transfer_to_agent call without speaking first.

    SILENCE HANDLING:
    If the customer has been silent for roughly 5-8 seconds after you spoke,
    gently check in with a short prompt. Keep it natural and not pushy:
    - "Are you still there?"
    - "Take your time, I'm here when you're ready."
    - "Would you like me to explain anything else?"
    Do NOT repeat the greeting. Do NOT re-introduce yourself.
    After two consecutive nudges with no response, say "It seems you may have
    stepped away. I'll be right here whenever you're ready!" and wait quietly.
    """

_TEXT_SUPPLEMENT = """
    You handle text-based conversations via WhatsApp and SMS.

    RESPONSE STYLE:
    - Keep responses concise and natural for messaging. Aim for 1-3 short paragraphs.
    - Do NOT write long walls of text. Customers are reading on their phones.
    - First messages should be brief and welcoming (1-2 sentences).
    - Follow-up messages should be focused — answer the question, ask one follow-up
      if needed, and stop. Never list more than 3-4 bullet points.

    NO PROCESSING NARRATION:
    - Do NOT narrate what you are about to do. Just do it and present the result.
    - BAD: "Let me calculate the delivery fee... One moment... Okay, the fee is ₦2,776."
    - GOOD: "Delivery to Yaba, Lagos: ₦2,776"
    - BAD: "I'll just need to check availability... Great, I found slots."
    - GOOD: "Here are the available slots:"
    - Never say "one moment", "let me check", "I'll just need to", or similar filler.
      In a text chat the customer doesn't see you working — they only see the final message.

    Do NOT use voice-specific language like "I hear you" or "sounds like".
    Use messaging-appropriate language instead.
    """

_INSTRUCTION = _CORE_INSTRUCTION + _VOICE_SUPPLEMENT
_TEXT_INSTRUCTION = _CORE_INSTRUCTION + _TEXT_SUPPLEMENT

_THINKING_CONFIG = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=256),
)

_CALLBACKS = dict(
    before_agent_callback=before_agent_isolation_guard_and_dedup,
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_capability_guard_and_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
    after_agent_callback=save_session_and_telemetry_callback,
)


def create_ekaette_router(model: str, *, channel: str = "voice") -> Agent:
    """Create the root router agent with all sub-agents using the given model.

    Args:
        model: Gemini model ID.
        channel: "voice" for Live API bidi, "text" for WhatsApp/SMS.
    """
    if channel not in ("voice", "text"):
        raise ValueError(f"Invalid channel: {channel!r}. Must be 'voice' or 'text'.")
    instruction = _TEXT_INSTRUCTION if channel == "text" else _INSTRUCTION
    tools = [PreloadMemoryTool()]
    if channel == "voice":
        tools.append(send_whatsapp_message)
    return Agent(
        name="ekaette_router",
        model=model,
        instruction=instruction,
        generate_content_config=_THINKING_CONFIG,
        tools=tools,
        sub_agents=[
            create_vision_agent(model),
            create_valuation_agent(model, channel=channel),
            create_booking_agent(model, channel=channel),
            create_catalog_agent(model, channel=channel),
            create_support_agent(model, channel=channel),
        ],
        **_CALLBACKS,
    )


# Module-level singleton for bidi-streaming (Live API)
ekaette_router = create_ekaette_router(LIVE_MODEL_ID)

# Export for ADK discovery (adk web uses `agent` by convention)
agent = ekaette_router
