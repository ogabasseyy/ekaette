"""Booking Agent — Availability checking and appointment scheduling."""

import os

from google.adk.agents import Agent

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3-flash-preview")

booking_agent = Agent(
    name="booking_agent",
    model=VISION_MODEL,
    instruction="""You handle appointment scheduling, reservations, and pickups.
    Check availability from the booking_slots Firestore collection.
    Create bookings with confirmation IDs, dates, times, and locations.
    Support cancellations when customers provide their booking ID.
    Always confirm the booking details before finalizing.
    """,
    tools=[],  # Booking tools added in S10
)
