"""ekaette_router — Root Agent with Multi-Agent Dispatch.

S11 additions:
- before_agent_callback: dedup mitigation for ADK Bug #3395
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
    before_model_inject_config,
    before_tool_log,
    on_tool_error_emit,
)
from app.agents.dedup import dedup_before_agent, telemetry_after_agent
from app.configs.model_resolver import resolve_live_model_id
from app.agents.vision_agent.agent import vision_agent
from app.agents.valuation_agent.agent import valuation_agent
from app.agents.booking_agent.agent import booking_agent
from app.agents.catalog_agent.agent import catalog_agent
from app.agents.support_agent.agent import support_agent

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
    except Exception:
        pass

    # Memory Bank save (non-blocking, incremental via cursor)
    try:
        _schedule_memory_save(callback_context)
    except Exception:
        pass
    return None


ekaette_router = Agent(
    name="ekaette_router",
    model=LIVE_MODEL_ID,
    instruction="""You are Ekaette, a universal AI customer service agent.
    You handle real-time voice conversations with customers.

    You have specialist sub-agents for different tasks:
    - vision_agent: When the customer sends photos or needs visual analysis
    - valuation_agent: When assessing condition and calculating trade-in/market value
    - booking_agent: When scheduling appointments, reservations, or pickups
    - catalog_agent: When searching for products, rooms, vehicles, or items,
      especially for store-specific availability, pricing, and recommendations
    - support_agent: When answering general questions, FAQs, tracking orders,
      or public product/specification comparisons not requiring live inventory

    Route to the appropriate specialist based on customer intent.
    For product questions:
    - Use catalog_agent for "do you have...", price, stock, availability,
      product lookup, and store recommendations.
    - Use support_agent for general product comparisons/specs (e.g. "which has
      a better camera, iPhone 13 or 12?") when the customer is asking general
      knowledge rather than this company's live catalog data.
    Always be warm, professional, and helpful.
    If unsure which agent to use, ask the customer to clarify.

    Current industry configuration is loaded in session state under app:industry_config.
    Use the configured voice persona and greeting for the current industry.
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

    LATENCY BEHAVIOR:
    When transferring to a sub-agent or calling a tool, produce a natural
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

    GREETING RULES:
    Greet the customer ONLY at the very start of the conversation.
    After the initial greeting, NEVER greet again — no "hello", no "good
    morning", no "how can I help you". Just respond naturally to whatever
    the customer says.
    """,
    # Thinking budget: low for fast routing decisions
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=256),
    ),
    tools=[PreloadMemoryTool()],
    sub_agents=[
        vision_agent,
        valuation_agent,
        booking_agent,
        catalog_agent,
        support_agent,
    ],
    before_agent_callback=dedup_before_agent,
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
    after_agent_callback=save_session_and_telemetry_callback,
)

# Export for ADK discovery (adk web uses `agent` by convention)
agent = ekaette_router
