"""Booking Agent — Availability checking and appointment scheduling.

In bidi-streaming mode, all agents must use a Live API-compatible model.
Scheduling logic is implemented as tools.
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
from app.tools.knowledge_tools import (
    get_company_profile_fact,
    query_company_system,
    search_company_knowledge,
)
from app.tools.booking_tools import check_availability, create_booking, cancel_booking

LIVE_MODEL_ID = resolve_live_model_id()

booking_agent = Agent(
    name="booking_agent",
    model=LIVE_MODEL_ID,
    instruction="""You handle appointment scheduling, reservations, and pickups.

    BOOKING FLOW:
    1. Ask the customer for their preferred date and location
    2. Call check_availability to find open slots
    3. Present available time slots to the customer
    4. Once they choose, call create_booking with their details
    5. Confirm the booking with the confirmation ID

    CANCELLATION FLOW:
    1. Ask for the confirmation ID (starts with EKT-)
    2. Call cancel_booking to process the cancellation
    3. Confirm the cancellation was successful

    IMPORTANT:
    - Use company grounding tools before assumptions:
      - get_company_profile_fact for operating hours/locations/policies
      - search_company_knowledge for booking-specific policy details
      - query_company_system for connected booking/CRM checks when available
    - Always confirm details BEFORE creating the booking
    - Read back the date, time, and location for confirmation
    - If no slots are available, suggest alternative dates
    - The confirmation ID is important — make sure the customer notes it down
    - Be warm and helpful — scheduling should feel easy, not bureaucratic
    """,
    tools=[
        check_availability,
        create_booking,
        cancel_booking,
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
