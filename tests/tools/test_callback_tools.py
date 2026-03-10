"""Tests for callback request tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.api.v1.realtime.caller_phone_registry import (
    clear_registered_caller_phone,
    register_caller_phone,
)


class TestRequestCallbackTool:
    @pytest.mark.asyncio
    async def test_missing_caller_phone_returns_error(self):
        from app.tools.callback_tools import request_callback

        ctx = SimpleNamespace(state={})
        result = await request_callback("Call me back later", ctx)
        assert result["status"] == "error"
        assert "caller phone" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_queues_callback_for_session_caller(self):
        from app.tools.callback_tools import request_callback

        ctx = SimpleNamespace(
            state={
                "user:caller_phone": "+2348012345678",
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
            }
        )

        with patch("app.tools.callback_tools.service_voice.register_callback_request") as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            result = await request_callback("Low airtime", ctx)

        assert result["status"] == "pending"
        mock_register.assert_called_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_ai_request",
            reason="Low airtime",
            trigger_after_hangup=True,
        )

    @pytest.mark.asyncio
    async def test_service_error_is_exposed_as_tool_error(self):
        from app.tools.callback_tools import request_callback

        ctx = SimpleNamespace(
            state={
                "user:caller_phone": "+2348012345678",
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
            }
        )

        with patch("app.tools.callback_tools.service_voice.register_callback_request") as mock_register:
            mock_register.return_value = {
                "status": "error",
                "detail": "Could not queue callback request",
                "phone": "+2348012345678",
            }
            result = await request_callback("Low airtime", ctx)

        assert result["status"] == "error"
        assert result["error"] == "Could not queue callback request"

    @pytest.mark.asyncio
    async def test_uses_session_state_caller_phone_fallback(self):
        from app.tools.callback_tools import request_callback

        ctx = SimpleNamespace(
            state={
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
            },
            session=SimpleNamespace(
                state={
                    "user:caller_phone": "+2348012345678",
                }
            ),
        )

        with patch("app.tools.callback_tools.service_voice.register_callback_request") as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            result = await request_callback("Low airtime", ctx)

        assert result["status"] == "pending"
        mock_register.assert_called_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_ai_request",
            reason="Low airtime",
            trigger_after_hangup=True,
        )

    @pytest.mark.asyncio
    async def test_uses_runtime_registry_caller_phone_fallback(self):
        from app.tools.callback_tools import request_callback

        register_caller_phone(
            user_id="voice-user-1",
            session_id="session-1",
            caller_phone="+2348012345678",
        )
        ctx = SimpleNamespace(
            state={
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
            },
            session=SimpleNamespace(id="session-1", state={}),
            user_id="voice-user-1",
        )

        with patch("app.tools.callback_tools.service_voice.register_callback_request") as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            result = await request_callback("Low airtime", ctx)

        clear_registered_caller_phone(user_id="voice-user-1", session_id="session-1")
        assert result["status"] == "pending"
        mock_register.assert_called_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_ai_request",
            reason="Low airtime",
            trigger_after_hangup=True,
        )

    @pytest.mark.asyncio
    async def test_uses_registry_ids_from_state_when_context_ids_missing(self):
        from app.tools.callback_tools import request_callback

        register_caller_phone(
            user_id="voice-user-2",
            session_id="session-2",
            caller_phone="+2348012345678",
        )
        ctx = SimpleNamespace(
            state={
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
                "app:user_id": "voice-user-2",
                "app:session_id": "session-2",
            },
            session=SimpleNamespace(state={}),
        )

        with patch("app.tools.callback_tools.service_voice.register_callback_request") as mock_register:
            mock_register.return_value = {"status": "pending", "phone": "+2348012345678"}
            result = await request_callback("Low airtime", ctx)

        clear_registered_caller_phone(user_id="voice-user-2", session_id="session-2")
        assert result["status"] == "pending"
        mock_register.assert_called_once_with(
            phone="+2348012345678",
            tenant_id="public",
            company_id="ekaette-electronics",
            source="voice_ai_request",
            reason="Low airtime",
            trigger_after_hangup=True,
        )


class TestBeforeToolCallerPhoneInjection:
    """Verify that the before_tool_callback injects caller phone from the
    ephemeral registry into tool_context.state when session state lacks it."""

    @pytest.mark.asyncio
    async def test_injects_caller_phone_for_request_callback(self):
        """When tool_context.state lacks user:caller_phone but the ephemeral
        registry has the phone, before_tool should inject it."""
        from app.agents.callbacks import before_tool_capability_guard_and_log

        register_caller_phone(
            user_id="sip-user-1",
            session_id="sip-session-1",
            caller_phone="+2349169449282",
        )
        tool = SimpleNamespace(name="request_callback")
        state = {
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
            "app:capabilities": ["outbound_messaging"],
            "app:user_id": "sip-user-1",
            "app:session_id": "sip-session-1",
        }
        ctx = SimpleNamespace(state=state, agent_name="ekaette_router")

        result = await before_tool_capability_guard_and_log(tool, {}, ctx)

        clear_registered_caller_phone(user_id="sip-user-1", session_id="sip-session-1")
        assert result is None  # tool was not blocked
        assert state.get("user:caller_phone") == "+2349169449282"

    @pytest.mark.asyncio
    async def test_injects_caller_phone_for_send_sms(self):
        """Same injection should work for send_sms_message."""
        from app.agents.callbacks import before_tool_capability_guard_and_log

        register_caller_phone(
            user_id="sip-user-2",
            session_id="sip-session-2",
            caller_phone="+2349169449282",
        )
        tool = SimpleNamespace(name="send_sms_message")
        state = {
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
            "app:capabilities": ["outbound_messaging"],
            "app:user_id": "sip-user-2",
            "app:session_id": "sip-session-2",
        }
        ctx = SimpleNamespace(state=state, agent_name="ekaette_router")

        result = await before_tool_capability_guard_and_log(tool, {}, ctx)

        clear_registered_caller_phone(user_id="sip-user-2", session_id="sip-session-2")
        assert result is None
        assert state.get("user:caller_phone") == "+2349169449282"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_caller_phone(self):
        """If state already has caller_phone, injection must not overwrite."""
        from app.agents.callbacks import before_tool_capability_guard_and_log

        register_caller_phone(
            user_id="sip-user-3",
            session_id="sip-session-3",
            caller_phone="+2340000000000",
        )
        tool = SimpleNamespace(name="request_callback")
        state = {
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
            "app:capabilities": ["outbound_messaging"],
            "app:user_id": "sip-user-3",
            "app:session_id": "sip-session-3",
            "user:caller_phone": "+2348012345678",
        }
        ctx = SimpleNamespace(state=state, agent_name="ekaette_router")

        await before_tool_capability_guard_and_log(tool, {}, ctx)

        clear_registered_caller_phone(user_id="sip-user-3", session_id="sip-session-3")
        assert state["user:caller_phone"] == "+2348012345678"

    @pytest.mark.asyncio
    async def test_injects_from_context_ids_when_state_ids_missing(self):
        """Live ADK contexts sometimes omit app:user_id/app:session_id in state."""
        from app.agents.callbacks import before_tool_capability_guard_and_log

        register_caller_phone(
            user_id="sip-user-4",
            session_id="sip-session-4",
            caller_phone="+2349169449282",
        )
        tool = SimpleNamespace(name="request_callback")
        state = {
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
            "app:capabilities": ["outbound_messaging"],
        }
        ctx = SimpleNamespace(
            state=state,
            agent_name="ekaette_router",
            user_id="sip-user-4",
            session=SimpleNamespace(id="sip-session-4", state={}),
        )

        result = await before_tool_capability_guard_and_log(tool, {}, ctx)

        clear_registered_caller_phone(user_id="sip-user-4", session_id="sip-session-4")
        assert result is None
        assert state.get("user:caller_phone") == "+2349169449282"


class TestAgentCallbackPromiseDetection:
    """Verify that agent output transcriptions containing callback promises
    are detected by looks_like_callback_promise."""

    def test_detects_ill_call_you_back(self):
        from app.agents.callbacks import looks_like_callback_promise

        assert looks_like_callback_promise("I'll call you back right away")

    def test_detects_i_will_call_you_back(self):
        from app.agents.callbacks import looks_like_callback_promise

        assert looks_like_callback_promise("I will call you back shortly")

    def test_detects_arrange_a_callback(self):
        from app.agents.callbacks import looks_like_callback_promise

        assert looks_like_callback_promise(
            "I can certainly arrange a callback for you"
        )

    def test_detects_request_a_callback(self):
        from app.agents.callbacks import looks_like_callback_promise

        assert looks_like_callback_promise(
            "I'll request a callback for you right after this call"
        )

    def test_detects_when_i_call_back_follow_up(self):
        from app.agents.callbacks import looks_like_callback_promise

        assert looks_like_callback_promise(
            "What was the main thing you wanted to discuss when I call back?"
        )

    def test_does_not_match_unrelated_text(self):
        from app.agents.callbacks import looks_like_callback_promise

        assert not looks_like_callback_promise("How can I help you today?")

    def test_does_not_match_empty_or_dot(self):
        from app.agents.callbacks import looks_like_callback_promise

        assert not looks_like_callback_promise("")
        assert not looks_like_callback_promise(".")


class TestUserCallbackRequestDetection:
    def test_detects_explicit_call_me_back(self):
        from app.agents.callbacks import looks_like_callback_request

        assert looks_like_callback_request("Can you call me back please?")

    def test_detects_noisy_call_me_bug_as_callback(self):
        from app.agents.callbacks import looks_like_callback_request

        assert looks_like_callback_request("you call me bug.")

    def test_detects_dont_have_time_as_callback_need(self):
        from app.agents.callbacks import looks_like_callback_request

        assert looks_like_callback_request("I don't have a time.")

    def test_does_not_match_regular_product_request(self):
        from app.agents.callbacks import looks_like_callback_request

        assert not looks_like_callback_request("I want the iPhone 14 128GB.")
