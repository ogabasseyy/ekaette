"""Catalog Agent — Product search and recommendations."""

import os

from google.adk.agents import Agent

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3-flash-preview")

catalog_agent = Agent(
    name="catalog_agent",
    model=VISION_MODEL,
    instruction="""You search for products, rooms, vehicles, or items in the catalog.
    Use Firestore queries (or Vertex AI Search when available) to find matching items.
    Present results with names, prices, availability, and key features.
    Suggest alternatives if the exact match is unavailable.
    """,
    tools=[],  # Catalog tools added in S10
)
