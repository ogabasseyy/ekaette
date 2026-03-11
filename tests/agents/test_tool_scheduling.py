"""Tests for live tool response scheduling patch."""

from contextlib import nullcontext
from types import SimpleNamespace

from google.genai import types

import app.agents.tool_scheduling as tool_scheduling


def _make_event(*, will_continue=None):
    return SimpleNamespace(
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(
                    function_response=SimpleNamespace(
                        name="transfer_to_agent",
                        scheduling=None,
                        will_continue=will_continue,
                    ),
                )
            ]
        ),
        actions=SimpleNamespace(transfer_to_agent=None),
    )


def _install_with_fake_module(monkeypatch, *, will_continue=None):
    fake_functions_module = SimpleNamespace()
    class FakeBaseLlmFlow:
        async def run_live(self, invocation_context):
            # Keep this as an async generator with no yielded items.
            return
            yield  # pragma: no cover

    original_run_live = FakeBaseLlmFlow.run_live

    fake_base_flow_module = SimpleNamespace(
        BaseLlmFlow=FakeBaseLlmFlow,
        asyncio=__import__("asyncio"),
        LlmRequest=SimpleNamespace,
        Event=SimpleNamespace(new_id=lambda: "evt-1"),
        Aclosing=lambda agen: agen,
        tracer=SimpleNamespace(start_as_current_span=lambda _name: nullcontext()),
        trace_send_data=lambda *args, **kwargs: None,
        DEFAULT_TRANSFER_AGENT_DELAY=1.0,
        DEFAULT_TASK_COMPLETION_DELAY=1.0,
        ConnectionClosed=RuntimeError,
        ConnectionClosedOK=RuntimeError,
        logger=SimpleNamespace(debug=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        types=SimpleNamespace(LiveConnectConfig=SimpleNamespace, SessionResumptionConfig=SimpleNamespace),
    )

    def _original_build_response_event(tool, function_result, tool_context, invocation_context):
        return _make_event(will_continue=will_continue)

    setattr(fake_functions_module, "__build_response_event", _original_build_response_event)
    monkeypatch.setattr(tool_scheduling, "_ORIGINAL_BUILD_RESPONSE_EVENT", None)
    monkeypatch.setattr(tool_scheduling, "_ORIGINAL_RUN_LIVE", None)
    monkeypatch.setattr(
        tool_scheduling.importlib,
        "import_module",
        lambda name: (
            fake_functions_module
            if name == "google.adk.flows.llm_flows.functions"
            else fake_base_flow_module
        ),
    )
    installed = tool_scheduling.install_tool_response_scheduling_patch()
    assert installed is True
    return fake_functions_module, fake_base_flow_module, original_run_live


def test_patch_does_not_schedule_blocking_tool_responses(monkeypatch):
    fake_module, _, _ = _install_with_fake_module(monkeypatch)
    tool = SimpleNamespace(name="grade_and_value_tool")

    event = fake_module.__build_response_event(tool, {}, None, None)
    scheduling = event.content.parts[0].function_response.scheduling
    assert scheduling is None


def test_patch_sets_silent_for_non_blocking_background_tools(monkeypatch):
    fake_module, _, _ = _install_with_fake_module(monkeypatch, will_continue=True)
    tool = SimpleNamespace(name="preload_memory")

    event = fake_module.__build_response_event(tool, {}, None, None)
    scheduling = event.content.parts[0].function_response.scheduling
    assert scheduling == types.FunctionResponseScheduling.SILENT


def test_patch_keeps_blocked_transfer_function_name(monkeypatch):
    fake_module, fake_base_flow_module, original_run_live = _install_with_fake_module(monkeypatch)
    tool = SimpleNamespace(name="transfer_to_agent")

    event = fake_module.__build_response_event(tool, {}, None, None)

    assert event.content.parts[0].function_response.name == "transfer_to_agent"
    assert fake_base_flow_module.BaseLlmFlow.run_live is not original_run_live
