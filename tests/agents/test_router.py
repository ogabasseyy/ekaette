"""Tests for the Ekaette root agent (router) structure."""

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
        """Sub-agents must use a Live API-compatible model for bidi-streaming."""
        from app.agents.ekaette_router.agent import ekaette_router
        from app.configs.model_resolver import resolve_live_model_id

        live_model_id = resolve_live_model_id()
        for sa in ekaette_router.sub_agents:
            assert sa.model == live_model_id, (
                f"Sub-agent {sa.name} must use LIVE_MODEL_ID={live_model_id}, got: {sa.model}"
            )
            assert sa.model != "gemini-3-flash-preview", (
                f"Sub-agent {sa.name} must not use unsupported preview model {sa.model}"
            )

    def test_sub_agents_have_model_and_tool_callbacks(self):
        """Sub-agents expose callbacks for production observability/event wiring."""
        from app.agents.ekaette_router.agent import ekaette_router
        for sa in ekaette_router.sub_agents:
            assert sa.before_model_callback is not None
            assert sa.after_model_callback is not None
            assert sa.before_tool_callback is not None
            assert sa.after_tool_callback is not None
            assert sa.on_tool_error_callback is not None

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

    def test_agent_instruction_mentions_cctv_asr_variants_for_catalog_routing(self):
        """Router should treat CCTV ASR variants as catalog inventory intent."""
        from app.agents.ekaette_router.agent import ekaette_router

        instruction = ekaette_router.instruction
        assert "CTV" in instruction
        assert "CT scan" in instruction

    def test_agent_instruction_mentions_company_grounding_state(self):
        """Router instruction should mention company grounding context keys."""
        from app.agents.ekaette_router.agent import ekaette_router

        instruction = ekaette_router.instruction
        assert "app:company_profile" in instruction
        assert "app:company_knowledge" in instruction
        assert "app:company_id" in instruction

    def test_agent_has_after_agent_callback(self):
        """Root agent has an after_agent_callback for memory saves."""
        from app.agents.ekaette_router.agent import ekaette_router
        assert ekaette_router.after_agent_callback is not None

    def test_agent_has_model_and_tool_callbacks(self):
        """Root agent has callbacks wired for model/tool lifecycle."""
        from app.agents.ekaette_router.agent import ekaette_router
        assert ekaette_router.before_model_callback is not None
        assert ekaette_router.after_model_callback is not None
        assert ekaette_router.before_tool_callback is not None
        assert ekaette_router.after_tool_callback is not None
        assert ekaette_router.on_tool_error_callback is not None

    def test_router_uses_isolation_and_dedup_before_agent_callback(self):
        """Root router should enforce industry isolation before dedup mitigation."""
        from app.agents.callbacks import before_agent_isolation_guard_and_dedup
        from app.agents.ekaette_router.agent import ekaette_router

        assert ekaette_router.before_agent_callback is before_agent_isolation_guard_and_dedup

    def test_agent_instruction_includes_ai_disclosure(self):
        """Root agent instruction must include AI disclosure for transparency (EU AI Act Art. 50)."""
        from app.agents.ekaette_router.agent import ekaette_router
        instruction = ekaette_router.instruction
        assert "AI-powered" in instruction, "Instruction should disclose AI nature"

    def test_agent_instruction_includes_human_escalation(self):
        """Root agent instruction must offer human escalation path (EU AI Act best practice)."""
        from app.agents.ekaette_router.agent import ekaette_router
        instruction = ekaette_router.instruction
        assert "human" in instruction.lower(), "Instruction should mention human escalation"

    def test_agent_has_preload_memory_tool(self):
        """Root agent includes PreloadMemoryTool in tool list."""
        from app.agents.ekaette_router.agent import ekaette_router
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in ekaette_router.tools
        }
        assert "preload_memory" in tool_names

    def test_agent_has_send_whatsapp_message_tool(self):
        """Root agent includes send_whatsapp_message for SIP bridge calls."""
        from app.agents.ekaette_router.agent import ekaette_router
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in ekaette_router.tools
        }
        assert "send_whatsapp_message" in tool_names

    def test_text_router_omits_send_whatsapp_message_tool(self):
        from app.agents.ekaette_router.agent import create_ekaette_router

        agent = create_ekaette_router(model="gemini-3-flash-preview", channel="text")
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in agent.tools
        }
        assert "send_whatsapp_message" not in tool_names

    def test_text_router_sub_agents_omit_send_whatsapp_message_tool(self):
        from app.agents.ekaette_router.agent import create_ekaette_router

        agent = create_ekaette_router(model="gemini-3-flash-preview", channel="text")
        for sub_agent in agent.sub_agents:
            tool_names = {
                getattr(tool, "name", getattr(tool, "__name__", str(tool)))
                for tool in getattr(sub_agent, "tools", [])
            }
            assert "send_whatsapp_message" not in tool_names

    def test_model_reads_from_env(self):
        """Root agent reads LIVE_MODEL_ID from environment at module load time."""
        # Verify the module-level variable matches what os.getenv returns
        from app.agents.ekaette_router import agent as router_mod
        from app.configs.model_resolver import resolve_live_model_id
        expected = resolve_live_model_id()
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

    def test_catalog_and_support_instructions_mention_photo_upload_path(self):
        """Catalog/support instructions should guide photo-upload identification flow."""
        from app.agents.catalog_agent.agent import catalog_agent
        from app.agents.support_agent.agent import support_agent

        catalog_instruction = catalog_agent.instruction.lower()
        support_instruction = support_agent.instruction.lower()

        assert "upload" in catalog_instruction and "photo" in catalog_instruction
        assert "upload" in support_instruction and "photo" in support_instruction

    @pytest.mark.parametrize("agent_module,agent_name", [
        ("app.agents.vision_agent.agent", "vision_agent"),
        ("app.agents.valuation_agent.agent", "valuation_agent"),
        ("app.agents.booking_agent.agent", "booking_agent"),
        ("app.agents.catalog_agent.agent", "catalog_agent"),
        ("app.agents.support_agent.agent", "support_agent"),
    ])
    def test_sub_agent_has_company_grounding_tools(self, agent_module, agent_name):
        """All specialist agents should have company grounding tools."""
        import importlib

        mod = importlib.import_module(agent_module)
        agent = getattr(mod, agent_name)
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in agent.tools
        }
        assert "search_company_knowledge" in tool_names
        assert "get_company_profile_fact" in tool_names
        assert "query_company_system" in tool_names
