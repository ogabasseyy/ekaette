"""Tests for shared agent callback behaviors."""

import json
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
    before_tool_capability_guard,
    before_model_inject_config,
    before_tool_capability_guard_and_log,
    on_tool_error_emit,
    queue_server_message,
)
from app.api.v1.realtime.voice_state_registry import (
    clear_registered_voice_state,
    get_registered_voice_state,
    update_voice_state,
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
    async def test_seeds_optional_instruction_state_defaults(self):
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

        assert callback_context.state["temp:vision_media_handoff_state"] == ""
        assert callback_context.state["temp:background_vision_status"] == ""
        assert callback_context.state["temp:pending_handoff_target_agent"] == ""
        assert callback_context.state["temp:pending_handoff_latest_user"] == ""
        assert callback_context.state["temp:pending_handoff_latest_agent"] == ""
        assert callback_context.state["temp:pending_handoff_recent_customer_context"] == ""

    @pytest.mark.asyncio
    async def test_callback_hospitality_not_injected_during_protected_opening(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "app:company_profile": {"name": "Ogabassey Gadgets"},
                "app:channel": "voice",
            },
            agent_name="ekaette_router",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "NIGERIAN PACING (NO SILENCE)" in system_instruction
        assert "NIGERIAN HOSPITALITY (CALLBACKS)" not in system_instruction

    @pytest.mark.asyncio
    async def test_callback_hospitality_returns_after_opening_progress(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "app:company_profile": {"name": "Ogabassey Gadgets"},
                "app:channel": "voice",
                "temp:opening_greeting_complete": True,
                "temp:first_user_turn_started": True,
            },
            agent_name="ekaette_router",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "NIGERIAN HOSPITALITY (CALLBACKS)" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_safe_no_analysis_guidance_for_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                }
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "No tool-backed vision analysis is currently available" in system_instruction
        assert "transfer to vision_agent before answering" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_media_request_status_guidance_for_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_media_request_status": "sending",
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "WhatsApp media request status" in system_instruction
        assert "Do not say the message was already sent" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_voice_tradein_media_collection_guidance_for_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_user_turn": "I want to swap my iPhone XS for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XS for an iPhone 14."
                ),
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "VOICE TRADE-IN MEDIA COLLECTION" in system_instruction
        assert "request_media_via_whatsapp" in system_instruction
        assert "Do NOT ask the caller to send media on the audio call" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_latest_analysis_guidance_for_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "red",
                    "color_confidence": 0.12,
                    "condition": "Good",
                    "power_state": "on",
                    "details": {"body": {"description": "Minor wear"}},
                },
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "Latest tool-backed vision analysis is available" in system_instruction
        assert "device_name='iPhone XR'" in system_instruction
        assert "brand='Apple'" in system_instruction
        assert "device_color='red'" in system_instruction
        assert "condition='Good'" in system_instruction
        assert "power_state='on'" in system_instruction
        assert "must say 'red' and no other colour" in system_instruction
        assert "did not confirm the device colour" not in system_instruction

    @pytest.mark.asyncio
    async def test_tradein_questionnaire_state_advances_from_latest_user_answer(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_user_turn": "It is 87%.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:tradein_questionnaire_state": {
                    "status": "in_progress",
                    "questions": [
                        {
                            "id": "battery_health_pct",
                            "question": "What's the battery health %? (Settings → Battery → Battery Health)",
                            "type": "number",
                            "invert": False,
                        },
                        {
                            "id": "account_locked",
                            "question": "Have you signed out of iCloud and disabled Find My?",
                            "type": "boolean",
                            "invert": True,
                        },
                    ],
                    "answers": {},
                    "current_index": 0,
                    "last_answer_source_text": "",
                    "omitted_question_ids": [],
                    "pending_completion_ack": False,
                    "catalog_cache": {},
                },
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        questionnaire_state = callback_context.state["temp:tradein_questionnaire_state"]
        assert questionnaire_state["answers"]["battery_health_pct"] == 87
        assert questionnaire_state["current_index"] == 1
        system_instruction = str(llm_request.config.system_instruction)
        assert "TRADE-IN QUESTIONNAIRE PHASE" in system_instruction
        assert "Have you signed out of iCloud and disabled Find My?" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_swap_direction_instruction_for_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_user_turn": "I want to swap from my iPhone XR to an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap from my iPhone XR to an iPhone 14."
                ),
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "SWAP DEVICE DIRECTION (MANDATORY)" in system_instruction
        assert "'my iPhone XR'" in system_instruction or "'iPhone XR'" in system_instruction
        assert "'an iPhone 14'" in system_instruction or "'iPhone 14'" in system_instruction
        assert "only about the current trade-in device" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_swap_direction_clarifier_when_two_devices_are_named_ambiguously(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_user_turn": "I want to swap my iPhone XR and iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR and iPhone 14."
                ),
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "SWAP DEVICE DIRECTION IS UNCLEAR" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_tradein_booking_handoff_instruction_for_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_offer_amount": 245000,
                "temp:last_user_turn": "Yes, let's proceed with the swap.",
                "temp:recent_customer_context": (
                    "Customer: Yes, let's proceed with the swap."
                ),
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        assert callback_context.state["temp:tradein_fulfillment_phase"] == "booking_pending"
        system_instruction = str(llm_request.config.system_instruction)
        assert "TRADE-IN BOOKING HANDOFF (MANDATORY)" in system_instruction
        assert "transfer_to_agent with booking_agent" in system_instruction
        assert "Do NOT ask for the customer's name" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_exact_colour_answer_instruction_from_grounded_analysis(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:background_vision_status": "ready",
                "temp:last_user_turn": "What colour is the phone?",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "red",
                    "condition": "Good",
                },
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "VISIBLE FACT ANSWER (MANDATORY)" in system_instruction
        assert "phone is red" in system_instruction
        assert "Do not mention any other colour" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_unknown_colour_guidance_for_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "unknown",
                    "color_confidence": 0.0,
                    "condition": "Good",
                    "details": {"body": {"description": "Minor wear"}},
                },
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "Latest tool-backed vision analysis is available" in system_instruction
        assert "did not confirm the device colour" in system_instruction
        assert "do not guess" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_canonical_live_swap_guidance_when_analysis_not_ready(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:last_user_turn": "Can you confirm the colour now?",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14.\n"
                    "Customer: Can you confirm the colour now?"
                ),
                "temp:vision_media_handoff_state": "transferring",
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "canonical background analysis path" in system_instruction
        assert "Do NOT transfer to vision_agent for this same media" in system_instruction

    @pytest.mark.asyncio
    async def test_injects_background_vision_guidance_for_voice_valuation_agent(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "temp:background_vision_status": "running",
            },
            agent_name="valuation_agent",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "BACKGROUND VISION ANALYSIS" in system_instruction
        assert "Do NOT request media again" in system_instruction
        assert "non-visual follow-up question" in system_instruction

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
    async def test_text_channel_uses_written_name_spelling_not_phonetic_intro(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "whatsapp",
                "app:industry_config": {
                    "name": "Electronics & Gadgets",
                    "greeting": "Welcome!",
                },
                "app:company_profile": {
                    "name": "Ogabassey Gadgets",
                },
            }
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "spell it exactly as 'Ekaette'" in system_instruction
        assert "Never type the phonetic spelling 'ehkaitay'" in system_instruction
        assert "Hello, this is ehkaitay from Ogabassey Gadgets." not in system_instruction

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
    async def test_callback_leg_instruction_does_not_claim_callback_was_requested(self):
        callback_context = SimpleNamespace(
            state={
                "temp:greeted": True,
                "app:channel": "voice",
                "app:session_id": "sip-callback-abc123",
                "app:industry_config": {"name": "Electronics"},
                "app:company_profile": {"name": "Awgabassey Gadgets"},
            },
            session=SimpleNamespace(id="sip-callback-abc123"),
            agent_name="ekaette_router",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "CALLBACK LEG" in system_instruction
        assert "Do NOT say 'as requested'" in system_instruction
        assert "customer previously requested" not in system_instruction

    @pytest.mark.asyncio
    async def test_router_injects_mandatory_voice_swap_handoff_for_explicit_pair(self):
        callback_context = SimpleNamespace(
            state={
                "temp:greeted": True,
                "app:channel": "voice",
                "app:industry_config": {"name": "Electronics"},
                "app:company_profile": {"name": "Awgabassey Gadgets"},
                "temp:last_user_turn": "I want to swap from XR to 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap from XR to 14."
                ),
            },
            agent_name="ekaette_router",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "VOICE SWAP ROUTING" in system_instruction
        assert 'transfer_to_agent(agent_name="valuation_agent")' in system_instruction
        assert "Do NOT ask catalog questions" in system_instruction

    @pytest.mark.asyncio
    async def test_router_does_not_inject_mandatory_swap_handoff_without_both_devices(self):
        callback_context = SimpleNamespace(
            state={
                "temp:greeted": True,
                "app:channel": "voice",
                "app:industry_config": {"name": "Electronics"},
                "app:company_profile": {"name": "Awgabassey Gadgets"},
                "temp:last_user_turn": "I want to swap my phone.",
                "temp:recent_customer_context": "Customer: I want to swap my phone.",
            },
            agent_name="ekaette_router",
        )
        llm_request = LlmRequest(model="gemini-test", contents=[])

        await before_model_inject_config(callback_context, llm_request)

        system_instruction = str(llm_request.config.system_instruction)
        assert "VOICE SWAP ROUTING" not in system_instruction

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
        assert "NIGERIAN ACCENT AND PERSONA" in system_instruction
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
    async def test_after_model_normalizes_written_name_for_text_channels(self):
        callback_context = SimpleNamespace(
            state={"app:channel": "whatsapp"},
            agent_name="ekaette_router",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Hello, this is ehkaitay from Ogabassey Gadgets.")]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        assert llm_response.content.parts[0].text == "Hello, this is Ekaette from Ogabassey Gadgets."

    @pytest.mark.asyncio
    async def test_after_model_rewrites_visible_question_while_background_analysis_runs(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:background_vision_status": "running",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
            agent_name="valuation_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Can you describe the condition of the phone for me?")]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        assert "describe the condition" not in llm_response.content.parts[0].text.lower()
        assert "which storage size would you like for the new phone" in llm_response.content.parts[0].text.lower()

    @pytest.mark.asyncio
    async def test_after_model_rewrites_unbacked_whatsapp_delivery_claim(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_media_request_status": "sending",
            },
            agent_name="valuation_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="I've sent it on WhatsApp already, please check there now.")]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        rewritten = llm_response.content.parts[0].text
        assert "reply there with the photo or short video" in rewritten.lower()
        assert "already" not in rewritten.lower()

    @pytest.mark.asyncio
    async def test_after_model_rewrites_booking_transfer_disclosure_on_voice(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_offer_amount": 168750,
                "temp:last_user_turn": "Yeah, you can proceed.",
                "temp:recent_customer_context": (
                    "Customer: Okay, so let's proceed with the swap then.\n"
                    "Customer: Yeah, you can proceed."
                ),
            },
            agent_name="valuation_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        text="Great, I'll transfer you to the booking agent now to finalize the swap!"
                    )
                ]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        rewritten = llm_response.content.parts[0].text.lower()
        assert "transfer" not in rewritten
        assert "booking agent" not in rewritten
        assert "next step" in rewritten

    @pytest.mark.asyncio
    async def test_after_model_rewrites_tradein_offer_to_lead_with_grounded_analysis(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_offer_amount": 234000,
                "temp:background_vision_status": "ready",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "red",
                    "color_confidence": 0.12,
                    "condition": "Good",
                    "power_state": "on",
                    "details": {
                        "screen": {"description": "Minor scratches"},
                        "body": {"description": "Light wear"},
                    },
                },
            },
            agent_name="valuation_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Our final offer is ₦234,000. Would you like to proceed?")]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        rewritten = llm_response.content.parts[0].text
        assert rewritten.startswith("Here's what I can confirm from the video:")
        assert "iphone xr" in rewritten.lower()
        assert "red" in rewritten.lower()
        assert "power on" in rewritten.lower()
        assert "good condition" in rewritten.lower()
        assert "₦234,000" in rewritten

    @pytest.mark.asyncio
    async def test_after_model_rewrites_color_confirmation_from_registry_analysis(self):
        user_id = "voice-user-color"
        session_id = "voice-session-color"
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "red",
                    "color_confidence": 0.12,
                    "condition": "Good",
                },
            },
        )
        try:
            callback_context = SimpleNamespace(
                state={
                    "app:channel": "voice",
                    "app:user_id": user_id,
                    "app:session_id": session_id,
                    "temp:background_vision_status": "ready",
                    "temp:last_user_turn": "Can you confirm the colour of the phone?",
                    "temp:recent_customer_context": (
                        "Customer: I want to swap my iPhone XR for an iPhone 14.\n"
                        "Customer: Can you confirm the colour of the phone?"
                    ),
                    "temp:last_analysis": {
                        "device_name": "iPhone XR",
                        "brand": "Apple",
                        "device_color": "blue",
                        "color_confidence": 0.91,
                        "condition": "Good",
                    },
                },
                session=SimpleNamespace(
                    state={
                        "app:user_id": user_id,
                        "app:session_id": session_id,
                    }
                ),
                agent_name="valuation_agent",
            )
            llm_response = SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text="The analysis confirms it's blue. Did you want to proceed with that offer?"
                        )
                    ]
                )
            )

            await after_model_valuation_sanity(callback_context, llm_response)

            rewritten = llm_response.content.parts[0].text.lower()
            assert "phone is red" in rewritten
            assert "blue" not in rewritten
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_after_model_prefers_live_session_turn_for_color_confirmation(self):
        user_id = "voice-user-color-session"
        session_id = "voice-session-color-session"
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "red",
                    "condition": "Good",
                },
            },
        )
        try:
            callback_context = SimpleNamespace(
                state={
                    "app:channel": "voice",
                    "app:user_id": user_id,
                    "app:session_id": session_id,
                    "temp:background_vision_status": "ready",
                    "temp:last_user_turn": "Yes, please.",
                },
                session=SimpleNamespace(
                    state={
                        "app:user_id": user_id,
                        "app:session_id": session_id,
                        "temp:last_user_turn": "Can you confirm the colour of the phone?",
                    }
                ),
                agent_name="valuation_agent",
            )
            llm_response = SimpleNamespace(
                content=SimpleNamespace(
                    parts=[SimpleNamespace(text="Yes, the video shows that your iPhone XR is blue.")]
                )
            )

            await after_model_valuation_sanity(callback_context, llm_response)

            rewritten = llm_response.content.parts[0].text.lower()
            assert "phone is red" in rewritten
            assert "blue" not in rewritten
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_after_model_rewrites_offer_summary_using_canonical_offer_and_analysis_state(self):
        user_id = "voice-user-offer-summary"
        session_id = "voice-session-offer-summary"
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "red",
                    "condition": "Good",
                    "power_state": "on",
                    "details": {
                        "screen_condition": "clear",
                    },
                },
            },
        )
        try:
            callback_context = SimpleNamespace(
                state={
                    "app:channel": "voice",
                    "app:user_id": user_id,
                    "app:session_id": session_id,
                    "temp:background_vision_status": "ready",
                    "temp:last_user_turn": "Yes, definitely. Face ID is working.",
                },
                session=SimpleNamespace(
                    state={
                        "app:user_id": user_id,
                        "app:session_id": session_id,
                        "temp:last_offer_amount": 87000,
                    }
                ),
                agent_name="valuation_agent",
            )
            llm_response = SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text=(
                                "Thanks for all that information. So, based on the video, "
                                "we see a blue iPhone XR. Your trade-in offer is ₦87,000."
                            )
                        )
                    ]
                )
            )

            await after_model_valuation_sanity(callback_context, llm_response)

            rewritten = llm_response.content.parts[0].text.lower()
            assert "red" in rewritten
            assert "blue" not in rewritten
            assert "₦87,000".lower() in rewritten or "ngn" in rewritten
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_after_model_rewrites_hand_you_over_booking_disclosure(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:tradein_fulfillment_phase": "booking_pending",
                "temp:last_offer_amount": 87000,
                "temp:last_user_turn": "Yes.",
                "temp:recent_customer_context": "Customer: Yes.",
            },
            agent_name="valuation_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        text="Great! I'll hand you over now to finalize the pickup details."
                    )
                ]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        rewritten = llm_response.content.parts[0].text.lower()
        assert "hand you over" not in rewritten
        assert "booking agent" not in rewritten
        assert "let me get the next step sorted for you now" in rewritten

    @pytest.mark.asyncio
    async def test_after_model_blocks_tradein_offer_while_background_analysis_is_running(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_offer_amount": 234000,
                "temp:background_vision_status": "running",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
            agent_name="valuation_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Based on the video, our offer is ₦234,000.")]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        rewritten = llm_response.content.parts[0].text
        assert "quote a trade-in price from guesswork" in rewritten.lower()
        assert "₦234,000" not in rewritten
        assert "which storage size would you like for the new phone" in rewritten.lower()

    @pytest.mark.asyncio
    async def test_after_model_blocks_tradein_offer_when_background_analysis_failed(self):
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_offer_amount": 234000,
                "temp:background_vision_status": "failed",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
            agent_name="valuation_agent",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Based on the video, our offer is ₦234,000.")]
            )
        )

        await after_model_valuation_sanity(callback_context, llm_response)

        rewritten = llm_response.content.parts[0].text
        assert "please resend the video or a few clear photos on whatsapp" in rewritten.lower()
        assert "₦234,000" not in rewritten

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
        assert result["status"] == "error"
        assert result["error"] == "already_on_callback"

    @pytest.mark.asyncio
    async def test_capability_guard_allows_request_callback_on_normal_session(self):
        tool = SimpleNamespace(name="request_callback")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-inbound-abc123",
                "app:capabilities": ["outbound_messaging"],
                "temp:last_user_turn": "Please call me back later.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(tool, {"reason": "Please call me back later."}, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_capability_guard_blocks_request_callback_without_user_intent(self):
        tool = SimpleNamespace(name="request_callback")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "wa-abc123",
                "app:capabilities": ["outbound_messaging"],
                "app:channel": "voice",
                "app:company_profile": {"name": "Ogabassey Gadgets"},
                "temp:last_user_turn": "",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(tool, {}, ctx)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert result["error"] == "callback_intent_required"
        assert "OPENING PHASE" in result["detail"]
        assert "Do NOT call any tools or transfer" in result["detail"]
        assert "Ogabassey Gadgets" in result["detail"]
        assert "How can I help you today?" in result["detail"]

    @pytest.mark.asyncio
    async def test_capability_guard_allows_request_callback_from_last_user_turn_intent(self):
        tool = SimpleNamespace(name="request_callback")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "wa-abc123",
                "app:capabilities": ["outbound_messaging"],
                "app:channel": "voice",
                "temp:last_user_turn": "Can you call me back please?",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(tool, {}, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_questionnaire_blocked_while_background_vision_running_from_registry(self):
        tool = SimpleNamespace(name="get_device_questionnaire_tool")
        user_id = "voice-user-tradein"
        session_id = "sip-tradein-pending"
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{"temp:background_vision_status": "running"},
        )
        ctx = SimpleNamespace(
            state={
                "app:session_id": session_id,
                "app:user_id": user_id,
                "app:capabilities": ["valuation_tradein"],
                "app:channel": "voice",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": "Customer: I want to swap my iPhone XR for an iPhone 14.",
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(tool, {"device_brand": "Apple"}, ctx)

        assert isinstance(result, dict)
        assert result["error"] == "vision_analysis_pending"
        clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_questionnaire_blocked_from_registry_recent_customer_context_when_latest_turn_is_only_ack(self):
        tool = SimpleNamespace(name="get_device_questionnaire_tool")
        user_id = "voice-user-tradein-registry-context"
        session_id = "sip-tradein-registry-context"
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{
                "temp:background_vision_status": "running",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
        )
        try:
            ctx = SimpleNamespace(
                state={
                    "app:session_id": session_id,
                    "app:user_id": user_id,
                    "app:capabilities": ["valuation_tradein"],
                    "app:channel": "voice",
                    "temp:last_user_turn": "Yes.",
                },
                session=SimpleNamespace(state={}),
                agent_name="valuation_agent",
            )

            result = await before_tool_capability_guard_and_log(
                tool,
                {"device_brand": "Apple"},
                ctx,
            )

            assert isinstance(result, dict)
            assert result["error"] == "vision_analysis_pending"
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_grade_and_value_blocked_while_media_handoff_pending(self):
        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-pending-two",
                "app:user_id": "voice-user-tradein-two",
                "app:capabilities": ["valuation_tradein"],
                "app:channel": "voice",
                "temp:vision_media_handoff_state": "pending",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": "Customer: I want to swap my iPhone XR for an iPhone 14.",
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"analysis": "{}", "questionnaire_answers": "{}", "retail_price": 1000},
            ctx,
        )

        assert isinstance(result, dict)
        assert result["error"] == "vision_analysis_pending"

    @pytest.mark.asyncio
    async def test_questionnaire_blocked_while_waiting_for_new_media_even_with_stale_analysis(self):
        tool = SimpleNamespace(name="get_device_questionnaire_tool")
        user_id = "voice-user-awaiting"
        session_id = "sip-tradein-awaiting"
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{"temp:background_vision_status": "awaiting_media"},
        )
        try:
            ctx = SimpleNamespace(
                state={
                    "app:session_id": session_id,
                    "app:user_id": user_id,
                    "app:capabilities": ["valuation_tradein"],
                    "app:channel": "voice",
                    "temp:last_user_turn": "I just sent the video.",
                    "temp:recent_customer_context": (
                        "Customer: I want to swap my iPhone XR for an iPhone 14.\n"
                        "Customer: I just sent the video."
                    ),
                    "temp:last_analysis": {
                        "device_name": "iPhone XR",
                        "brand": "Apple",
                        "condition": "Good",
                        "device_color": "blue",
                        "details": {"body": {"description": "Minor wear"}},
                    },
                },
                session=SimpleNamespace(
                    state={
                        "app:user_id": user_id,
                        "app:session_id": session_id,
                    }
                ),
                agent_name="valuation_agent",
            )

            result = await before_tool_capability_guard_and_log(
                tool,
                {"device_brand": "Apple"},
                ctx,
            )

            assert isinstance(result, dict)
            assert result["error"] == "vision_analysis_pending"
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_request_media_via_whatsapp_is_deduped_while_waiting_for_upload(self):
        tool = SimpleNamespace(name="request_media_via_whatsapp")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-media-dedupe",
                "app:user_id": "voice-user-media-dedupe",
                "app:capabilities": ["valuation_tradein"],
                "app:channel": "voice",
                "temp:last_media_request_status": "sent",
                "temp:background_vision_status": "awaiting_media",
                "temp:last_outbound_delivery_phone": "+2348012345678",
                "temp:last_user_turn": "Okay.",
                "temp:recent_customer_context": "Customer: I want to swap my iPhone XR for an iPhone 14.",
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {
                "reason": "trade_in_photo_requested",
                "summary": "Customer wants to swap an iPhone XR for an iPhone 14.",
            },
            ctx,
        )

        assert isinstance(result, dict)
        assert result["status"] == "sent"
        assert result["deduplicated"] is True

    @pytest.mark.asyncio
    async def test_request_media_via_whatsapp_resend_is_allowed_after_customer_reports_missing_message(self):
        tool = SimpleNamespace(name="request_media_via_whatsapp")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-media-resend",
                "app:user_id": "voice-user-media-resend",
                "app:capabilities": ["valuation_tradein"],
                "app:channel": "voice",
                "temp:last_media_request_status": "sent",
                "temp:background_vision_status": "awaiting_media",
                "temp:last_user_turn": "I didn't get the WhatsApp message, please resend it.",
                "temp:recent_customer_context": "Customer: I didn't get the WhatsApp message, please resend it.",
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {
                "reason": "trade_in_photo_requested",
                "summary": "Customer wants to swap an iPhone XR for an iPhone 14.",
            },
            ctx,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_search_catalog_blocked_while_background_analysis_running(self):
        tool = SimpleNamespace(name="search_catalog")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-search-blocked",
                "app:user_id": "voice-user-search-blocked",
                "app:capabilities": ["catalog_lookup", "valuation_tradein"],
                "app:channel": "voice",
                "temp:background_vision_status": "running",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"query": "iPhone 14 128GB"},
            ctx,
        )

        assert isinstance(result, dict)
        assert result["error"] == "vision_analysis_pending"

    @pytest.mark.asyncio
    async def test_search_catalog_blocked_while_questionnaire_incomplete(self):
        tool = SimpleNamespace(name="search_catalog")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-search-questionnaire",
                "app:user_id": "voice-user-search-questionnaire",
                "app:capabilities": ["catalog_lookup", "valuation_tradein"],
                "app:channel": "voice",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:tradein_questionnaire_state": {
                    "status": "in_progress",
                    "questions": [
                        {
                            "id": "water_damage",
                            "question": "Has the device ever been exposed to water damage?",
                            "type": "boolean",
                            "invert": False,
                        }
                    ],
                    "answers": {},
                    "current_index": 0,
                    "last_answer_source_text": "",
                    "omitted_question_ids": [],
                    "pending_completion_ack": False,
                    "catalog_cache": {},
                },
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "condition": "Good",
                },
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"query": "iPhone XR"},
            ctx,
        )

        assert isinstance(result, dict)
        assert result["error"] == "questionnaire_incomplete"
        assert "water damage" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_grade_and_value_uses_saved_questionnaire_answers(self):
        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-grade-answers",
                "app:user_id": "voice-user-grade-answers",
                "app:capabilities": ["valuation_tradein"],
                "app:channel": "voice",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:tradein_questionnaire_state": {
                    "status": "completed",
                    "questions": [],
                    "answers": {"battery_health_pct": 87, "water_damage": "no"},
                    "current_index": 0,
                    "last_answer_source_text": "No water damage and battery is 87%.",
                    "omitted_question_ids": [],
                    "pending_completion_ack": False,
                    "catalog_cache": {},
                },
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "condition": "Good",
                },
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )
        args = {"analysis": "{}", "retail_price": 1000}

        result = await before_tool_capability_guard_and_log(tool, args, ctx)

        assert result is None
        assert json.loads(args["questionnaire_answers"]) == {
            "battery_health_pct": 87,
            "water_damage": "no",
        }

    @pytest.mark.asyncio
    async def test_search_catalog_requires_completion_ack_before_pricing(self):
        tool = SimpleNamespace(name="search_catalog")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-completion-ack",
                "app:user_id": "voice-user-completion-ack",
                "app:capabilities": ["catalog_lookup", "valuation_tradein"],
                "app:channel": "voice",
                "temp:last_user_turn": "Yes, Face ID works.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:tradein_questionnaire_state": {
                    "status": "completed",
                    "questions": [],
                    "answers": {"biometric_not_working": "yes"},
                    "current_index": 0,
                    "last_answer_source_text": "Yes, Face ID works.",
                    "omitted_question_ids": [],
                    "pending_completion_ack": True,
                    "catalog_cache": {},
                },
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "condition": "Good",
                },
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(tool, {"query": "iPhone XR"}, ctx)

        assert isinstance(result, dict)
        assert result["error"] == "questionnaire_completion_ack_required"

    @pytest.mark.asyncio
    async def test_grade_and_value_allowed_once_tool_backed_analysis_is_ready(self):
        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = SimpleNamespace(
            state={
                "app:session_id": "sip-tradein-ready",
                "app:user_id": "voice-user-tradein-ready",
                "app:capabilities": ["valuation_tradein"],
                "app:channel": "voice",
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "condition": "Good",
                    "details": {"screen": "Minor wear"},
                },
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": "Customer: I want to swap my iPhone XR for an iPhone 14.",
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"analysis": "{}", "questionnaire_answers": "{}", "retail_price": 1000},
            ctx,
        )

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
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "What time do you close today?",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_voice_tradein_transfer_sets_bootstrap_for_normal_handoff(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        session = SimpleNamespace(state={})
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
            session=session,
            agent_name="ekaette_router",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"agent_name": "valuation_agent"},
            ctx,
        )

        assert result is None
        assert ctx.state["temp:pending_transfer_bootstrap_target_agent"] == "valuation_agent"
        assert ctx.state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_handoff"
        assert session.state["temp:pending_transfer_bootstrap_target_agent"] == "valuation_agent"

    @pytest.mark.asyncio
    async def test_voice_tradein_booking_transfer_sets_bootstrap_for_normal_handoff(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        session = SimpleNamespace(state={})
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:tradein_fulfillment_phase": "booking_pending",
                "temp:last_offer_amount": 168750,
                "temp:last_user_turn": "Okay, thank you very much. What's next?",
                "temp:recent_customer_context": (
                    "Customer: Yes, let's proceed.\n"
                    "Customer: Okay, thank you very much. What's next?"
                ),
            },
            session=session,
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool,
            {"agent_name": "booking_agent"},
            ctx,
        )

        assert result is None
        assert ctx.state["temp:pending_transfer_bootstrap_target_agent"] == "booking_agent"
        assert ctx.state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_booking_handoff"
        assert session.state["temp:pending_transfer_bootstrap_target_agent"] == "booking_agent"

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_opening_phase_complete_exists_only_in_session_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(
                state={
                    "temp:opening_phase_complete": True,
                    "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                }
            ),
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_recent_customer_context_exists_only_in_registry(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        user_id = "sip-user-ctx"
        session_id = "sip-session-ctx"
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
        )
        try:
            ctx = SimpleNamespace(
                state={
                    "app:channel": "voice",
                    "app:user_id": user_id,
                    "app:session_id": session_id,
                    "temp:opening_phase_complete": True,
                    "temp:last_user_turn": "1.5 million",
                },
                agent_name="ekaette_router",
            )
            result = await before_tool_capability_guard_and_log(
                tool, {"agent_name": "valuation_agent"}, ctx
            )
            assert result is None
            assert (
                ctx.state["temp:pending_handoff_recent_customer_context"]
                == "Customer: I want to swap my iPhone XR for an iPhone 14."
            )
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_first_user_turn_complete_exists_in_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:first_user_turn_complete": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_first_user_turn_complete_exists_only_in_session_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(
                state={
                    "temp:first_user_turn_complete": True,
                    "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                }
            ),
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_first_user_turn_started_and_greeted_in_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:first_user_turn_started": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_first_user_turn_started_and_greeted_exist_only_in_session_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(
                state={
                    "temp:greeted": True,
                    "temp:first_user_turn_started": True,
                    "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                }
            ),
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_blocked_when_latest_user_is_only_greeting(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Hello?",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert isinstance(result, dict)
        assert result["error"] == "explicit_request_required"

    @pytest.mark.asyncio
    async def test_transfer_blocked_when_latest_user_is_only_self_intro(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "My name is Akon.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert isinstance(result, dict)
        assert result["error"] == "explicit_request_required"

    @pytest.mark.asyncio
    async def test_transfer_blocked_when_latest_user_is_connection_check(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Can you hear me now?",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert isinstance(result, dict)
        assert result["error"] == "explicit_request_required"

    @pytest.mark.asyncio
    async def test_transfer_blocked_when_latest_user_requests_slower_repeat(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Please slow down and repeat that.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert isinstance(result, dict)
        assert result["error"] == "explicit_request_required"

    @pytest.mark.asyncio
    async def test_support_transfer_allowed_for_real_support_question(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Please help me track my order.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_recent_customer_context_carries_booking_intent(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Yes.",
                "temp:recent_customer_context": "Customer wants to book a pickup for tomorrow morning.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allows_booking_after_tradein_offer_acceptance(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_offer_amount": 168750,
                "temp:last_user_turn": "Yeah, you can proceed.",
                "temp:recent_customer_context": (
                    "Customer: Okay, so let's proceed with the swap then.\n"
                    "Customer: 128 GB.\n"
                    "Customer: Yeah, you can proceed."
                ),
            },
            agent_name="valuation_agent",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allows_booking_after_plain_yes_once_offer_exists(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_offer_amount": 87000,
                "temp:last_user_turn": "Yes.",
                "temp:recent_customer_context": (
                    "Customer: Perfect. So, to confirm, you're swapping for a used 128GB iPhone 14. "
                    "Your trade-in is worth ₦87,000.\n"
                    "Customer: Yes."
                ),
            },
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )

        assert result is None
        assert ctx.state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_booking_handoff"

    @pytest.mark.asyncio
    async def test_transfer_allows_booking_after_explicit_payment_intent_once_offer_exists(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_offer_amount": 165400,
                "temp:last_user_turn": "No, I want to make payment for the swap.",
                "temp:recent_customer_context": (
                    "Customer: The highest we can go is ₦165,400.\n"
                    "Customer: Yes.\n"
                    "Customer: No, I want to make payment for the swap."
                ),
            },
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )

        assert result is None
        assert ctx.state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_booking_handoff"

    @pytest.mark.asyncio
    async def test_transfer_allows_booking_after_asr_garbled_payment_when_recent_yes_exists(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_offer_amount": 165400,
                "temp:last_user_turn": "I want to play for each work.",
                "temp:recent_customer_context": (
                    "Customer: Your final offer is ₦165,400.\n"
                    "Customer: Yes.\n"
                    "Customer: I want to play for each work."
                ),
            },
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )

        assert result is None
        assert ctx.state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_booking_handoff"

    @pytest.mark.asyncio
    async def test_transfer_allows_booking_when_tradein_fulfillment_phase_is_pending(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:tradein_fulfillment_phase": "booking_pending",
                "temp:last_offer_amount": 168750,
                "temp:last_user_turn": "Okay, I am in Lagos Nigeria.",
                "temp:recent_customer_context": (
                    "Customer: Great, let's proceed.\n"
                    "Customer: My name is Bassey John.\n"
                    "Customer: Okay, I am in Lagos Nigeria."
                ),
            },
            agent_name="valuation_agent",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_allows_booking_when_offer_only_exists_in_voice_state_registry(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        user_id = "voice-booking-user"
        session_id = "voice-booking-session"
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:user_id": user_id,
                "app:session_id": session_id,
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Okay, thank you very much. What's next?",
                "temp:recent_customer_context": (
                    "Customer: Yes, let's proceed.\n"
                    "Customer: Okay, thank you very much. What's next?"
                ),
            },
            session=SimpleNamespace(
                state={
                    "app:user_id": user_id,
                    "app:session_id": session_id,
                }
            ),
            agent_name="valuation_agent",
        )
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{
                "temp:last_offer_amount": 168750,
                "temp:tradein_fulfillment_phase": "booking_pending",
            },
        )

        try:
            result = await before_tool_capability_guard_and_log(
                tool, {"agent_name": "booking_agent"}, ctx
            )
            assert result is None
            assert ctx.state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_booking_handoff"
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_transfer_retry_cap_suppresses_repeat_blocked_booking_handoff(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_offer_amount": 87000,
                "temp:last_user_turn": "Hello?",
                "temp:recent_customer_context": "Customer: Hello?",
            },
            actions=SimpleNamespace(transfer_to_agent="booking_agent"),
            agent_name="valuation_agent",
        )

        first = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )
        second = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )
        third = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "booking_agent"}, ctx
        )

        assert first["error"] == "explicit_request_required"
        assert second["error"] == "explicit_request_required"
        assert third["error"] == "routing_retry_suppressed"
        assert ctx.actions.transfer_to_agent is None

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_opening_phase_complete_exists_only_in_voice_state_registry(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:user_id": "voice-user-1",
                "app:session_id": "voice-session-1",
            },
            agent_name="ekaette_router",
        )
        update_voice_state(
            user_id="voice-user-1",
            session_id="voice-session-1",
            **{
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            },
        )
        try:
            result = await before_tool_capability_guard_and_log(
                tool, {"agent_name": "valuation_agent"}, ctx
            )
            assert result is None
        finally:
            clear_registered_voice_state(
                user_id="voice-user-1",
                session_id="voice-session-1",
            )

    @pytest.mark.asyncio
    async def test_tradein_transfer_allowed_when_latest_user_turn_exists_only_in_voice_state_registry(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:user_id": "voice-user-2",
                "app:session_id": "voice-session-2",
            },
            agent_name="ekaette_router",
        )
        update_voice_state(
            user_id="voice-user-2",
            session_id="voice-session-2",
            **{
                "temp:greeted": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14",
            },
        )
        try:
            result = await before_tool_capability_guard_and_log(
                tool, {"agent_name": "valuation_agent"}, ctx
            )
            assert result is None
        finally:
            clear_registered_voice_state(
                user_id="voice-user-2",
                session_id="voice-session-2",
            )

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_greeting_complete_and_last_user_turn_exists_in_session_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(
                state={
                    "temp:opening_greeting_complete": True,
                    "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                }
            ),
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_vision_transfer_blocked_when_pending_media_exists_in_voice_state_registry(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:user_id": "voice-user-vision",
                "app:session_id": "voice-session-vision",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Okay, I will send it to you.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my phone, my iPhone XR to an iPhone 14.\n"
                    "Customer: Okay, I will send it to you."
                ),
            },
            session=SimpleNamespace(
                state={
                    "app:user_id": "voice-user-vision",
                    "app:session_id": "voice-session-vision",
                }
            ),
            agent_name="valuation_agent",
        )
        update_voice_state(
            user_id="voice-user-vision",
            session_id="voice-session-vision",
            **{"temp:vision_media_handoff_state": "pending"},
        )
        try:
            result = await before_tool_capability_guard_and_log(
                tool, {"agent_name": "vision_agent"}, ctx
            )
            assert result is not None
            assert result["error"] == "canonical_background_vision_only"
        finally:
            clear_registered_voice_state(
                user_id="voice-user-vision",
                session_id="voice-session-vision",
            )

    @pytest.mark.asyncio
    async def test_vision_transfer_blocked_while_background_analysis_is_running(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Can you confirm the colour now?",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14.\n"
                    "Customer: Can you confirm the colour now?"
                ),
                "temp:background_vision_status": "running",
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "vision_agent"}, ctx
        )

        assert result is not None
        assert result["error"] == "canonical_background_vision_only"

    @pytest.mark.asyncio
    async def test_vision_transfer_blocked_when_canonical_analysis_is_ready(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Can you confirm the colour now?",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14.\n"
                    "Customer: Can you confirm the colour now?"
                ),
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "unknown",
                    "color_confidence": 0.0,
                    "condition": "Good",
                    "details": {"body": {"description": "Minor wear"}},
                },
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )

        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "vision_agent"}, ctx
        )

        assert result is not None
        assert result["error"] == "canonical_background_vision_only"

    @pytest.mark.asyncio
    async def test_transfer_allows_tradein_fast_path_after_greeting_even_without_opening_phase_complete(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:last_user_turn": "I want to swap my Samsung S10 for an iPhone 14.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_still_blocked_when_only_last_agent_turn_exists_in_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_agent_turn": "Hello, this is ehkaitay from Ogabassey Gadgets.",
            },
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert isinstance(result, dict)
        assert result["error"] == "greeting_required"

    @pytest.mark.asyncio
    async def test_transfer_still_blocked_when_only_last_agent_turn_exists_in_session_state(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(
                state={
                    "temp:last_agent_turn": "Hello, this is ehkaitay from Ogabassey Gadgets.",
                }
            ),
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "support_agent"}, ctx
        )
        assert isinstance(result, dict)
        assert result["error"] == "greeting_required"

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


class TestOptionEHydrationGuard:
    """Option E: session.state hydration ensures transfer guards see stream_tasks writes."""

    @pytest.mark.asyncio
    async def test_transfer_allowed_when_opening_complete_only_in_session_state(self):
        """Guard should allow transfer when session.state has opening_phase_complete,
        even if tool_context.state does not."""
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(state={
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            }),
            agent_name="ekaette_router",
        )
        result = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hallucinated_valuation_recovery_allowed_when_session_state_has_opening_flags(self):
        """Hallucinated sub-agent recovery should also benefit from hydration."""
        tool = SimpleNamespace(name="valuation_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(state={
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "I want to swap my Samsung S10 for an iPhone 14.",
            }),
            agent_name="ekaette_router",
            actions=SimpleNamespace(transfer_to_agent=None),
        )
        result = await on_tool_error_emit(tool, {}, ctx)
        # Should recover via transfer, not block with opening_phase_in_progress
        assert isinstance(result, dict)
        assert result.get("error") != "opening_phase_in_progress"
        assert result.get("error") != "routing_retry_suppressed"

    @pytest.mark.asyncio
    async def test_transfer_still_blocked_when_only_greeted_is_true(self):
        """Regression: greeted alone must NOT unlock transfers — strict guard preserved."""
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
        assert isinstance(result, dict)
        assert result["error"] in ("greeting_required", "routing_retry_suppressed")

    @pytest.mark.asyncio
    async def test_after_model_bridges_last_user_turn_from_session_state(self):
        """after_model should copy last_user_turn from session.state when ADK state lacks it."""
        callback_context = SimpleNamespace(
            state={
                "app:channel": "voice",
            },
            session=SimpleNamespace(state={
                "temp:last_user_turn": "I want to swap my iPhone XR for a 15 Pro Max.",
                "temp:first_user_turn_complete": True,
            }),
            agent_name="ekaette_router",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="Let me help you with that.")])
        )
        await after_model_valuation_sanity(callback_context, llm_response)
        assert callback_context.state.get("temp:last_user_turn") == "I want to swap my iPhone XR for a 15 Pro Max."
        assert callback_context.state.get("temp:first_user_turn_complete") is True

    @pytest.mark.asyncio
    async def test_hydration_overwrites_stale_string_values(self):
        """Verify that hydration replaces old strings in callback state with fresh ones from session."""
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "hello",  # stale value
            },
            session=SimpleNamespace(state={
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "I want to swap my phone",  # fresh value
            }),
            agent_name="ekaette_router",
        )
        # Calling the guard triggers hydration
        await before_tool_capability_guard_and_log(
            tool, {"agent_name": "valuation_agent"}, ctx
        )
        assert ctx.state["temp:last_user_turn"] == "I want to swap my phone"

    @pytest.mark.asyncio
    async def test_hydration_does_not_downgrade_booleans(self):
        """Verify that hydration only upgrades booleans (False->True) and never downgrades (True->False)."""
        from app.agents.callbacks import _hydrate_voice_opening_state_from_session
        state = {"temp:opening_phase_complete": True}
        session = SimpleNamespace(state={"temp:opening_phase_complete": False})
        
        _hydrate_voice_opening_state_from_session(state, session=session)
        
        # Should remain True
        assert state["temp:opening_phase_complete"] is True

    @pytest.mark.asyncio
    async def test_after_model_overwrites_stale_string_values(self):
        """Verify after_model bridge also treats session.state as canonical for strings."""
        callback_context = SimpleNamespace(
            state={
                "temp:last_user_turn": "stale",
            },
            session=SimpleNamespace(state={
                "temp:last_user_turn": "canonical",
            }),
            agent_name="ekaette_router",
        )
        llm_response = SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="hi")])
        )
        await after_model_valuation_sanity(callback_context, llm_response)
        assert callback_context.state["temp:last_user_turn"] == "canonical"


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
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone 14 Pro",
                    "brand": "Apple",
                    "device_color": "red",
                    "condition": "Good",
                    "power_state": "on",
                    "details": {"screen_condition": "clear"},
                },
            },
            agent_name="valuation_agent",
        )
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
        assert ctx.state["temp:pending_valuation_result_voice_ack"] == "ready"
        assert " in red" in ctx.state["temp:pending_valuation_result_voice_text"].lower()

    @pytest.mark.asyncio
    async def test_grade_and_value_tool_is_deduped_for_same_signature(self):
        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "red",
                    "condition": "Good",
                    "power_state": "on",
                    "details": {"screen_condition": "clear"},
                },
                "temp:tradein_questionnaire_state": {
                    "status": "in_progress",
                    "questions": [],
                    "answers": {"water_damage": "no"},
                    "current_index": 0,
                    "last_answer_source_text": "",
                    "omitted_question_ids": [],
                    "pending_completion_ack": False,
                    "catalog_cache": {},
                },
            },
            agent_name="valuation_agent",
        )
        args = {
            "questionnaire_answers": json.dumps({"water_damage": "no"}),
            "retail_price": 230000,
        }
        result = {
            "device_name": "iPhone XR",
            "grade": "Good",
            "offer_amount": 87000,
            "currency": "NGN",
            "summary": "Minor wear",
        }

        await after_tool_emit_messages(tool, args, ctx, result)
        deduped = await before_tool_capability_guard(tool, dict(args), ctx)

        assert isinstance(deduped, dict)
        assert deduped["deduplicated"] is True
        assert deduped["offer_amount"] == 87000

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

    @pytest.mark.asyncio
    async def test_request_media_via_whatsapp_marks_delivery_success(self):
        tool = SimpleNamespace(name="request_media_via_whatsapp")
        ctx = SimpleNamespace(state={}, agent_name="valuation_agent")
        result = {"status": "sent", "phone": "+2348012345678", "message_id": "wamid-1"}

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:last_media_request_status"] == "sent"
        assert ctx.state["temp:last_outbound_delivery_status"] == "success"
        assert ctx.state["temp:last_outbound_delivery_channels"] == "whatsapp"
        assert ctx.state["temp:last_outbound_delivery_phone"] == "+2348012345678"

    @pytest.mark.asyncio
    async def test_quote_failure_persists_clarify_route_state(self):
        tool = SimpleNamespace(name="get_topship_delivery_quote")
        session = SimpleNamespace(id="sess-1", state={})
        ctx = SimpleNamespace(
            state={"app:user_id": "user-1", "app:session_id": "sess-1"},
            session=session,
            agent_name="booking_agent",
        )
        result = {
            "status": "error",
            "code": "TOPSHIP_NO_QUOTES",
            "receiver_city": "Yaba, Lagos",
            "attempted_receiver_cities": ["Yaba, Lagos", "Yaba", "Lagos"],
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:last_delivery_quote_status"] == "clarify_route"
        assert ctx.state["temp:last_server_message"]["code"] == "TOPSHIP_NO_QUOTES"
        assert session.state["temp:last_delivery_quote_status"] == "clarify_route"
        assert session.state["temp:last_delivery_quote_details"]["receiver_city"] == "Yaba, Lagos"

    @pytest.mark.asyncio
    async def test_request_media_via_whatsapp_skips_runtime_ack_when_agent_already_said_sending(self):
        tool = SimpleNamespace(name="request_media_via_whatsapp")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:last_agent_turn": (
                    "One moment, I'm sending you a WhatsApp message now so you can reply there "
                    "with a quick video or a few photos of your device."
                ),
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )
        result = {
            "status": "sent",
            "phone": "+2348012345678",
            "message_id": "wamid-1",
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:last_media_request_status"] == "sent"
        assert ctx.state["temp:pending_media_request_voice_ack"] == ""

    @pytest.mark.asyncio
    async def test_request_media_via_whatsapp_deduplicated_result_does_not_reset_tradein_state(self):
        tool = SimpleNamespace(name="request_media_via_whatsapp")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {"device_name": "iPhone XR", "device_color": "red"},
                "temp:last_offer_amount": 234000,
            },
            session=SimpleNamespace(
                state={
                    "temp:last_analysis": {"device_name": "iPhone XR", "device_color": "red"},
                    "temp:last_offer_amount": 234000,
                }
            ),
            agent_name="valuation_agent",
        )
        result = {
            "status": "sent",
            "phone": "+2348012345678",
            "message_id": "wamid-1",
            "deduplicated": True,
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:last_media_request_status"] == "sent"
        assert ctx.state["temp:last_analysis"] == {"device_name": "iPhone XR", "device_color": "red"}
        assert ctx.state["temp:last_offer_amount"] == 234000
        assert ctx.session.state["temp:last_analysis"] == {
            "device_name": "iPhone XR",
            "device_color": "red",
        }

    @pytest.mark.asyncio
    async def test_request_media_via_whatsapp_marks_voice_tradein_media_pending(self):
        tool = SimpleNamespace(name="request_media_via_whatsapp")
        user_id = "voice-user-media-pending"
        session_id = "voice-session-media-pending"
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:user_id": user_id,
                "app:session_id": session_id,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": "Customer: I want to swap my iPhone XR for an iPhone 14.",
                "temp:last_analysis": {"device_name": "iPhone XR", "device_color": "blue"},
                "temp:last_offer_amount": 123000,
            },
            session=SimpleNamespace(
                state={
                    "app:user_id": user_id,
                    "app:session_id": session_id,
                    "temp:last_analysis": {"device_name": "iPhone XR", "device_color": "blue"},
                    "temp:last_offer_amount": 123000,
                }
            ),
            agent_name="valuation_agent",
        )
        result = {"status": "sent", "phone": "+2348012345678", "message_id": "wamid-1"}

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:last_media_request_status"] == "sent"
        assert ctx.state["temp:vision_media_handoff_state"] == "pending"
        assert ctx.state["temp:background_vision_status"] == "awaiting_media"
        assert ctx.state["temp:pending_media_request_voice_ack"] == "ready"
        assert ctx.state["temp:last_analysis"] == {}
        assert ctx.state["temp:last_offer_amount"] == 0
        assert ctx.session.state["temp:last_analysis"] == {}
        assert ctx.session.state["temp:last_offer_amount"] == 0
        assert ctx.session.state["temp:pending_media_request_voice_ack"] == "ready"
        registry_state = get_registered_voice_state(user_id=user_id, session_id=session_id)
        assert registry_state["temp:last_analysis"] == {}
        assert registry_state["temp:last_offer_amount"] == 0
        clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_grade_and_value_persists_offer_amount_into_voice_registry(self):
        tool = SimpleNamespace(name="grade_and_value_tool")
        user_id = "voice-user-offer"
        session_id = "voice-session-offer"
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "app:user_id": user_id,
                "app:session_id": session_id,
            },
            session=SimpleNamespace(
                state={
                    "app:user_id": user_id,
                    "app:session_id": session_id,
                }
            ),
            agent_name="valuation_agent",
        )
        result = {
            "device_name": "iPhone XR",
            "grade": "Good",
            "offer_amount": 234000,
            "currency": "NGN",
        }

        try:
            await after_tool_emit_messages(tool, {}, ctx, result)
            registry_state = get_registered_voice_state(
                user_id=user_id,
                session_id=session_id,
            )
            assert ctx.state["temp:last_offer_amount"] == 234000
            assert ctx.session.state["temp:last_offer_amount"] == 234000
            assert registry_state["temp:last_offer_amount"] == 234000
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_request_media_via_whatsapp_marks_delivery_failure(self):
        tool = SimpleNamespace(name="request_media_via_whatsapp")
        ctx = SimpleNamespace(state={}, agent_name="valuation_agent")
        result = {"status": "error", "detail": "delivery failed"}

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:last_media_request_status"] == "failure"
        assert ctx.state["temp:last_outbound_delivery_status"] == "failure"
        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "error"

    @pytest.mark.asyncio
    async def test_create_virtual_account_payment_without_notifications_does_not_mark_delivery_failure(self):
        tool = SimpleNamespace(name="create_virtual_account_payment")
        ctx = SimpleNamespace(state={}, agent_name="booking_agent")
        result = {
            "status": "ok",
            "notification_phone": "+2348012345678",
            "notifications_attempted": False,
            "sms_sent": False,
            "whatsapp_sent": False,
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert "temp:last_outbound_delivery_status" not in ctx.state

    @pytest.mark.asyncio
    async def test_blocks_booking_whatsapp_followup_without_explicit_customer_consent(self):
        tool = SimpleNamespace(name="send_whatsapp_message")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "yes",
                "temp:last_agent_turn": "Here are the account details for your transfer.",
            },
            session=SimpleNamespace(state={}),
            agent_name="booking_agent",
        )

        blocked = await before_tool_capability_guard(tool, {"text": "Account details"}, ctx)

        assert blocked is not None
        assert blocked["error"] == "written_followup_consent_required"

    @pytest.mark.asyncio
    async def test_allows_booking_whatsapp_followup_after_explicit_customer_consent(self):
        tool = SimpleNamespace(name="send_whatsapp_message")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "yes",
                "temp:last_agent_turn": "Would you like me to send the account details via WhatsApp?",
            },
            session=SimpleNamespace(state={}),
            agent_name="booking_agent",
        )

        blocked = await before_tool_capability_guard(tool, {"text": "Account details"}, ctx)

        assert blocked is None

    @pytest.mark.asyncio
    async def test_blocks_payment_creation_when_delivery_quote_needs_clarification(self):
        tool = SimpleNamespace(name="create_virtual_account_payment")
        session = SimpleNamespace(state={"temp:last_delivery_quote_status": "clarify_route"})
        ctx = SimpleNamespace(
            state={"app:channel": "voice"},
            session=session,
            agent_name="booking_agent",
        )

        blocked = await before_tool_capability_guard(
            tool,
            {
                "customer_email": "ada@example.com",
                "customer_first_name": "Ada",
                "customer_last_name": "Buyer",
            },
            ctx,
        )

        assert blocked is not None
        assert blocked["error"] == "delivery_quote_required"

    @pytest.mark.asyncio
    async def test_blocks_payment_creation_without_explicit_payment_intent(self):
        tool = SimpleNamespace(name="create_virtual_account_payment")
        session = SimpleNamespace(state={"temp:last_delivery_quote_status": "ready"})
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "yes",
                "temp:last_agent_turn": "Would you like delivery or pickup?",
            },
            session=session,
            agent_name="booking_agent",
        )

        blocked = await before_tool_capability_guard(
            tool,
            {
                "customer_email": "ada@example.com",
                "customer_first_name": "Ada",
                "customer_last_name": "Buyer",
            },
            ctx,
        )

        assert blocked is not None
        assert blocked["error"] == "payment_intent_required"

    @pytest.mark.asyncio
    async def test_dedupes_check_availability_for_same_request(self):
        tool = SimpleNamespace(name="check_availability")
        args = {"date": "2026-03-20", "location": "Yaba"}
        session = SimpleNamespace(state={})
        ctx = SimpleNamespace(
            state={"app:channel": "voice"},
            session=session,
            agent_name="booking_agent",
        )
        result = {"status": "ok", "slots": ["10:00"]}

        await after_tool_emit_messages(tool, args, ctx, result)
        deduped = await before_tool_capability_guard(tool, dict(args), ctx)

        assert deduped is not None
        assert deduped["deduplicated"] is True
        assert deduped["slots"] == ["10:00"]

    @pytest.mark.asyncio
    async def test_dedupes_payment_creation_for_same_request(self):
        tool = SimpleNamespace(name="create_virtual_account_payment")
        args = {
            "customer_email": "ada@example.com",
            "customer_first_name": "Ada",
            "customer_last_name": "Buyer",
            "expected_amount_kobo": 12500000,
        }
        session = SimpleNamespace(state={"temp:last_delivery_quote_status": "ready"})
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "I want to pay now",
                "temp:last_agent_turn": "Would you like me to generate the account details now?",
            },
            session=session,
            agent_name="booking_agent",
        )
        result = {
            "status": "ok",
            "payment_method": "virtual_account",
            "account_number": "1234567890",
            "notifications_attempted": False,
            "sms_sent": False,
            "whatsapp_sent": False,
        }

        await after_tool_emit_messages(tool, args, ctx, result)
        deduped = await before_tool_capability_guard(tool, dict(args), ctx)

        assert deduped is not None
        assert deduped["deduplicated"] is True
        assert deduped["payment_method"] == "virtual_account"

    @pytest.mark.asyncio
    async def test_end_call_queues_end_after_speaking_on_voice(self):
        tool = SimpleNamespace(name="end_call")
        ctx = SimpleNamespace(
            state={"app:channel": "voice"},
            agent_name="ekaette_router",
        )
        result = {"status": "ok", "reason": "goodbye_complete"}

        await after_tool_emit_messages(tool, {"reason": "goodbye_complete"}, ctx, result)

        message = ctx.state["temp:last_server_message"]
        assert message["type"] == "call_control"
        assert message["action"] == "end_after_speaking"
        assert message["reason"] == "goodbye_complete"

    @pytest.mark.asyncio
    async def test_end_call_does_not_queue_on_text_channel(self):
        tool = SimpleNamespace(name="end_call")
        ctx = SimpleNamespace(
            state={"app:channel": "text"},
            agent_name="ekaette_router",
        )
        result = {"status": "ok", "reason": "goodbye_complete"}

        await after_tool_emit_messages(tool, {"reason": "goodbye_complete"}, ctx, result)

        assert "temp:last_server_message" not in ctx.state


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
        assert message["nextQuestion"] == "Water damage?"
        questionnaire_state = ctx.state["temp:tradein_questionnaire_state"]
        assert questionnaire_state["status"] == "in_progress"
        assert questionnaire_state["questions"][0]["id"] == "water_damage"

    @pytest.mark.asyncio
    async def test_questionnaire_tool_sets_pending_voice_question_for_voice_channel(self):
        tool = SimpleNamespace(name="get_device_questionnaire_tool")
        ctx = SimpleNamespace(
            state={"app:channel": "voice"},
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )
        result = {
            "questions": [
                {
                    "id": "water_damage",
                    "question": "Has the device ever been exposed to water damage?",
                    "type": "boolean",
                    "invert": False,
                }
            ],
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:pending_questionnaire_voice_ack"] == "ready"
        assert (
            ctx.state["temp:pending_questionnaire_voice_text"]
            == "Has the device ever been exposed to water damage?"
        )

    @pytest.mark.asyncio
    async def test_search_catalog_result_cached_for_tradein_flow(self):
        tool = SimpleNamespace(name="search_catalog")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
                "temp:tradein_questionnaire_state": {
                    "status": "completed",
                    "questions": [],
                    "answers": {"battery_health_pct": 87},
                    "current_index": 0,
                    "last_answer_source_text": "Battery is 87%.",
                    "omitted_question_ids": [],
                    "pending_completion_ack": False,
                    "catalog_cache": {},
                },
            },
            session=SimpleNamespace(state={}),
            agent_name="valuation_agent",
        )
        result = {
            "products": [
                {
                    "name": "iPhone XR",
                    "price": 350000,
                    "currency": "NGN",
                    "in_stock": True,
                }
            ]
        }

        await after_tool_emit_messages(tool, {"query": "iPhone XR"}, ctx, result)

        questionnaire_state = ctx.state["temp:tradein_questionnaire_state"]
        assert "iphone xr" in next(iter(questionnaire_state["catalog_cache"].keys()))

    @pytest.mark.asyncio
    async def test_caches_power_state_from_vision_analysis(self):
        """Vision tool results should preserve visible power-state evidence for valuation."""
        tool = SimpleNamespace(name="analyze_device_image_tool")
        ctx = SimpleNamespace(
            state={
                "app:user_id": "voice-user-analysis",
                "app:session_id": "voice-session-analysis",
                "temp:vision_media_handoff_state": "transferring",
            },
            session=SimpleNamespace(
                state={
                    "app:user_id": "voice-user-analysis",
                    "app:session_id": "voice-session-analysis",
                    "temp:vision_media_handoff_state": "transferring",
                }
            ),
            agent_name="vision_agent",
        )
        result = {
            "device_name": "iPhone XR",
            "brand": "Apple",
            "device_color": "red",
            "color_confidence": 0.93,
            "condition": "Good",
            "power_state": "on",
            "details": {"functionality": "Display is on"},
        }

        await after_tool_emit_messages(tool, {}, ctx, result)

        assert ctx.state["temp:last_analysis"]["power_state"] == "on"
        assert ctx.state["temp:last_analysis"]["device_color"] == "red"
        assert ctx.state["temp:last_analysis"]["color_confidence"] == 0.93
        assert ctx.state["temp:vision_media_handoff_state"] == "consumed"
        assert ctx.session.state["temp:vision_media_handoff_state"] == "consumed"
        clear_registered_voice_state(
            user_id="voice-user-analysis",
            session_id="voice-session-analysis",
        )

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
        tool_context.state["app:channel"] = "voice"
        tool_context.state["temp:greeted"] = True
        tool_context.state["temp:opening_phase_complete"] = True
        err = ValueError("Tool 'catalog_agent' not found.")

        await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert tool_context.actions.transfer_to_agent == "catalog_agent"
        assert tool_context.state["temp:pending_handoff_target_agent"] == "catalog_agent"
        assert tool_context.state["temp:pending_handoff_latest_user"] == "Do you have iPhone 14?"

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_before_greeting_does_not_transfer(self):
        tool = BaseTool(name="catalog_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state["app:channel"] = "voice"
        err = ValueError("Tool 'catalog_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert result["error"] == "opening_phase_in_progress"
        assert tool_context.actions.transfer_to_agent is None
        assert "temp:pending_handoff_target_agent" not in tool_context.state

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_after_opening_phase_complete_transfers(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state["app:channel"] = "voice"
        tool_context.state["temp:opening_phase_complete"] = True
        tool_context.state["temp:last_user_turn"] = "I want to swap my iPhone XR for an iPhone 14."
        err = ValueError("Tool 'valuation_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert "transfer_to_agent" in result.get("hint", "")
        assert tool_context.actions.transfer_to_agent == "valuation_agent"
        assert tool_context.state["temp:pending_handoff_target_agent"] == "valuation_agent"

    @pytest.mark.asyncio
    async def test_hallucinated_tradein_recovery_sets_transfer_bootstrap(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        user_id = "voice-user-bootstrap"
        session_id = "sip-bootstrap-recovery"
        tool_context.state.update(
            {
                "app:channel": "voice",
                "app:user_id": user_id,
                "app:session_id": session_id,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            }
        )
        tool_context.session = SimpleNamespace(state={})
        err = ValueError("Tool 'valuation_agent' not found.")

        try:
            await on_tool_error_emit(
                tool=tool, args={}, tool_context=tool_context, error=err,
            )

            assert tool_context.state["temp:pending_transfer_bootstrap_target_agent"] == "valuation_agent"
            assert tool_context.state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_recovery"
            assert tool_context.session.state["temp:pending_transfer_bootstrap_target_agent"] == "valuation_agent"
            registry_state = get_registered_voice_state(user_id=user_id, session_id=session_id)
            assert registry_state["temp:pending_transfer_bootstrap_target_agent"] == "valuation_agent"
            assert registry_state["temp:pending_transfer_bootstrap_reason"] == "voice_tradein_recovery"
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_uses_registry_recent_customer_context_fallback(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        user_id = "sip-user-handoff"
        session_id = "sip-session-handoff"
        tool_context.state.update(
            {
                "app:channel": "voice",
                "app:user_id": user_id,
                "app:session_id": session_id,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Yes.",
            }
        )
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14."
                ),
            },
        )
        err = ValueError("Tool 'valuation_agent' not found.")

        try:
            result = await on_tool_error_emit(
                tool=tool, args={}, tool_context=tool_context, error=err,
            )
            assert isinstance(result, dict)
            assert "transfer_to_agent" in result.get("hint", "")
            assert tool_context.actions.transfer_to_agent == "valuation_agent"
            assert (
                tool_context.state["temp:pending_handoff_recent_customer_context"]
                == "Customer: I want to swap my iPhone XR for an iPhone 14."
            )
        finally:
            clear_registered_voice_state(user_id=user_id, session_id=session_id)

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_uses_session_opening_phase_complete_fallback(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state["app:channel"] = "voice"
        tool_context.state["temp:last_user_turn"] = "I want to swap my iPhone XR for an iPhone 14."
        tool_context.session = SimpleNamespace(state={"temp:opening_phase_complete": True})
        err = ValueError("Tool 'valuation_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert "transfer_to_agent" in result.get("hint", "")
        assert tool_context.actions.transfer_to_agent == "valuation_agent"

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_allows_transfer_after_first_user_turn_started(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state.update(
            {
                "app:channel": "voice",
                "temp:opening_greeting_complete": True,
                "temp:first_user_turn_started": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            }
        )
        err = ValueError("Tool 'valuation_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert "transfer_to_agent" in result.get("hint", "")
        assert tool_context.actions.transfer_to_agent == "valuation_agent"

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_allows_transfer_when_session_has_started_user_turn_and_greeted(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state["app:channel"] = "voice"
        tool_context.session = SimpleNamespace(
            state={
                "temp:greeted": True,
                "temp:first_user_turn_started": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            }
        )
        err = ValueError("Tool 'valuation_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert "transfer_to_agent" in result.get("hint", "")
        assert tool_context.actions.transfer_to_agent == "valuation_agent"

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_allows_transfer_after_first_user_turn_complete(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state.update(
            {
                "app:channel": "voice",
                "temp:first_user_turn_complete": True,
                "temp:last_user_turn": "I want to swap my iPhone XR for an iPhone 14.",
            }
        )
        err = ValueError("Tool 'valuation_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert "transfer_to_agent" in result.get("hint", "")
        assert tool_context.actions.transfer_to_agent == "valuation_agent"

    @pytest.mark.asyncio
    async def test_hallucinated_vision_agent_blocked_when_canonical_background_path_exists(self):
        tool = BaseTool(name="vision_agent", description="Tool not found")
        tool_context = self._make_tool_context(agent_name="valuation_agent")
        tool_context.state.update(
            {
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:opening_phase_complete": True,
                "temp:last_user_turn": "Can you confirm the colour now?",
                "temp:recent_customer_context": (
                    "Customer: I want to swap my iPhone XR for an iPhone 14.\n"
                    "Customer: Can you confirm the colour now?"
                ),
                "temp:background_vision_status": "ready",
                "temp:last_analysis": {
                    "device_name": "iPhone XR",
                    "brand": "Apple",
                    "device_color": "unknown",
                    "color_confidence": 0.0,
                    "condition": "Good",
                    "details": {"body": {"description": "Minor wear"}},
                },
            }
        )
        err = ValueError("Tool 'vision_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert result["error"] == "canonical_background_vision_only"
        assert tool_context.actions.transfer_to_agent is None

    @pytest.mark.asyncio
    async def test_hallucinated_valuation_agent_allows_tradein_fast_path_after_greeting(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state.update(
            {
                "app:channel": "voice",
                "temp:greeted": True,
                "temp:last_user_turn": "I want to swap my Samsung S10 for an iPhone 14.",
            }
        )
        err = ValueError("Tool 'valuation_agent' not found.")

        result = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(result, dict)
        assert "transfer_to_agent" in result.get("hint", "")
        assert tool_context.actions.transfer_to_agent == "valuation_agent"

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_never_bypasses_greeting_after_retries(self):
        tool = BaseTool(name="valuation_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state["app:channel"] = "voice"
        err = ValueError("Tool 'valuation_agent' not found.")

        first = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )
        assert isinstance(first, dict)
        assert first["error"] == "opening_phase_in_progress"
        assert tool_context.actions.transfer_to_agent is None
        assert "temp:pending_handoff_target_agent" not in tool_context.state

        for _ in range(2):
            result = await on_tool_error_emit(
                tool=tool, args={}, tool_context=tool_context, error=err,
            )
            assert isinstance(result, dict)
            assert result["error"] == "routing_retry_suppressed"
            assert tool_context.actions.transfer_to_agent is None
            assert "temp:pending_handoff_target_agent" not in tool_context.state
        assert not bool(tool_context.state.get("temp:greeted", False))

    @pytest.mark.asyncio
    async def test_hallucinated_agent_name_is_suppressed_after_first_same_turn_attempt(self):
        tool = BaseTool(name="catalog_agent", description="Tool not found")
        tool_context = self._make_tool_context()
        tool_context.state["app:channel"] = "voice"
        tool_context.state["temp:last_user_turn"] = "Do you have iPhone 14?"
        tool_context.state["temp:greeted"] = True
        tool_context.state["temp:opening_phase_complete"] = True
        err = ValueError("Tool 'catalog_agent' not found.")

        first = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )
        assert "transfer_to_agent" in first.get("hint", "")
        assert tool_context.actions.transfer_to_agent == "catalog_agent"

        tool_context.actions.transfer_to_agent = None
        second = await on_tool_error_emit(
            tool=tool, args={}, tool_context=tool_context, error=err,
        )

        assert isinstance(second, dict)
        assert second["error"] == "routing_retry_suppressed"
        assert tool_context.actions.transfer_to_agent is None

    @pytest.mark.asyncio
    async def test_transfer_retry_is_suppressed_after_first_same_turn_block(self):
        tool = SimpleNamespace(name="transfer_to_agent")
        ctx = SimpleNamespace(
            state={
                "app:channel": "voice",
                "temp:opening_greeting_complete": True,
            },
            agent_name="ekaette_router",
        )
        first = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "catalog_agent"}, ctx
        )
        second = await before_tool_capability_guard_and_log(
            tool, {"agent_name": "catalog_agent"}, ctx
        )

        assert isinstance(first, dict)
        assert first["error"] == "greeting_required"
        assert isinstance(second, dict)
        assert second["error"] == "routing_retry_suppressed"
