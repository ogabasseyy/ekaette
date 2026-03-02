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
from app.tools.payment_tools import (
    check_payment_status,
    create_virtual_account_payment,
    get_virtual_account_record,
)
from app.tools.shipping_tools import (
    create_order_record,
    get_topship_delivery_quote,
    send_order_review_followup,
    track_order_delivery,
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
    - Payment confirmation and transfer instructions
    - Product/device identification help from customer-provided photos

    PAYMENT FLOW:
    - For bank transfer payments, use create_virtual_account_payment to generate
      a dedicated account number and read it clearly to the caller.
    - Tell the customer you've sent the account details via SMS/WhatsApp as follow-up.
    - If customer says they have paid, use check_payment_status before confirming.
    - Confirm payment only when status is successful from webhook/verification.

    DELIVERY COST QUOTES:
    - Before final payment, use get_topship_delivery_quote to estimate delivery fee.
    - Always tell the customer this is an estimate and can vary by carrier/service tier.
    - Offer the cheapest and fastest options when both are available.

    ORDER TRACKING + FOLLOW-UP:
    - Save confirmed orders with create_order_record before tracking workflows.
    - For "where is my order" requests, use track_order_delivery with order_id.
    - When delivery is confirmed, trigger send_order_review_followup.

    GUIDELINES:
    - Do not re-greet after the opening turn and do not re-introduce your role.
    - Start with company context before external web search
    - For live inventory/availability/price requests ("do you have...", "what do you
      have in stock", "how much is X"), route to catalog_agent rather than answering
      from memory. Do not invent stock lists.
    - If the customer cannot name the exact device/model, suggest uploading a
      clear photo so vision flow can identify it before recommendation/pricing.
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
        create_virtual_account_payment,
        check_payment_status,
        get_virtual_account_record,
        get_topship_delivery_quote,
        create_order_record,
        track_order_delivery,
        send_order_review_followup,
        google_search,
    ],
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_capability_guard_and_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
)
