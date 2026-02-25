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
