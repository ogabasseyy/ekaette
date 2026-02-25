"""Tests for shared agent callback behaviors."""

from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest

from app.agents.callbacks import (
    _company_instruction,
    after_tool_emit_messages,
    before_model_inject_config,
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
