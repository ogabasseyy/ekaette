"""Support Agent — General questions, FAQs, and order tracking.

In bidi-streaming mode, all agents must use a Live API-compatible model.
Uses company grounding tools first, then Google Search for fresh external facts.
"""

from google.adk.agents import Agent
from google.adk.tools import google_search

from app.agents.callbacks import (
    after_model_valuation_sanity,
    after_tool_emit_messages,
    before_model_inject_config,
    before_tool_capability_guard_and_log,
    on_tool_error_emit,
)
from app.configs.model_resolver import resolve_live_model_id
from app.tools.knowledge_tools import (
    get_company_profile_fact,
    query_company_system,
    search_company_knowledge,
)

LIVE_MODEL_ID = resolve_live_model_id()

support_agent = Agent(
    name="support_agent",
    model=LIVE_MODEL_ID,
    instruction="""You answer general customer questions, FAQs, and provide support.

    Grounding priority:
    1) Use company tools first for company-specific truth:
       - search_company_knowledge
       - get_company_profile_fact
       - query_company_system
    2) Use google_search only for public/external facts not in company context.

    COMMON QUESTIONS:
    - Store hours, locations, policies
    - Return/warranty information
    - Device specifications and comparisons
    - Order status inquiries

    GUIDELINES:
    - Start with company context before external web search
    - Use google_search when the customer asks external facts you're not sure about
    - Be concise — give the answer first, then add context if needed
    - If the question is about a specific order, ask for the order/confirmation ID
    - For trade-in related questions, suggest transferring to the valuation agent
    - For booking questions, suggest transferring to the booking agent
    - Always be honest if you don't know — never make up information
    """,
    tools=[
        search_company_knowledge,
        get_company_profile_fact,
        query_company_system,
        google_search,
    ],
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_capability_guard_and_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
)
