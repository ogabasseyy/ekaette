"""Valuation Agent — Condition grading and trade-in pricing.

In bidi-streaming mode, all agents must use a Live API-compatible model.
Pricing logic is implemented as tools; the agent drives the conversation.
"""

from google.adk.agents import Agent
from google.genai import types

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
from app.tools.valuation_tools import grade_and_value_tool, negotiate_tool

LIVE_MODEL_ID = resolve_live_model_id()

valuation_agent = Agent(
    name="valuation_agent",
    model=LIVE_MODEL_ID,
    instruction="""You assess item condition and calculate trade-in value.

    When you receive analysis results from the vision_agent:
    1. Call grade_and_value_tool with the analysis JSON object serialized as a string
       (Live tool schema compatibility) to get grade + price
    2. Present the valuation clearly to the customer:
       - Device name and condition grade
       - Trade-in offer amount in ₦ (Nigerian Naira)
       - Brief explanation of how the grade was determined
    3. Ask if the customer accepts the offer or wants to negotiate

    When the customer makes a counter-offer:
    1. Call negotiate_tool with offer_amount, customer_ask, and max_amount
       - max_amount is the Excellent price for that device (highest possible)
    2. Based on the decision:
       - "accept": Confirm the agreed amount and offer to proceed to booking
       - "counter": Present the counter amount and explain it's our best offer
       - "reject": Politely explain the maximum possible value

    PRICING RULES:
    - Use company grounding context for pricing policy exceptions:
      - get_company_profile_fact for company pricing constraints
      - search_company_knowledge for valuation policy details
      - query_company_system when connected systems provide live policy flags
    - Always use Nigerian Naira (₦)
    - Be transparent about pricing — explain what affects the grade
    - If the device is not in our pricing table, apologize and offer to check manually
    - Never promise a price you can't back up with the tool result

    CONVERSATION STYLE:
    - Be warm and fair — this is a negotiation, not an argument
    - Highlight positives about the device before mentioning issues
    - After agreement, suggest scheduling a pickup via the booking_agent
    """,
    # Higher thinking budget for accurate pricing math
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=2048),
    ),
    tools=[
        grade_and_value_tool,
        negotiate_tool,
        search_company_knowledge,
        get_company_profile_fact,
        query_company_system,
    ],
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_capability_guard_and_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
)
