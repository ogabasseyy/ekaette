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
from app.tools.wa_messaging import send_whatsapp_message
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

_VOICE_PAYMENT_FOLLOWUP = (
    "    - Offer to send the account details via WhatsApp when helpful and use "
    "send_whatsapp_message only if the customer wants that follow-up."
)
_TEXT_PAYMENT_FOLLOWUP = (
    "    - Share the account details directly in this chat and do not promise a "
    "separate SMS/WhatsApp follow-up."
)

_INSTRUCTION_TEMPLATE = """You answer general customer questions, FAQs, and provide support.

    COMPANY IDENTITY:
    - The business for this session is '{{app:company_name}}'.
    - If the customer asks what company or business you work for, answer with
      exactly '{{app:company_name}}'.
    - Never invent, substitute, or paraphrase a different company or brand name.
    - Your personal assistant name is ehkaitay; never use the company name as
      your personal name.

    TRANSFER CONTINUITY:
    - You may be reached after another agent already spoke to the customer.
    - In that case, do NOT greet, re-introduce yourself, or restate the
      customer's request. Continue directly from the active handoff context.

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
{payment_followup_line}
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
    """

_BASE_TOOLS = [
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
        tools.append(send_whatsapp_message)
    return tools


def create_support_agent(model: str, *, channel: str = "voice") -> Agent:
    """Create a support agent with the specified model."""
    if channel not in ("voice", "text"):
        raise ValueError(f"Invalid channel: {channel!r}. Must be 'voice' or 'text'.")
    instruction = _INSTRUCTION_TEMPLATE.format(
        payment_followup_line=(
            _VOICE_PAYMENT_FOLLOWUP if channel == "voice" else _TEXT_PAYMENT_FOLLOWUP
        )
    )
    return Agent(
        name="support_agent",
        model=model,
        instruction=instruction,
        tools=_tools_for_channel(channel),
        **_CALLBACKS,
    )


# Module-level singleton for bidi-streaming (Live API)
support_agent = create_support_agent(LIVE_MODEL_ID)
