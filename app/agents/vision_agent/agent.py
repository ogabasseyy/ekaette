"""Vision Agent — Image/video analysis.

In bidi-streaming mode, all agents must use a Live API-compatible model.
Complex vision tasks (grading, detailed analysis) are handled by tools
that can internally call standard API models like gemini-3-flash.
"""

from google.adk.agents import Agent

from app.agents.callbacks import (
    after_model_valuation_sanity,
    after_tool_emit_messages,
    before_model_inject_config,
    before_tool_log,
    on_tool_error_emit,
)
from app.configs.model_resolver import resolve_live_model_id
from app.tools.knowledge_tools import (
    get_company_profile_fact,
    query_company_system,
    search_company_knowledge,
)
from app.tools.vision_tools import analyze_device_image_tool

# Bidi mode requires Live API-compatible model for all agents
LIVE_MODEL_ID = resolve_live_model_id()

vision_agent = Agent(
    name="vision_agent",
    model=LIVE_MODEL_ID,
    instruction="""You analyze images and videos sent by customers for trade-in valuation.

    When the customer sends a photo of their device:
    1. Immediately say a filler like "Let me take a closer look at your device..."
    2. Call analyze_device_image_tool immediately (the latest uploaded image is available in session context)
    3. Report the results naturally:
       - Name the identified device
       - Describe the condition (screen, body, any damage)
       - Mention battery health if visible
    4. After reporting, suggest the customer proceed to valuation

    IMPORTANT:
    - The analyze_device_image_tool calls gemini-3-flash for detailed visual analysis.
      This is a NON_BLOCKING operation — keep talking while it processes.
    - If a customer asks policy/business questions mid-analysis, use:
      - search_company_knowledge
      - get_company_profile_fact
      - query_company_system (if connectors are configured)
    - If analysis returns device_name "Unknown", ask the customer to tell you what
      the device is or to send a clearer photo.
    - Always be encouraging about the device condition — focus on positives first,
      then mention any issues.

    Your analysis feeds into the valuation_agent for pricing.
    """,
    tools=[
        analyze_device_image_tool,
        search_company_knowledge,
        get_company_profile_fact,
        query_company_system,
    ],
    before_model_callback=before_model_inject_config,
    after_model_callback=after_model_valuation_sanity,
    before_tool_callback=before_tool_log,
    after_tool_callback=after_tool_emit_messages,
    on_tool_error_callback=on_tool_error_emit,
)
