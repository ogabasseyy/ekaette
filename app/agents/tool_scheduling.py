"""Live tool response scheduling patch for Gemini Live API."""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from google.genai import types

logger = logging.getLogger(__name__)

# One-time patch state (kept module-level for deterministic test monkeypatching).
_ORIGINAL_BUILD_RESPONSE_EVENT: Callable[..., Any] | None = None

# S11 scheduling matrix:
# - User-facing tool responses should speak when the model is idle.
# - Background tools should be silent.
TOOL_RESPONSE_SCHEDULING: dict[str, str] = {
    "analyze_device_image_tool": "WHEN_IDLE",
    "grade_and_value_tool": "WHEN_IDLE",
    "negotiate_tool": "WHEN_IDLE",
    "check_availability": "WHEN_IDLE",
    "create_booking": "WHEN_IDLE",
    "cancel_booking": "WHEN_IDLE",
    "search_catalog": "WHEN_IDLE",
    "get_topship_delivery_quote": "WHEN_IDLE",
    "create_virtual_account_payment": "WHEN_IDLE",
    "check_payment_status": "WHEN_IDLE",
    "get_virtual_account_record": "WHEN_IDLE",
    "create_order_record": "WHEN_IDLE",
    "track_order_delivery": "WHEN_IDLE",
    "send_order_review_followup": "WHEN_IDLE",
    "preload_memory": "SILENT",
}

def _to_scheduling_enum(value: str | None) -> types.FunctionResponseScheduling | None:
    if not value:
        return None
    enum_cls = getattr(types, "FunctionResponseScheduling", None)
    if enum_cls is None:
        return None
    try:
        return enum_cls[value]
    except KeyError:
        return None


def _apply_response_scheduling(event: Any, tool_name: str) -> None:
    scheduling = _to_scheduling_enum(TOOL_RESPONSE_SCHEDULING.get(tool_name))
    if scheduling is None:
        return

    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return

    for part in parts:
        function_response = getattr(part, "function_response", None)
        if function_response is None:
            continue
        function_response.scheduling = scheduling
        break


def install_tool_response_scheduling_patch() -> bool:
    """Install one-time patch to set FunctionResponse.scheduling by tool name."""
    global _ORIGINAL_BUILD_RESPONSE_EVENT
    if _ORIGINAL_BUILD_RESPONSE_EVENT is not None:
        return True

    try:
        functions_mod = importlib.import_module("google.adk.flows.llm_flows.functions")
        original = getattr(functions_mod, "__build_response_event", None)
        if not callable(original):
            logger.warning("Tool scheduling patch skipped: build response hook unavailable")
            return False
        _ORIGINAL_BUILD_RESPONSE_EVENT = original

        def _patched_build_response_event(
            tool: Any,
            function_result: dict[str, object],
            tool_context: Any,
            invocation_context: Any,
        ) -> Any:
            original_hook = _ORIGINAL_BUILD_RESPONSE_EVENT
            if original_hook is None:
                return None
            event = original_hook(tool, function_result, tool_context, invocation_context)
            tool_name = getattr(tool, "name", "")
            if isinstance(tool_name, str) and tool_name:
                _apply_response_scheduling(event, tool_name)
            return event

        setattr(functions_mod, "__build_response_event", _patched_build_response_event)
        logger.info("Installed live tool response scheduling patch")
        return True
    except Exception as exc:
        logger.warning("Failed to install live tool scheduling patch: %s", exc)
        return False
