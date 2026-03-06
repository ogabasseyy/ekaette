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
    return None


_CORE_INSTRUCTION = """You are Ekaette, an AI-powered customer service assistant.
    Always identify yourself as an AI assistant in your opening greeting.
    If a customer asks to speak with a human, acknowledge the request and
    explain that human support can be reached via the company's direct
    contact channels.

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

_VOICE_SUPPLEMENT = """
    You handle real-time voice conversations with customers.

    LATENCY BEHAVIOR:
    When routing to a sub-agent or calling a tool, produce a natural
    filler response immediately. Never leave more than 2 seconds of silence.
    Examples: "Let me take a closer look...", "One moment while I check..."

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

    RESPONSE LENGTH:
    Keep responses concise and natural for messaging. Aim for 1-3 short paragraphs.
    Do NOT write long walls of text. Customers are reading on their phones.
    First messages should be brief and welcoming (1-2 sentences).
    Follow-up messages should be focused — answer the question, ask one follow-up
    if needed, and stop. Never list more than 3-4 bullet points.

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
    return Agent(
        name="ekaette_router",
        model=model,
        instruction=instruction,
        generate_content_config=_THINKING_CONFIG,
        tools=[PreloadMemoryTool()],
        sub_agents=[
            create_vision_agent(model),
            create_valuation_agent(model),
            create_booking_agent(model),
            create_catalog_agent(model),
            create_support_agent(model),
        ],
        **_CALLBACKS,
    )


# Module-level singleton for bidi-streaming (Live API)
ekaette_router = create_ekaette_router(LIVE_MODEL_ID)

# Export for ADK discovery (adk web uses `agent` by convention)
agent = ekaette_router
