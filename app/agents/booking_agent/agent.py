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
from app.tools.callback_tools import request_callback
from app.tools.sms_messaging import send_sms_message
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
    "6. Offer to send the account details via SMS or WhatsApp when helpful. "
    "Use send_sms_message or send_whatsapp_message only if the customer wants "
    "that follow-up."
)
_TEXT_DELIVERY_FOLLOWUP = (
    "6. Share the account details directly in this chat and do not promise a "
    "separate WhatsApp follow-up."
)

_INSTRUCTION_TEMPLATE = """You handle delivery quotes, purchase finalization, and pickup scheduling.

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
    - If '{{temp:pending_handoff_target_agent}}' is 'booking_agent', this is the
      first turn immediately after a live transfer.
    - Latest customer request before transfer: '{{temp:pending_handoff_latest_user}}'.
    - Previous agent's latest spoken line: '{{temp:pending_handoff_latest_agent}}'.
    - Recent customer-only context: '{{temp:pending_handoff_recent_customer_context}}'.
    - In that first transferred turn, do NOT repeat or paraphrase the previous
      agent's last question or statement. Continue from the next useful step.

    FULFILLMENT PREFERENCE:
    Before starting any fulfillment flow, ask: "Would you like this delivered or would
    you prefer to pick it up?" Then proceed with the matching flow below.
    If the customer already stated a preference earlier in the conversation, respect it
    without re-asking.

    DELIVERY QUOTE + CHECKOUT FLOW:
    1. Confirm product/items and subtotal.
    2. Ask for delivery destination city and full delivery address.
       For voice calls, offer both options:
       - customer can say the address on call, or
       - customer can type address details in WhatsApp chat while staying on the call.
    3. Call get_topship_delivery_quote to estimate delivery fee.
    4. Present subtotal + delivery fee + total clearly.
    5. Call create_virtual_account_payment and read account details clearly.
    {delivery_followup_line}
    7. Call create_order_record once order details are confirmed.
    8. If payment setup or verification takes a moment, use short conversational
       fillers ("One moment while I confirm that for you") instead of silence.
    9. If customer says they paid, call check_payment_status before confirmation.
    10. For tracking requests, call track_order_delivery.

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
    - For delivery, never finalize payment until address and quote are captured.
    - After a successful send_sms_message or send_whatsapp_message tool call, plainly confirm
      that the written details were sent. Never say the send failed unless the tool result failed.
    - If the customer asks to be called back later, says they are out of airtime,
      or says they do not have time to continue, use request_callback and confirm
      you will call them back on this same number, then wrap up the call warmly.
    - If tool results contain the currency code "NGN", always say "naira" to the customer instead.
    - After an order is confirmed and recorded, let the customer know they can
      call back anytime to check the status of their order or delivery.
    - Keep momentum: after every step, ask one clear next-step question.
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
        tools.append(request_callback)
        tools.append(send_sms_message)
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
        description="Schedules appointments, reservations, pickups, and manages booking modifications.",
        instruction=instruction,
        tools=_tools_for_channel(channel),
        **_CALLBACKS,
    )


# Module-level singleton for bidi-streaming (Live API)
booking_agent = create_booking_agent(LIVE_MODEL_ID)
