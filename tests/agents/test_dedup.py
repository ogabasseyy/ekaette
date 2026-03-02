"""Tests for ADK Bug #3395 dedup mitigation — TDD for S11."""

from types import SimpleNamespace

import pytest

from google.genai import types


class TestDedupBeforeAgentCallback:
    """Test before_agent_callback that suppresses duplicate responses."""

    @pytest.mark.asyncio
    async def test_allows_first_response_through(self):
        """First invocation of an agent should always proceed (return None)."""
        from app.agents.dedup import dedup_before_agent

        callback_context = SimpleNamespace(
            agent_name="vision_agent",
            state={},
            session=SimpleNamespace(events=[]),
        )

        result = await dedup_before_agent(callback_context=callback_context)
        assert result is None  # None means "proceed normally"

    @pytest.mark.asyncio
    async def test_suppresses_duplicate_transfer(self):
        """Repeated rapid transfer to same agent should be suppressed."""
        from app.agents.dedup import dedup_before_agent

        user_content = types.Content(parts=[types.Part(text="Please check this iPhone")])
        state: dict[str, object] = {}
        callback_context = SimpleNamespace(
            agent_name="vision_agent",
            state=state,
            user_content=user_content,
            session=SimpleNamespace(events=[]),
        )

        first = await dedup_before_agent(callback_context=callback_context)
        assert first is None

        second = await dedup_before_agent(callback_context=callback_context)
        # Should return Content (skipping agent) because same agent + same turn was just invoked
        assert second is not None
        assert isinstance(second, types.Content)

    @pytest.mark.asyncio
    async def test_allows_different_agent(self):
        """Transfer to a different agent should always proceed."""
        from app.agents.dedup import dedup_before_agent

        state = {"temp:dedup_last_agent": "vision_agent", "temp:dedup_last_ts": 999999999999.0}
        callback_context = SimpleNamespace(
            agent_name="valuation_agent",  # Different agent
            state=state,
            user_content=types.Content(parts=[types.Part(text="Value this device")]),
            session=SimpleNamespace(events=[]),
        )

        result = await dedup_before_agent(callback_context=callback_context)
        assert result is None  # Different agent, should proceed

    @pytest.mark.asyncio
    async def test_allows_same_agent_after_cooldown(self):
        """Same agent should be allowed after cooldown period expires."""
        import time
        from app.agents.dedup import dedup_before_agent, DEDUP_COOLDOWN_SECONDS

        # Set last_ts to well in the past (beyond cooldown)
        old_ts = time.time() - DEDUP_COOLDOWN_SECONDS - 10
        state = {"temp:dedup_last_agent": "vision_agent", "temp:dedup_last_ts": old_ts}
        callback_context = SimpleNamespace(
            agent_name="vision_agent",
            state=state,
            user_content=types.Content(parts=[types.Part(text="Analyze this image")]),
            session=SimpleNamespace(events=[]),
        )

        result = await dedup_before_agent(callback_context=callback_context)
        assert result is None  # Cooldown expired, should proceed

    @pytest.mark.asyncio
    async def test_updates_state_on_proceed(self):
        """When allowing an agent, should update tracking state."""
        from app.agents.dedup import dedup_before_agent

        state: dict[str, object] = {}
        callback_context = SimpleNamespace(
            agent_name="booking_agent",
            state=state,
            user_content=types.Content(parts=[types.Part(text="Book pickup for tomorrow")]),
            session=SimpleNamespace(events=[]),
        )

        await dedup_before_agent(callback_context=callback_context)
        assert state.get("temp:dedup_last_agent") == "booking_agent"
        assert isinstance(state.get("temp:dedup_last_ts"), float)

    @pytest.mark.asyncio
    async def test_skips_root_agent(self):
        """Root agent should never be deduped."""
        from app.agents.dedup import dedup_before_agent

        state = {"temp:dedup_last_agent": "ekaette_router", "temp:dedup_last_ts": 999999999999.0}
        callback_context = SimpleNamespace(
            agent_name="ekaette_router",
            state=state,
            user_content=types.Content(parts=[types.Part(text="Hello")]),
            session=SimpleNamespace(events=[]),
        )

        result = await dedup_before_agent(callback_context=callback_context)
        assert result is None  # Root agent is never suppressed

    @pytest.mark.asyncio
    async def test_dedup_only_handles_dedup_not_isolation_policy(self):
        """Isolation policy is enforced elsewhere (callbacks), not in dedup."""
        from app.agents.dedup import dedup_before_agent

        state = {
            "app:industry_template_id": "hotel",
            "app:enabled_agents": ["booking_agent", "support_agent"],
        }
        callback_context = SimpleNamespace(
            agent_name="catalog_agent",
            state=state,
            user_content=types.Content(parts=[types.Part(text="I want a TV")]),
            session=SimpleNamespace(events=[]),
        )

        result = await dedup_before_agent(callback_context=callback_context)

        assert result is None


class TestTelemetryAfterAgentCallback:
    """Test token/cost telemetry logging."""

    @pytest.mark.asyncio
    async def test_logs_usage_metadata(self):
        """After-agent callback should record usage in state."""
        from app.agents.dedup import telemetry_after_agent

        state: dict[str, object] = {}
        callback_context = SimpleNamespace(
            agent_name="valuation_agent",
            state=state,
            session=SimpleNamespace(
                events=[
                    SimpleNamespace(
                        usage_metadata=SimpleNamespace(
                            prompt_token_count=100,
                            candidates_token_count=50,
                            total_token_count=150,
                        )
                    ),
                ]
            ),
        )

        result = await telemetry_after_agent(callback_context=callback_context)
        assert result is None  # Should not produce content
        assert state.get("temp:total_tokens", 0) > 0

    @pytest.mark.asyncio
    async def test_accumulates_tokens_across_calls(self):
        """Token counts should accumulate, not reset."""
        from app.agents.dedup import telemetry_after_agent

        state: dict[str, object] = {
            "temp:total_prompt_tokens": 100,
            "temp:total_completion_tokens": 50,
            "temp:total_tokens": 150,
        }
        callback_context = SimpleNamespace(
            agent_name="catalog_agent",
            state=state,
            session=SimpleNamespace(
                events=[
                    SimpleNamespace(
                        usage_metadata=SimpleNamespace(
                            prompt_token_count=50,
                            candidates_token_count=25,
                            total_token_count=75,
                        )
                    ),
                ]
            ),
        )

        await telemetry_after_agent(callback_context=callback_context)
        assert state["temp:total_prompt_tokens"] >= 150
        assert state["temp:total_completion_tokens"] >= 75
        assert state["temp:total_tokens"] >= 225

    @pytest.mark.asyncio
    async def test_does_not_recount_same_events_when_cursor_present(self):
        """Telemetry cursor should prevent counting old session events twice."""
        from app.agents.dedup import telemetry_after_agent

        event = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                candidates_token_count=5,
                total_token_count=15,
            )
        )
        state: dict[str, object] = {}
        callback_context = SimpleNamespace(
            agent_name="support_agent",
            state=state,
            session=SimpleNamespace(events=[event]),
        )

        await telemetry_after_agent(callback_context=callback_context)
        assert state["temp:total_tokens"] == 15
        assert state["temp:telemetry_event_cursor"] == 1

        await telemetry_after_agent(callback_context=callback_context)
        assert state["temp:total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_handles_missing_usage_metadata(self):
        """Should not crash when events lack usage_metadata."""
        from app.agents.dedup import telemetry_after_agent

        state: dict[str, object] = {}
        callback_context = SimpleNamespace(
            agent_name="support_agent",
            state=state,
            session=SimpleNamespace(
                events=[
                    SimpleNamespace(usage_metadata=None),
                ]
            ),
        )

        result = await telemetry_after_agent(callback_context=callback_context)
        assert result is None  # No crash
