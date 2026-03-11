"""Tests for Live transfer-close gating helpers."""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.tool_scheduling import (
    _event_requests_live_transfer_close,
    _event_requests_task_completion,
)


def _make_event(*, fn_name: str, transfer_to_agent: str | None = None):
    function_response = SimpleNamespace(name=fn_name, id="call-1")
    part = SimpleNamespace(function_response=function_response)
    content = SimpleNamespace(parts=[part])
    actions = SimpleNamespace(transfer_to_agent=transfer_to_agent)
    return SimpleNamespace(content=content, actions=actions)


class TestTransferCloseGate:
    def test_blocked_transfer_does_not_request_live_close(self):
        event = _make_event(fn_name="transfer_to_agent", transfer_to_agent=None)
        assert _event_requests_live_transfer_close(event) is False
        assert event.content.parts[0].function_response.name == "transfer_to_agent"

    def test_real_transfer_requests_live_close(self):
        event = _make_event(
            fn_name="transfer_to_agent",
            transfer_to_agent="support_agent",
        )
        assert _event_requests_live_transfer_close(event) is True

    def test_non_transfer_tools_do_not_request_live_close(self):
        event = _make_event(fn_name="catalog_lookup", transfer_to_agent=None)
        assert _event_requests_live_transfer_close(event) is False

    def test_recovery_transfer_requests_live_close_even_when_function_name_differs(self):
        event = _make_event(fn_name="catalog_agent", transfer_to_agent="catalog_agent")
        assert _event_requests_live_transfer_close(event) is True

    def test_empty_transfer_target_does_not_request_live_close(self):
        event = _make_event(fn_name="transfer_to_agent", transfer_to_agent="")
        assert _event_requests_live_transfer_close(event) is False

    def test_handles_no_content(self):
        event = SimpleNamespace(content=None, actions=SimpleNamespace(transfer_to_agent=None))
        assert _event_requests_live_transfer_close(event) is False

    def test_task_completed_detection(self):
        event = _make_event(fn_name="task_completed", transfer_to_agent=None)
        assert _event_requests_task_completion(event) is True

    def test_non_task_completed_detection(self):
        event = _make_event(fn_name="transfer_to_agent", transfer_to_agent=None)
        assert _event_requests_task_completion(event) is False
