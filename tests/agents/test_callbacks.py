"""Tests for shared agent callback behaviors."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from google.adk.tools.base_tool import BaseTool

from app.agents.callbacks import (
    AGENT_NOT_ENABLED_ERROR_CODE,
    _company_instruction,
    _is_callback_leg,
    _response_has_content,
    after_model_valuation_sanity,
    after_tool_emit_messages,
    before_agent_isolation_guard,
    before_model_inject_config,
    before_tool_capability_guard_and_log,
    on_tool_error_emit,
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
        assert "Do NOT greet" in system_instruction
        assert "Do not re-introduce your role" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_callback_wrapup_guidance_for_voice(self):
        callback_context = SimpleNamespace(
            state={
                "temp:greeted": True,
                "temp:callback_requested": True,
                "app:channel": "voice",
                "app:industry_config": {"name": "Electronics"},
                "app:company_profile": {"name": "Awgabassey Gadgets"},
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "CALLBACK WRAP-UP" in system_instruction
        assert "Do NOT ask follow-up questions" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_nigerian_voice_style_guidance_for_voice(self):
        callback_context = SimpleNamespace(
            state={
                "temp:greeted": True,
                "app:channel": "voice",
                "app:industry_config": {"name": "Electronics"},
                "app:company_profile": {"name": "Awgabassey Gadgets"},
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "VOICE STYLE" in system_instruction
        assert "Nigerian English" in system_instruction
        assert "Pidgin" in system_instruction

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
        assert "LIVE HANDOFF" in system_instruction
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
    async def test_clear_handoff_preserves_keys_in_state(self):
        """Keys must remain in state (set to '') — never deleted.

        ADK's inject_session_state raises KeyError when a template variable
        referenced in an agent instruction is missing from state entirely.
        """
        callback_context = SimpleNamespace(
            state={
                "temp:pending_handoff_target_agent": "support_agent",
                "temp:pending_handoff_latest_user": "Help me.",
                "temp:pending_handoff_latest_agent": "Transferring now.",
                "temp:pending_handoff_recent_customer_context": "Customer: Help me.",
            },
            agent_name="support_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="How can I help?")])
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        # Keys must still exist (not popped) to avoid ADK KeyError
        for key in (
            "temp:pending_handoff_target_agent",
            "temp:pending_handoff_latest_user",
            "temp:pending_handoff_latest_agent",
            "temp:pending_handoff_recent_customer_context",
        ):
            assert key in callback_context.state, f"{key} was deleted from state"
            assert callback_context.state[key] == "", f"{key} was not cleared to ''"

    @pytest.mark.asyncio
    async def test_after_model_sets_greeted_on_audio_only_response(self):
        """Native-audio Live API responses have inline_data, not text parts.

        The greeted flag must still be set so the greeting instruction is not
        re-injected on subsequent model turns.
        """
        callback_context = SimpleNamespace(
            state={},
            agent_name="support_agent",
        )
        audio_part = SimpleNamespace(
            text=None,
            inline_data=SimpleNamespace(
                data=b"\x00" * 960,
                mime_type="audio/pcm",
            ),
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(parts=[audio_part])
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        assert callback_context.state.get("temp:greeted") is True

    @pytest.mark.asyncio
    async def test_after_model_clears_handoff_on_audio_only_response(self):
        """Handoff state must clear when support_agent speaks via audio."""
        callback_context = SimpleNamespace(
            state={
                "temp:pending_handoff_target_agent": "support_agent",
                "temp:pending_handoff_latest_user": "Help me.",
                "temp:pending_handoff_latest_agent": "Transferring.",
                "temp:pending_handoff_recent_customer_context": "Help me.",
            },
            agent_name="support_agent",
        )
        audio_part = SimpleNamespace(
            text=None,
            inline_data=SimpleNamespace(
                data=b"\x00" * 960,
                mime_type="audio/pcm",
            ),
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(parts=[audio_part])
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        assert callback_context.state["temp:pending_handoff_target_agent"] == ""
        assert callback_context.state["temp:pending_handoff_latest_user"] == ""

    def test_response_has_content_text_only(self):
        resp = SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="hi")]))
        assert _response_has_content(resp) is True

    def test_response_has_content_audio_only(self):
        part = SimpleNamespace(
            text=None,
            inline_data=SimpleNamespace(data=b"\x00" * 100, mime_type="audio/pcm"),
        )
        resp = SimpleNamespace(content=SimpleNamespace(parts=[part]))
        assert _response_has_content(resp) is True

    def test_response_has_content_empty(self):
        resp = SimpleNamespace(content=SimpleNamespace(parts=[]))
        assert _response_has_content(resp) is False

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
        message = callback_context.state["temp:last_server_message"]
        assert message["type"] == "call_control"
        assert message["action"] == "end_after_speaking"

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

    @pytest.mark.asyncio
    async def test_after_model_callback_acknowledgement_queues_end_control(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:callback_requested": True,
            },
            agent_name="ekaette_router",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Certainly, I'll call you back on this same number shortly.")]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        message = callback_context.state["temp:last_server_message"]
        assert message["type"] == "call_control"
        assert message["action"] == "end_after_speaking"


class TestCallbackLegGuards:
    """Callback-leg detection and request_callback blocking."""

    def test_is_callback_leg_true(self):
        state = {"app:session_id": "sip-callback-abc123"}
        assert _is_callback_leg(state) is True

    def test_is_callback_leg_false_normal_session(self):
        state = {"app:session_id": "sip-inbound-abc123"}
        assert _is_callback_leg(state) is False

    def test_is_callback_leg_false_missing_key(self):
        state: dict[str, object] = {}
        assert _is_callback_leg(state) is False

    @pytest.mark.asyncio
    async def test_capability_guard_blocks_request_callback_on_callback_leg(self):
        tool = SimpleNamespace(name="request_callback")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-callback-abc123",
                "app:capabilities": ["outbound_messaging"],
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(tool, {}, ctx)
        assert isinstance(result, dict)
        assert result["status"] == "already_on_callback"

    @pytest.mark.asyncio
    async def test_capability_guard_allows_request_callback_on_normal_session(self):
        tool = SimpleNamespace(name="request_callback")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-inbound-abc123",
                "app:capabilities": ["outbound_messaging"],
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(tool, {}, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_callback_skipped_on_callback_leg(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:session_id": "sip-callback-abc123",
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
                "user:caller_phone": "+2348012345678",
            },
            agent_name="ekaette_router",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="I'll call you back on this same number.")]
            )
        )

        with patch("app.agents.callbacks.service_voice.register_callback_request") as mock_register:
            await after_model_valuation_sanity(callback_context, llm_response)

        mock_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_leg_instruction_injected(self):
        state = {
            "app:industry_config": {"name": "Electronics", "greeting": "Hello"},
            "app:company_profile": {"name": "Test Co"},
            "app:session_id": "sip-callback-abc123",
            "temp:greeted": True,
        }
        ctx = SimpleNamespace(state=state, agent_name="ekaette_router")
        llm_request = LlmRequest(config=types.GenerateContentConfig(system_instruction="Base."))
        await before_model_inject_config(ctx, llm_request)
        instruction = llm_request.config.system_instruction
        assert "CALLBACK LEG" in instruction
        assert "request_callback" in instruction

    @pytest.mark.asyncio
    async def test_transfer_blocked_before_greeting_on_voice(self):
        """Transfer guard blocks premature transfers (ADK patch makes this safe in Live mode)."""
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:capabilities": ["catalog_lookup", "policy_qa"],
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert isinstance(result, dict)
        assert result["error"] == "greeting_required"
        # Error must be actionable — tell the model exactly what to do
        assert "greet" in result["detail"].lower()
        assert "speak" in result["detail"].lower() or "say" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_transfer_allowed_after_greeting_on_voice(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allowed_on_text_channel_without_greeting(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "text",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert result is None


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
        ctx = SimpleNamespace(
            state={"app:channel": "voice"},
            agent_name="ekaette_router",
        )
        result = {"status": "pending", "phone": "+2348012345678"}

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:callback_requested"] is True
        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "call_control"
        assert message["action"] == "end_after_speaking"


class TestQuestionnaireWiring:
    """Phase 5: Wiring tests for questionnaire tool + callback integration."""

    def test_capability_map_contains_questionnaire_tool(self):
        """TOOL_CAPABILITY_MAP should include get_device_questionnaire_tool."""
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert "get_device_questionnaire_tool" in TOOL_CAPABILITY_MAP
        assert TOOL_CAPABILITY_MAP["get_device_questionnaire_tool"] == "valuation_tradein"

    def test_capability_map_contains_cross_channel_media_tool(self):
        """Cross-channel media handoff should be guarded like other valuation tools."""
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert "request_media_via_whatsapp" in TOOL_CAPABILITY_MAP
        assert TOOL_CAPABILITY_MAP["request_media_via_whatsapp"] == "valuation_tradein"

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


class TestOnToolErrorEmit:
    """Tests for on_tool_error_emit — tool error recovery."""

    def _make_tool_context(self, agent_name: str = "ekaette_router"):
        state: dict[str, object] = {}
        return SimpleNamespace(
            agent_name=agent_name,
            state=state,
            actions=SimpleNamespace(transfer_to_agent=None),
        )

    @pytest.mark.asyncio
    async def test_returns_recovery_dict_for_tool_not_found(self):
        """When a tool is not found (hallucination), return a dict so ADK
        feeds the error back to the model instead of crashing the live flow."""
        tool = BaseTool(name="catalog_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        err = ValueError("Tool 'catalog_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict), "Must return dict to prevent live flow crash"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_recovery_dict_for_generic_error(self):
        """Non-hallucination tool errors should also return a recovery dict."""
        tool = BaseTool(name="request_callback", description="Request callback")
        tool_context = self._make_tool_context()
        err = RuntimeError("Connection timeout")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict), "Must return dict to prevent live flow crash"

    @pytest.mark.asyncio
    async def test_queues_error_server_message(self):
        """Should still queue an error ServerMessage for the client."""
        tool = BaseTool(name="catalog_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        err = ValueError("Tool 'catalog_agent' not found.")

        await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        msg = tool_context.state.get("temp:last_server_message")
        assert msg is not None
        assert msg["type"] == "error"
        assert msg["code"] == "TOOL_EXCEPTION"

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_hint(self):
        """For hallucinated sub-agent calls, hint should mention transfer_to_agent."""
        tool = BaseTool(name="catalog_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        err = ValueError("Tool 'catalog_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert "transfer_to_agent" in result.get("hint", "")

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_requests_real_transfer(self):
        tool = BaseTool(name="catalog_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state["temp:last_user_turn"] = "Do you have iPhone 14?"
        tool_context.state["temp:last_agent_turn"] = "Let me check that for you."
        tool_context.state["temp:recent_customer_context"] = "Customer wants a phone."
        err = ValueError("Tool 'catalog_agent' not found.")

        await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert tool_context.actions.transfer_to_agent == "catalog_agent"
        assert tool_context.state["temp:pending_handoff_target_agent"] == "catalog_agent"
        assert tool_context.state["temp:pending_handoff_latest_user"] == "Do you have iPhone 14?"
