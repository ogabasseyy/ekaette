"""Valuation Agent — Condition grading and trade-in pricing."""

import os

from google.adk.agents import Agent

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3-flash-preview")

valuation_agent = Agent(
    name="valuation_agent",
    model=VISION_MODEL,
    instruction="""You assess item condition and calculate trade-in or market value.
    Use the industry pricing tables from session state (app:industry_config).
    Grade items on a scale: Excellent, Good, Fair, Poor.
    Present valuations clearly with the grade, price, and reasoning.
    Handle counter-offers by checking if they fall within acceptable negotiation range.
    Always use the local currency (Nigerian Naira, ₦).
    """,
    tools=[],  # Valuation tools added in S9
)
