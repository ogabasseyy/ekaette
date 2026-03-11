"""Tests for agent factory functions — create agents with configurable models."""

from google.adk.agents import Agent


class TestAgentFactories:
    """Each agent module exports a create_*_agent(model) factory."""

    def test_create_vision_agent_uses_given_model(self):
        from app.agents.vision_agent.agent import create_vision_agent

        agent = create_vision_agent(model="gemini-3-flash-preview")
        assert isinstance(agent, Agent)
        assert agent.model == "gemini-3-flash-preview"
        assert agent.name == "vision_agent"

    def test_create_valuation_agent_uses_given_model(self):
        from app.agents.valuation_agent.agent import create_valuation_agent

        agent = create_valuation_agent(model="gemini-3-flash-preview")
        assert isinstance(agent, Agent)
        assert agent.model == "gemini-3-flash-preview"
        assert agent.name == "valuation_agent"

    def test_voice_valuation_agent_includes_cross_channel_media_tool(self):
        from app.agents.valuation_agent.agent import create_valuation_agent

        agent = create_valuation_agent(model="gemini-3-flash-preview", channel="voice")
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in getattr(agent, "tools", [])
        }
        assert "request_media_via_whatsapp" in tool_names

    def test_text_valuation_agent_omits_cross_channel_media_tool(self):
        from app.agents.valuation_agent.agent import create_valuation_agent

        agent = create_valuation_agent(model="gemini-3-flash-preview", channel="text")
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in getattr(agent, "tools", [])
        }
        assert "request_media_via_whatsapp" not in tool_names

    def test_create_booking_agent_uses_given_model(self):
        from app.agents.booking_agent.agent import create_booking_agent

        agent = create_booking_agent(model="gemini-3-flash-preview")
        assert isinstance(agent, Agent)
        assert agent.model == "gemini-3-flash-preview"
        assert agent.name == "booking_agent"

    def test_create_catalog_agent_uses_given_model(self):
        from app.agents.catalog_agent.agent import create_catalog_agent

        agent = create_catalog_agent(model="gemini-3-flash-preview")
        assert isinstance(agent, Agent)
        assert agent.model == "gemini-3-flash-preview"
        assert agent.name == "catalog_agent"

    def test_create_support_agent_uses_given_model(self):
        from app.agents.support_agent.agent import create_support_agent

        agent = create_support_agent(model="gemini-3-flash-preview")
        assert isinstance(agent, Agent)
        assert agent.model == "gemini-3-flash-preview"
        assert agent.name == "support_agent"

    def test_create_ekaette_router_uses_given_model(self):
        from app.agents.ekaette_router.agent import create_ekaette_router

        agent = create_ekaette_router(model="gemini-3-flash-preview")
        assert isinstance(agent, Agent)
        assert agent.model == "gemini-3-flash-preview"
        assert agent.name == "ekaette_router"

    def test_create_ekaette_router_sub_agents_use_same_model(self):
        from app.agents.ekaette_router.agent import create_ekaette_router

        agent = create_ekaette_router(model="gemini-3-flash-preview")
        for sub in agent.sub_agents:
            assert sub.model == "gemini-3-flash-preview", (
                f"{sub.name} should use gemini-3-flash-preview, got {sub.model}"
            )

    def test_module_level_singletons_still_use_live_model(self):
        """The existing module-level agents must still use LIVE_MODEL_ID."""
        from app.agents.ekaette_router.agent import ekaette_router
        from app.configs.model_resolver import resolve_live_model_id

        live_model = resolve_live_model_id()
        assert ekaette_router.model == live_model


class TestTextRunnerInMain:
    """main.py exposes text_runner for text channels."""

    def test_text_runner_exists_in_main(self):
        import main as main_module

        assert hasattr(main_module, "text_runner")

    def test_text_runner_is_not_none_after_init(self):
        import main as main_module

        assert main_module.text_runner is not None
