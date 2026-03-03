"""Tests for the Ekaette root agent (router) structure."""

import os
import pytest


class TestEkaetteRouterAgent:
    """Test the root agent's configuration and structure."""

    def test_agent_exists_and_is_importable(self):
        """Root agent module can be imported."""
        from app.agents.ekaette_router.agent import ekaette_router
        assert ekaette_router is not None

    def test_agent_has_correct_name(self):
        """Root agent name matches expected value."""
        from app.agents.ekaette_router.agent import ekaette_router
        assert ekaette_router.name == "ekaette_router"

    def test_agent_uses_live_api_model(self):
        """Root agent uses the native audio model for Live API."""
        from app.agents.ekaette_router.agent import ekaette_router
        model = ekaette_router.model
        assert "native-audio" in model or "live" in model.lower(), (
            f"Root agent should use a native audio model, got: {model}"
        )

    def test_agent_has_sub_agents(self):
        """Root agent has exactly 5 sub-agents."""
        from app.agents.ekaette_router.agent import ekaette_router
        assert len(ekaette_router.sub_agents) == 5

    def test_sub_agent_names(self):
        """Sub-agents have the expected names."""
        from app.agents.ekaette_router.agent import ekaette_router
        names = {sa.name for sa in ekaette_router.sub_agents}
        expected = {"vision_agent", "valuation_agent", "booking_agent", "catalog_agent", "support_agent"}
        assert names == expected, f"Expected sub-agents {expected}, got {names}"

    def test_sub_agents_use_live_model(self):
        """Sub-agents use the same Live API model as root (bidi requires it).

        ADK creates separate Live sessions per agent in bidi-streaming mode,
        so ALL agents (root + sub) must use a Live API-compatible model.
        """
        from app.agents.ekaette_router.agent import ekaette_router
        root_model = ekaette_router.model
        for sa in ekaette_router.sub_agents:
            assert sa.model == root_model, (
                f"Sub-agent {sa.name} should use the same Live model as root "
                f"({root_model}), got: {sa.model}"
            )

    def test_agent_has_instruction(self):
        """Root agent has a non-empty instruction string."""
        from app.agents.ekaette_router.agent import ekaette_router
        assert ekaette_router.instruction is not None
        assert len(ekaette_router.instruction) > 50, "Instruction should be substantive"

    def test_agent_instruction_mentions_sub_agents(self):
        """Root agent instruction references all sub-agent names for routing."""
        from app.agents.ekaette_router.agent import ekaette_router
        instruction = ekaette_router.instruction
        for name in ["vision_agent", "valuation_agent", "booking_agent", "catalog_agent", "support_agent"]:
            assert name in instruction, f"Instruction should mention {name}"

    def test_agent_has_after_agent_callback(self):
        """Root agent has an after_agent_callback for memory saves."""
        from app.agents.ekaette_router.agent import ekaette_router
        assert ekaette_router.after_agent_callback is not None

    def test_agent_has_preload_memory_tool(self):
        """Root agent includes PreloadMemoryTool in tool list."""
        from app.agents.ekaette_router.agent import ekaette_router
        tool_names = {tool.name for tool in ekaette_router.tools}
        assert "preload_memory" in tool_names

    def test_model_reads_from_env(self):
        """Root agent reads LIVE_MODEL_ID from environment at module load time."""
        # Verify the module-level variable matches what os.getenv returns
        from app.agents.ekaette_router import agent as router_mod
        expected = os.getenv(
            "LIVE_MODEL_ID",
            "gemini-2.5-flash-native-audio-preview-12-2025",
        )
        assert router_mod.LIVE_MODEL_ID == expected


class TestSubAgentStubs:
    """Test individual sub-agent stubs exist and have correct structure."""

    @pytest.mark.parametrize("agent_module,agent_name", [
        ("app.agents.vision_agent.agent", "vision_agent"),
        ("app.agents.valuation_agent.agent", "valuation_agent"),
        ("app.agents.booking_agent.agent", "booking_agent"),
        ("app.agents.catalog_agent.agent", "catalog_agent"),
        ("app.agents.support_agent.agent", "support_agent"),
    ])
    def test_sub_agent_importable(self, agent_module, agent_name):
        """Each sub-agent module can be imported."""
        import importlib
        mod = importlib.import_module(agent_module)
        agent = getattr(mod, agent_name)
        assert agent is not None
        assert agent.name == agent_name

    @pytest.mark.parametrize("agent_module,agent_name", [
        ("app.agents.vision_agent.agent", "vision_agent"),
        ("app.agents.valuation_agent.agent", "valuation_agent"),
        ("app.agents.booking_agent.agent", "booking_agent"),
        ("app.agents.catalog_agent.agent", "catalog_agent"),
        ("app.agents.support_agent.agent", "support_agent"),
    ])
    def test_sub_agent_has_instruction(self, agent_module, agent_name):
        """Each sub-agent has a non-empty instruction."""
        import importlib
        mod = importlib.import_module(agent_module)
        agent = getattr(mod, agent_name)
        assert agent.instruction is not None
        assert len(agent.instruction) > 20
