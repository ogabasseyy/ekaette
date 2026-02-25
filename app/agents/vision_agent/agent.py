"""Vision Agent — Image/video analysis using Gemini 3 Flash."""

import os

from google.adk.agents import Agent

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3-flash-preview")

vision_agent = Agent(
    name="vision_agent",
    model=VISION_MODEL,
    instruction="""You analyze images and videos sent by customers.
    Identify objects, detect conditions (scratches, dents, wear), and read text in images.
    Use Visual Thinking to zoom into details when needed.
    Return structured analysis with identified items, their conditions, and notable details.
    Be thorough but concise — your analysis feeds into the valuation agent.
    """,
    tools=[],  # Vision tools added in S8
)
