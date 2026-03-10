"""Tests for shared agent callback behaviors."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.agents.callbacks import (
    AGENT_NOT_ENABLED_ERROR_CODE,
    _company_instruction,
    after_model_valuation_sanity,
    after_tool_emit_messages,
    before_agent_isolation_guard,
    before_model_inject_config,
    before_tool_capability_guard_and_log,
    queue_server_message,
)


class TestQueueServerMessage:
    def test_increments_message_sequence(self):
        state: dict[str, object] = {}
        queue_server_message(state, {"type": "agent_status", "agent": "x", "status": "idle"})
        first = state["temp:last_server_message"]
        queue_server_message(state, {"type": "agent_status", "agent": "x", "status": "active"})
        second = state["temp:last_server_message"]

        assert isinstance(first, dict)
        assert isinstance(second, dict)
        assert first["id"] == 1
        assert second["id"] == 2


class TestBeforeModelInjectConfig:
    @pytest.mark.asyncio
    async def test_injects_industry_instruction(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                }
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        assert llm_request.config is not None
        assert llm_request.config.system_instruction is not None
        assert "Electronics & Gadgets" in str(llm_request.config.system_instruction)

    @pytest.mark.asyncio
    async def test_injects_company_instruction_when_available(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Hotels & Hospitality",
                    "greeting": "Welcome!",
                },
                "app:company_id": "acme-hotel",
                "app:company_profile": {
                    "name": "Acme Grand Hotel",
                    "overview": "Luxury hospitality in downtown Lagos.",
                    "facts": {"rooms": 120, "check_in_time": "14:00"},
                },
                "app:company_knowledge": [
                    {
                        "title": "Late checkout policy",
                        "text": "Late checkout until 1 PM for premium guests.",
                    }
                ],
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "Acme Grand Hotel" in system_instruction
        assert "rooms=120" in system_instruction
        assert "Late checkout policy" in system_instruction
        assert "acme-hotel" not in system_instruction
        assert "internal company ids" in system_instruction.lower()

    @pytest.mark.asyncio
    async def test_first_turn_greeting_uses_company_name_template(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Hotels & Hospitality",
                    "greeting": "Good day! How can I help with your stay today?",
                },
                "app:company_profile": {
                    "name": "Acme Grand Hotel",
                },
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "First-turn greeting policy" in system_instruction
        assert "assistant name is exactly 'ehkaitay'" in system_instruction
        assert "business name for this session is exactly 'Acme Grand Hotel'" in system_instruction
        assert "Hello, this is ehkaitay from Acme Grand Hotel." in system_instruction
        assert "How can I help you today?" in system_instruction
        assert "welcome to <company>" in system_instruction

    @pytest.mark.asyncio
    async def test_first_turn_greeting_uses_returning_customer_variant(self):
        callback_context = SimpleNamespace(
            state={
                "user:name": "Ada",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome! I can help with trade-ins and purchases.",
                },
                "app:company_profile": {
                    "name": "Awgabassey Gadgets",
                },
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "Welcome back, Ada. This is ehkaitay from Awgabassey Gadgets." in system_instruction

    @pytest.mark.asyncio
    async def test_first_turn_greeting_falls_back_when_company_missing(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "General",
                    "greeting": "Hello! How can I help you today?",
                },
                "app:company_profile": {},
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "Hello, this is ehkaitay from our service desk." in system_instruction

    @pytest.mark.asyncio
    async def test_does_not_emit_first_turn_greeting_when_already_greeted(self):
        callback_context = SimpleNamespace(
            state={
                "temp:greeted": True,
                "app:industry_config": {
                    "name": "Hotels & Hospitality",
                    "greeting": "Welcome!",
                },
                "app:company_profile": {"name": "Acme Grand Hotel"},
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "First-turn greeting policy" not in system_instruction
        assert "Do NOT greet again" in system_instruction
        assert "Do not re-introduce your role" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_pending_handoff_context_for_matching_agent(self):
        callback_context = SimpleNamespace(
            state={
                "temp:greeted": True,
                "app:industry_config": {"name": "Electronics"},
                "app:company_profile": {"name": "Awgabassey Gadgets"},
                "temp:pending_handoff_target_agent": "catalog_agent",
                "temp:pending_handoff_latest_user": "I want the iPhone 14 128GB.",
                "temp:pending_handoff_latest_agent": "Sure, let me connect you to catalog.",
                "temp:pending_handoff_recent_customer_context": (
                    "  Customer: I want the iPhone 14 128GB."
                ),
            },
            agent_name="catalog_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "Live handoff continuity" in system_instruction
        assert "I want the iPhone 14 128GB." in system_instruction
        assert "let me connect you to catalog" in system_instruction.lower()

    @pytest.mark.asyncio
    async def test_after_model_clears_pending_handoff_for_target_agent(self):
        callback_context = SimpleNamespace(
            state={
                "temp:pending_handoff_target_agent": "catalog_agent",
                "temp:pending_handoff_latest_user": "I want the iPhone 14 128GB.",
                "temp:pending_handoff_latest_agent": "Sure, let me connect you.",
                "temp:pending_handoff_recent_customer_context": "Customer: iPhone 14 128GB",
            },
            agent_name="catalog_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="We have that in stock.")])
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        assert callback_context.state.get("temp:pending_handoff_target_agent", "") == ""
        assert callback_context.state.get("temp:pending_handoff_latest_user", "") == ""

    @pytest.mark.asyncio
    async def test_after_model_auto_queues_callback_from_spoken_commitment(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
                "user:caller_phone": "+2348012345678",
            },
            agent_name="ekaette_router",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="I'll call you back on this same number right after this call.")]
            )
        )

        with patch("app.agents.callbacks.service_voice.register_callback_request") as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            await after_model_valuation_sanity(callback_context, llm_response)

        mock_register.assert_called_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_ai_auto_callback",
            reason="Auto-queued from spoken callback commitment",
            trigger_after_hangup=True,
        )
        assert callback_context.state["temp:callback_requested"] is True

    @pytest.mark.asyncio
    async def test_after_model_auto_queues_callback_using_session_state_phone_fallback(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
            },
            session=SimpleNamespace(
                state={
                    "user:caller_phone": "+2348012345678",
                }
            ),
            agent_name="ekaette_router",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="I will call you back on this number.")]
            )
        )

        with patch("app.agents.callbacks.service_voice.register_callback_request") as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            await after_model_valuation_sanity(callback_context, llm_response)

        mock_register.assert_called_once()
        assert callback_context.state["temp:callback_requested"] is True


class TestCompanyInstructionBuilder:
    def test_company_instruction_includes_core_sections(self):
        text = _company_instruction(
            company_id="acme-hotel",
            company_profile={
                "name": "Acme Grand Hotel",
                "overview": "Luxury hospitality in downtown Lagos.",
                "facts": {
                    "rooms": 120,
                    "check_in_time": "14:00",
                    "check_out_time": "12:00",
                },
            },
            company_knowledge=[
                {
                    "title": "Late checkout policy",
                    "text": "Late checkout until 1 PM for premium guests.",
                }
            ],
        )
        assert "Company context" in text
        assert "name='Acme Grand Hotel'" in text
        assert "Use this exact company name" in text
        assert "answer with the exact company name 'Acme Grand Hotel'" in text
        assert "Do not replace it with generic phrases like 'our company'" in text
        assert "rooms=120" in text
        assert "Late checkout policy" in text

    def test_company_instruction_returns_empty_without_profile(self):
        assert _company_instruction("acme-hotel", {}, []) == ""

    def test_company_instruction_output_shape_is_stable(self):
        text = _company_instruction(
            company_id="ekaette-electronics",
            company_profile={
                "name": "Awgabassey Gadgets",
                "overview": "Trade-in focused electronics store serving Lagos and Abuja.",
                "facts": {"support_hours": "09:00-19:00", "pickup_window": "10:00-18:00"},
            },
            company_knowledge=[
                {
                    "title": "Pickup policy",
                    "text": "Same-day pickup is available for confirmed bookings made before 2 PM.",
                }
            ],
        )
        assert text.startswith("Company context:")
        assert "Overview='" in text
        assert "Facts:" in text
        assert "Knowledge topics:" in text
        assert "Trust policy:" in text


class TestTransferHandoffStatePreparation:
    @pytest.mark.asyncio
    async def test_transfer_tool_prepares_pending_handoff_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        tool_context = SimpleNamespace(
            state={
                "temp:last_user_turn": "I want the iPhone 14 128GB.",
                "temp:last_agent_turn": "Sure, let me connect you to catalog.",
                "temp:recent_customer_context": "  Customer: I want the iPhone 14 128GB.",
            },
            agent_name="ekaette_router",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"agent_name": "catalog_agent"},
            tool_context,
        )

        assert result is None
        assert tool_context.state["temp:pending_handoff_target_agent"] == "catalog_agent"
        assert tool_context.state["temp:pending_handoff_latest_user"] == "I want the iPhone 14 128GB."
        assert tool_context.state["temp:pending_handoff_latest_agent"] == (
            "Sure, let me connect you to catalog."
        )
        assert tool_context.state["temp:pending_handoff_recent_customer_context"].endswith(".")


class TestAfterToolEmitMessages:
    @pytest.mark.asyncio
    async def test_emits_valuation_result_message(self):
        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = SimpleNamespace(state={}, agent_name="valuation_agent")
        result = {
            "device_name": "iPhone 14 Pro",
            "grade": "Good",
            "offer_amount": 230000,
            "currency": "NGN",
            "summary": "Minor wear",
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "valuation_result"
        assert message["deviceName"] == "iPhone 14 Pro"
        assert message["price"] == 230000

    @pytest.mark.asyncio
    async def test_emits_booking_confirmation_message(self):
        tool = SimpleNamespace(name="create_booking")
        ctx = SimpleNamespace(state={}, agent_name="booking_agent")
        result = {
            "confirmation_id": "EKT-ABC12345",
            "date": "2026-03-01",
            "time": "10:00",
            "location": "Lagos - Ikeja",
            "service_type": "trade-in pickup",
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "booking_confirmation"
        assert message["confirmationId"] == "EKT-ABC12345"
        assert message["time"] == "10:00"

    @pytest.mark.asyncio
    async def test_preserves_structured_error_payload(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(state={}, agent_name="ekaette_router")
        result = {
            "error": "agent_not_enabled",
            "code": AGENT_NOT_ENABLED_ERROR_CODE,
            "message": "Blocked",
            "agentName": "catalog_agent",
            "allowedAgents": ["booking_agent", "support_agent"],
            "industryTemplateId": "hotel",
        }

        await after_tool_emit_messages(tool, {"agent_name": "catalog_agent"}, ctx, result)

        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "error"
        assert message["code"] == AGENT_NOT_ENABLED_ERROR_CODE
        assert message["agentName"] == "catalog_agent"
        assert message["allowedAgents"] == ["booking_agent", "support_agent"]

    @pytest.mark.asyncio
    async def test_status_error_without_error_key_is_still_emitted_as_error(self):
        tool = SimpleNamespace(name="request_callback")
        ctx = SimpleNamespace(state={}, agent_name="ekaette_router")
        result = {
            "status": "error",
            "detail": "Could not queue callback request",
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "error"
        assert message["message"] == "Could not queue callback request"

    @pytest.mark.asyncio
    async def test_request_callback_marks_state_when_queued(self):
        tool = SimpleNamespace(name="request_callback")
        ctx = SimpleNamespace(state={}, agent_name="ekaette_router")
        result = {"status": "pending", "phone": "+2348012345678"}

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:callback_requested"] is True


class TestQuestionnaireWiring:
    """Phase 5: Wiring tests for questionnaire tool + callback integration."""

    def test_capability_map_contains_questionnaire_tool(self):
        """TOOL_CAPABILITY_MAP should include get_device_questionnaire_tool."""
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert "get_device_questionnaire_tool" in TOOL_CAPABILITY_MAP
        assert TOOL_CAPABILITY_MAP["get_device_questionnaire_tool"] == "valuation_tradein"

    @pytest.mark.asyncio
    async def test_emits_questionnaire_started_message(self):
        """after_tool_emit_messages should emit questionnaire_started for questionnaire tool."""
        tool = SimpleNamespace(name="get_device_questionnaire_tool")
        ctx = SimpleNamespace(state={}, agent_name="valuation_agent")
        result = {
            "questions": [
                {"id": "water_damage", "question": "Water damage?", "type": "boolean", "invert": False},
            ],
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "questionnaire_started"
        assert message["questionCount"] == 1

    @pytest.mark.asyncio
    async def test_valuation_result_includes_adjustments_when_present(self):
        """valuation_result ServerMessage should include adjustments and originalGrade."""
        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = SimpleNamespace(state={}, agent_name="valuation_agent")
        result = {
            "device_name": "iPhone 14 Pro",
            "grade": "Fair",
            "original_vision_grade": "Good",
            "adjustments": ["Battery at 75% → -1 step"],
            "adjustment_count": 1,
            "offer_amount": 175000,
            "currency": "NGN",
            "summary": "Minor wear",
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "valuation_result"
        assert message["originalGrade"] == "Good"
        assert message["adjustments"] == ["Battery at 75% → -1 step"]


class TestAgentIsolationGuards:
    @pytest.mark.asyncio
    async def test_before_agent_isolation_guard_blocks_disallowed_sub_agent(self):
        state: dict[str, object] = {
            "app:industry_template_id": "hotel",
            "app:tenant_id": "baci",
            "app:enabled_agents": ["booking_agent", "support_agent"],
        }
        callback_context = SimpleNamespace(
            agent_name="catalog_agent",
            state=state,
        )

        blocked = await before_agent_isolation_guard(callback_context)

        assert blocked is not None
        assert isinstance(blocked, types.Content)
        message = state["temp:last_server_message"]
        assert message["type"] == "error"
        assert message["code"] == AGENT_NOT_ENABLED_ERROR_CODE
        assert message["agentName"] == "catalog_agent"
        assert message["allowedAgents"] == ["booking_agent", "support_agent"]
        assert message["tenantId"] == "baci"
        assert message["industryTemplateId"] == "hotel"

    @pytest.mark.asyncio
    async def test_before_tool_composed_guard_blocks_disallowed_transfer(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:industry_template_id": "hotel",
                "app:enabled_agents": ["booking_agent", "support_agent"],
            },
            agent_name="ekaette_router",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"agent_name": "catalog_agent"},
            ctx,
        )

        assert isinstance(result, dict)
        assert result["error"] == "agent_not_enabled"
        assert result["code"] == AGENT_NOT_ENABLED_ERROR_CODE
        assert result["agentName"] == "catalog_agent"
        assert result["allowedAgents"] == ["booking_agent", "support_agent"]

    @pytest.mark.asyncio
    async def test_before_agent_isolation_guard_supports_mapping_like_state(self):
        class FakeState:
            def __init__(self, data: dict[str, object]):
                self._data = dict(data)

            def get(self, key: str, default: object = None) -> object:
                return self._data.get(key, default)

            def __setitem__(self, key: str, value: object) -> None:
                self._data[key] = value

            def __getitem__(self, key: str) -> object:
                return self._data[key]

        state = FakeState({
            "app:industry_template_id": "hotel",
            "app:enabled_agents": ["booking_agent", "support_agent"],
        })
        callback_context = SimpleNamespace(agent_name="catalog_agent", state=state)

        blocked = await before_agent_isolation_guard(callback_context)

        assert blocked is not None
        message = state["temp:last_server_message"]
        assert message["code"] == AGENT_NOT_ENABLED_ERROR_CODE
