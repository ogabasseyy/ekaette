"""Catalog Agent — Product search and recommendations.

In bidi-streaming mode, all agents must use a Live API-compatible model.
Search logic is implemented as tools.
"""

from google.adk.agents import Agent

from app.agents.callbacks import (
    after_model_valuation_sanity,
    after_tool_emit_messages,
    before_model_inject_config,
    before_tool_log,
    on_tool_error_emit,
)
from app.configs.model_resolver import resolve_live_model_id
from app.tools.catalog_tools import search_catalog
from app.tools.knowledge_tools import (
    get_company_profile_fact,
    query_company_system,
    search_company_knowledge,
)

LIVE_MODEL_ID = resolve_live_model_id()

catalog_agent = Agent(
    name="catalog_agent",
    model=LIVE_MODEL_ID,
    instruction="""You search for products in the catalog and make recommendations.

    When the customer asks about a product:
    1. Call search_catalog with their query (and category if mentioned)
    2. Present results naturally:
       - Product name and price in ₦
       - Availability (in stock or not)
       - Key features
    3. If the exact product is unavailable, suggest alternatives from the results

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
    - Always mention the price in Nigerian Naira (₦)
    - If multiple results match, present the top 3 and ask which interests them
    - If no results found, suggest broader search terms or popular items
    - For trade-in customers, mention they can offset the price with their trade-in value
    """,
    tools=[
        search_catalog,
        search_company_knowledge,
        get_company_profile_fact,
        query_company_system,
    ],
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
)
