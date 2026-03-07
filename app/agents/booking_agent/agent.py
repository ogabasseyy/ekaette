"""Booking Agent — Delivery quote + checkout, plus pickup scheduling.

In bidi-streaming mode, all agents must use a Live API-compatible model.
Commerce and scheduling logic is implemented as tools.
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
from app.tools.wa_messaging import send_whatsapp_message
from app.tools.knowledge_tools import (
    get_company_profile_fact,
    query_company_system,
    search_company_knowledge,
)
from app.tools.booking_tools import check_availability, create_booking, cancel_booking
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

_VOICE_DELIVERY_FOLLOWUP = (
    "6. Offer to send the account details via WhatsApp when helpful and call "
    "send_whatsapp_message only if the customer wants that follow-up."
)
_TEXT_DELIVERY_FOLLOWUP = (
    "6. Share the account details directly in this chat and do not promise a "
    "separate WhatsApp follow-up."
)

_INSTRUCTION_TEMPLATE = """You handle delivery quotes, purchase finalization, and pickup scheduling.

    FULFILLMENT PREFERENCE:
    Before starting any fulfillment flow, ask: "Would you like this delivered or would
    you prefer to pick it up?" Then proceed with the matching flow below.
    If the customer already stated a preference earlier in the conversation, respect it
    without re-asking.

    DELIVERY QUOTE + CHECKOUT FLOW:
    1. Confirm product/items and subtotal.
    2. Ask for delivery destination city (and full address when possible).
    3. Call get_topship_delivery_quote to estimate delivery fee.
    4. Present subtotal + delivery fee + total clearly.
    5. Call create_virtual_account_payment and read account details clearly.
    {delivery_followup_line}
    7. Call create_order_record once order details are confirmed.
    8. If customer says they paid, call check_payment_status before confirmation.
    9. For tracking requests, call track_order_delivery.

    PICKUP BOOKING FLOW (when customer chooses pickup):
    1. Ask for preferred date and location.
    2. Call check_availability to find open slots.
    3. Present available time slots.
    4. Once selected, call create_booking with customer details.
    5. Confirm with the booking confirmation ID.

    CANCELLATION FLOW:
    1. Ask for confirmation ID (starts with EKT-).
    2. Call cancel_booking.
    3. Confirm cancellation success.

    PICKUP FALLBACK WHEN NO SLOTS:
    - Do not dead-end. Offer next available dates/locations, or proceed with
      delivery quote + payment flow immediately.

    IMPORTANT:
    - Use company grounding tools before assumptions:
      - get_company_profile_fact for operating hours/locations/policies
      - search_company_knowledge for booking and delivery policy details
      - query_company_system for connected booking/CRM checks when available
    - Booking is optional for completed purchases; do not block checkout on slot availability.
    - Always confirm critical details before tool calls.
    - Be warm and helpful; keep transitions concise.
    """

_BASE_TOOLS = [
    check_availability,
    create_booking,
    cancel_booking,
    create_virtual_account_payment,
    check_payment_status,
    get_virtual_account_record,
    get_topship_delivery_quote,
    create_order_record,
    track_order_delivery,
    send_order_review_followup,
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
        tools.append(send_whatsapp_message)
    return tools


def create_booking_agent(model: str, *, channel: str = "voice") -> Agent:
    """Create a booking agent with the specified model."""
    if channel not in ("voice", "text"):
        raise ValueError(f"Invalid channel: {channel!r}. Must be 'voice' or 'text'.")
    instruction = _INSTRUCTION_TEMPLATE.format(
        delivery_followup_line=(
            _VOICE_DELIVERY_FOLLOWUP if channel == "voice" else _TEXT_DELIVERY_FOLLOWUP
        )
    )
    return Agent(
        name="booking_agent",
        model=model,
        instruction=instruction,
        tools=_tools_for_channel(channel),
        **_CALLBACKS,
    )


# Module-level singleton for bidi-streaming (Live API)
booking_agent = create_booking_agent(LIVE_MODEL_ID)
