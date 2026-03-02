"""Tests for support_agent wiring."""


class TestSupportAgentTools:
    def test_support_agent_has_company_grounding_tools(self):
        from app.agents.support_agent.agent import support_agent

        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in support_agent.tools
        }
        assert "google_search" in tool_names
        assert "search_company_knowledge" in tool_names
        assert "get_company_profile_fact" in tool_names
        assert "query_company_system" in tool_names
        assert "get_topship_delivery_quote" in tool_names
        assert "create_order_record" in tool_names
        assert "track_order_delivery" in tool_names
        assert "send_order_review_followup" in tool_names

    def test_support_agent_instruction_routes_inventory_to_catalog(self):
        from app.agents.support_agent.agent import support_agent

        instruction = support_agent.instruction.lower()
        assert "route to catalog_agent" in instruction
        assert "do not invent stock lists" in instruction
        assert "do not re-greet" in instruction
