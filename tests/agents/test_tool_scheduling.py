"""Tests for live tool response scheduling patch."""

from types import SimpleNamespace

from google.genai import types

import app.agents.tool_scheduling as tool_scheduling


def _make_event(*, will_continue=None):
    return SimpleNamespace(
        content=SimpleNamespace(
            parts=[
                SimpleNamespace(
                    function_response=SimpleNamespace(
                        scheduling=None,
                        will_continue=will_continue,
                    ),
                )
            ]
        )
    )


def _install_with_fake_module(monkeypatch, *, will_continue=None):
    fake_module = SimpleNamespace()

    def _original_build_response_event(tool, function_result, tool_context, invocation_context):
        return _make_event(will_continue=will_continue)

    setattr(fake_module, "__build_response_event", _original_build_response_event)
    monkeypatch.setattr(tool_scheduling, "_ORIGINAL_BUILD_RESPONSE_EVENT", None)
    monkeypatch.setattr(
        tool_scheduling.importlib,
        "import_module",
        lambda _: fake_module,
    )
    installed = tool_scheduling.install_tool_response_scheduling_patch()
    assert installed is True
    return fake_module


def test_patch_does_not_schedule_blocking_tool_responses(monkeypatch):
    fake_module = _install_with_fake_module(monkeypatch)
    tool = SimpleNamespace(name="grade_and_value_tool")

    event = fake_module.__build_response_event(tool, {}, None, None)
    scheduling = event.content.parts[0].function_response.scheduling
    assert scheduling is None


def test_patch_sets_silent_for_non_blocking_background_tools(monkeypatch):
    fake_module = _install_with_fake_module(monkeypatch, will_continue=True)
    tool = SimpleNamespace(name="preload_memory")

    event = fake_module.__build_response_event(tool, {}, None, None)
    scheduling = event.content.parts[0].function_response.scheduling
    assert scheduling == types.FunctionResponseScheduling.SILENT
