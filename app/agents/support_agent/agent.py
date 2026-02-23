"""Support Agent — General questions, FAQs, and order tracking."""

import os

from google.adk.agents import Agent

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3-flash-preview")

support_agent = Agent(
    name="support_agent",
    model=VISION_MODEL,
    instruction="""You answer general customer questions, FAQs, and handle order tracking.
    Use grounding tools to search for accurate, up-to-date information.
    For order tracking, look up the order by ID in Firestore.
    Be helpful and direct — provide concise answers with sources when available.
    """,
    tools=[],  # google_search grounding tool added in S10
)
