"""ekaette_router — Root Agent with Multi-Agent Dispatch."""

import os

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from app.agents.vision_agent.agent import vision_agent
from app.agents.valuation_agent.agent import valuation_agent
from app.agents.booking_agent.agent import booking_agent
from app.agents.catalog_agent.agent import catalog_agent
from app.agents.support_agent.agent import support_agent

LIVE_MODEL_ID = os.getenv(
    "LIVE_MODEL_ID",
    "gemini-2.5-flash-native-audio-preview-12-2025",
)


async def save_session_to_memory_callback(callback_context: CallbackContext):
    """Save conversation insights to Memory Bank after each interaction.

    Runs async — never blocks the live audio stream.
    """
    try:
        await callback_context.add_events_to_memory(
            events=callback_context.session.events[-10:]
        )
    except Exception:
        # Non-blocking: memory save failures must not crash voice
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
    - catalog_agent: When searching for products, rooms, vehicles, or items
    - support_agent: When answering general questions, FAQs, or tracking orders

    Route to the appropriate specialist based on customer intent.
    Always be warm, professional, and helpful.
    If unsure which agent to use, ask the customer to clarify.

    Current industry configuration is loaded in session state under app:industry_config.
    Use the configured voice persona and greeting for the current industry.

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
    """,
    tools=[PreloadMemoryTool()],
    sub_agents=[
        vision_agent,
        valuation_agent,
        booking_agent,
        catalog_agent,
        support_agent,
    ],
    after_agent_callback=save_session_to_memory_callback,
)

# Export for ADK discovery (adk web uses `agent` by convention)
agent = ekaette_router
