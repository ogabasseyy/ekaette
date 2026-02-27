"""Tests for shared agent callback behaviors."""

from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.agents.callbacks import (
    AGENT_NOT_ENABLED_ERROR_CODE,
    _company_instruction,
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
        assert "rooms=120" in text
        assert "Late checkout policy" in text

    def test_company_instruction_returns_empty_without_profile(self):
        assert _company_instruction("acme-hotel", {}, []) == ""

    def test_company_instruction_output_shape_is_stable(self):
        text = _company_instruction(
            company_id="ekaette-electronics",
            company_profile={
                "name": "Ekaette Devices Hub",
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
        assert text.endswith(".")


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
