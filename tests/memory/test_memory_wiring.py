"""Tests for memory service wiring into Runner — TDD for S12.

Verifies that:
1. The router agent has PreloadMemoryTool in its tools
2. The after_agent_callback calls add_events_to_memory
3. Memory retrieval and storage work with InMemoryMemoryService
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class TestPreloadMemoryToolWiring:
    """PreloadMemoryTool should be in the router agent's tools."""

    def test_router_has_preload_memory_tool(self):
        from google.adk.tools.preload_memory_tool import PreloadMemoryTool
        from app.agents.ekaette_router.agent import ekaette_router

        tool_types = [type(t) for t in ekaette_router.tools]
        assert PreloadMemoryTool in tool_types

    def test_preload_memory_tool_has_process_llm_request(self):
        """PreloadMemoryTool should hook into LLM requests for memory injection."""
        from google.adk.tools.preload_memory_tool import PreloadMemoryTool

        tool = PreloadMemoryTool()
        assert hasattr(tool, "process_llm_request")


class TestAfterAgentMemorySave:
    """after_agent_callback should save conversation to memory."""

    @pytest.mark.asyncio
    async def test_callback_calls_add_events_to_memory(self):
        from app.agents.ekaette_router.agent import save_session_and_telemetry_callback

        events = [
            SimpleNamespace(text="Hello"),
            SimpleNamespace(text="How can I help?"),
        ]
        callback_context = SimpleNamespace(
            agent_name="ekaette_router",
            state={},
            session=SimpleNamespace(events=events),
            add_events_to_memory=AsyncMock(),
        )

        result = await save_session_and_telemetry_callback(callback_context)
        assert result is None
        # Memory write is scheduled as a background task (non-blocking).
        callback_context.add_events_to_memory.assert_not_awaited()
        await asyncio.sleep(0)
        callback_context.add_events_to_memory.assert_awaited_once()
        assert callback_context.state["temp:memory_event_cursor"] == len(events)
        assert callback_context.state["temp:memory_save_in_flight"] is False

    @pytest.mark.asyncio
    async def test_callback_does_not_crash_on_memory_error(self):
        """Memory save failure should not crash the callback."""
        from app.agents.ekaette_router.agent import save_session_and_telemetry_callback

        events = [SimpleNamespace(text="Hello")]
        callback_context = SimpleNamespace(
            agent_name="ekaette_router",
            state={},
            session=SimpleNamespace(events=events),
            add_events_to_memory=AsyncMock(side_effect=Exception("Memory unavailable")),
        )

        result = await save_session_and_telemetry_callback(callback_context)
        assert result is None  # Should not raise
        await asyncio.sleep(0)
        callback_context.add_events_to_memory.assert_awaited_once()
        assert callback_context.state["temp:memory_save_in_flight"] is False
        assert callback_context.state.get("temp:memory_event_cursor", 0) == 0

    @pytest.mark.asyncio
    async def test_callback_uses_cursor_to_avoid_duplicate_memory_saves(self):
        from app.agents.ekaette_router.agent import save_session_and_telemetry_callback

        events = [
            SimpleNamespace(text="one"),
            SimpleNamespace(text="two"),
            SimpleNamespace(text="three"),
        ]
        callback_context = SimpleNamespace(
            agent_name="ekaette_router",
            state={},
            session=SimpleNamespace(events=events),
            add_events_to_memory=AsyncMock(),
        )

        await save_session_and_telemetry_callback(callback_context)
        await asyncio.sleep(0)
        callback_context.add_events_to_memory.assert_awaited_once()

        # Second callback on unchanged event list should not re-save.
        await save_session_and_telemetry_callback(callback_context)
        await asyncio.sleep(0)
        callback_context.add_events_to_memory.assert_awaited_once()


class TestInMemoryMemoryServiceBasics:
    """InMemoryMemoryService should work for local dev/test."""

    @pytest.mark.asyncio
    async def test_search_returns_empty_initially(self):
        from google.adk.memory import InMemoryMemoryService

        service = InMemoryMemoryService()
        result = await service.search_memory(
            app_name="ekaette",
            user_id="test-user",
            query="previous trade-in",
        )
        assert result is not None
        assert hasattr(result, "memories")
        assert len(result.memories) == 0

    @pytest.mark.asyncio
    async def test_add_and_search_round_trip(self):
        """Events added to memory should be searchable."""
        from google.adk.memory import InMemoryMemoryService
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        service = InMemoryMemoryService()
        session_service = InMemorySessionService()

        # Create a session with events
        session = await session_service.create_session(
            app_name="ekaette",
            user_id="chidi",
            session_id="session-001",
        )

        # Simulate adding an event to the session
        from google.adk.events import Event

        event = Event(
            author="user",
            content=types.Content(
                role="user",
                parts=[types.Part(text="I want to trade in my iPhone 14 Pro")],
            ),
        )
        await session_service.append_event(session=session, event=event)

        # Re-fetch session with events
        session = await session_service.get_session(
            app_name="ekaette",
            user_id="chidi",
            session_id="session-001",
        )

        # Add session to memory
        await service.add_session_to_memory(session)

        # Search for the memory
        result = await service.search_memory(
            app_name="ekaette",
            user_id="chidi",
            query="iPhone trade",
        )
        assert len(result.memories) > 0

    @pytest.mark.asyncio
    async def test_memory_persists_across_sessions_for_same_user(self):
        """Memory retrieval should work in a later session for the same user_id."""
        from google.adk.events import Event
        from google.adk.memory import InMemoryMemoryService
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        service = InMemoryMemoryService()
        session_service = InMemorySessionService()

        first = await session_service.create_session(
            app_name="ekaette",
            user_id="chidi",
            session_id="session-a",
        )
        await session_service.append_event(
            session=first,
            event=Event(
                author="user",
                content=types.Content(
                    role="user",
                    parts=[types.Part(text="My name is Chidi and I traded an iPhone 14 Pro.")],
                ),
            ),
        )
        first = await session_service.get_session(
            app_name="ekaette",
            user_id="chidi",
            session_id="session-a",
        )
        await service.add_session_to_memory(first)

        # New session, same user.
        await session_service.create_session(
            app_name="ekaette",
            user_id="chidi",
            session_id="session-b",
        )
        result = await service.search_memory(
            app_name="ekaette",
            user_id="chidi",
            query="Chidi iPhone trade-in history",
        )
        assert len(result.memories) > 0
