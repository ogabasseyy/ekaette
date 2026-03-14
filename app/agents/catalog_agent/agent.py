"""Catalog Agent — Product search and recommendations.

In bidi-streaming mode, all agents must use a Live API-compatible model.
Search logic is implemented as tools.
"""

from google.adk.agents import Agent

from app.agents.callbacks import (
    after_model_valuation_sanity,
    after_tool_emit_messages,
    before_model_inject_config,
    before_tool_capability_guard_and_log,
    on_tool_error_emit,
)
from app.configs.model_resolver import resolve_live_model_id
from app.tools.callback_tools import request_callback
from app.tools.call_control_tools import end_call
from app.tools.catalog_tools import search_catalog
from app.tools.sms_messaging import send_sms_message
from app.tools.wa_messaging import send_whatsapp_message
from app.tools.knowledge_tools import (
    get_company_profile_fact,
    query_company_system,
    search_company_knowledge,
)

LIVE_MODEL_ID = resolve_live_model_id()

_INSTRUCTION = """You search for products in the catalog and make recommendations.

    COMPANY IDENTITY:
    - The business for this session is '{app:company_name}'.
    - If the customer asks what company or business you work for, answer with
      exactly '{app:company_name}'.
    - Never invent, substitute, or paraphrase a different company or brand name.
    - Your personal assistant name is ehkaitay; never use the company name as
      your personal name.

    TRANSFER CONTINUITY:
    - You may be reached after another agent already spoke to the customer.
    - In that case, do NOT greet, re-introduce yourself, or restate the
      customer's request. Continue directly from the active handoff context.
    - Runtime handoff details are injected separately before each model turn
      when a transfer actually happened. Follow that runtime context exactly.
    - In that first transferred turn, do NOT repeat or paraphrase the previous
      agent's last question or statement. Continue from the next useful step.

    CHECKOUT HANDOFF (CRITICAL):
    - Your scope ends at product discovery, availability, and recommendation.
    - As soon as the customer indicates intent to purchase (for example: "I want it",
      "let's proceed", "how do I pay", "deliver it", "book pickup"), immediately
      transfer to booking_agent for fulfillment and payment flow.
    - Do not keep the customer in catalog flow for payment, delivery quote, pickup,
      account number generation, or order confirmation.
    - Do not repeatedly ask preference questions already provided by the customer.

    When the customer asks about a product:
    1. Call search_catalog with their query (and category if mentioned)
    2. Present results naturally:
       - Product name and price in ₦
       - Availability (in stock or not)
       - Key features
    3. If the exact product is unavailable, suggest alternatives from the results
    4. If a follow-up is ambiguous (for example "which one do you have?"),
       use the last discussed product/category context and still provide options.
    5. If the customer is unsure about the exact model/spec, suggest they upload
       a photo so the vision flow can identify the device and you can recommend
       the closest available option from catalog.

    IMPORTANT:
    - Ground answers in company context first:
      - search_company_knowledge for policy/inventory notes
      - get_company_profile_fact for store facts (hours, branches, constraints)
      - query_company_system for connected inventory/CRM checks when available
    - If the customer asks a GENERAL public specification/comparison question
      (for example camera, battery, dimensions, release year), transfer to
      support_agent for the comparison. Keep catalog_agent focused on store
      lookup, availability, pricing, and recommendations.
    - If catalog/company systems are unavailable for a store-specific question,
      explain that live store data is currently unavailable instead of guessing.
    - If get_company_profile_fact returns missing/not found for a requested key,
      continue with available catalog data and move the customer to the next step;
      do not get stuck retrying the same fact lookup.
    - Always mention the price in Nigerian Naira. On voice calls, say "naira"
      (e.g. "four hundred and fifty thousand naira"), never just the ₦ symbol.
    - If tool results contain the currency code "NGN", say "naira" aloud to the customer.
    - If the customer asks to be called back later, says they are out of airtime,
      or says they do not have time to continue, use request_callback and confirm
      you will call them back on this number, then wrap up the call warmly.
    - If multiple results match, present the top 3 and ask which interests them
    - If no results found, suggest broader search terms or popular items
    - Proactively offer image upload when product identity is unclear:
      "If you upload a photo, I can identify it and match what we have."
    - For trade-in customers, mention they can offset the price with their trade-in value
    - If checkout/purchase intent is confirmed, transfer to booking_agent in the same turn.
    """

_BASE_TOOLS = [
    search_catalog,
    search_company_knowledge,
    get_company_profile_fact,
    query_company_system,
]

_CALLBACKS = dict(
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_capability_guard_and_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
)


def _tools_for_channel(channel: str) -> list[object]:
    tools = list(_BASE_TOOLS)
    if channel == "voice":
        tools.append(request_callback)
        tools.append(end_call)
        tools.append(send_sms_message)
        tools.append(send_whatsapp_message)
    return tools


def create_catalog_agent(model: str, *, channel: str = "voice") -> Agent:
    """Create a catalog agent with the specified model."""
    if channel not in ("voice", "text"):
        raise ValueError(f"Invalid channel: {channel!r}. Must be 'voice' or 'text'.")
    return Agent(
        name="catalog_agent",
        model=model,
        description="Searches product catalog for availability, pricing, and recommendations from store inventory.",
        instruction=_INSTRUCTION,
        tools=_tools_for_channel(channel),
        **_CALLBACKS,
    )


# Module-level singleton for bidi-streaming (Live API)
catalog_agent = create_catalog_agent(LIVE_MODEL_ID)
