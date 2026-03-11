"""Live tool response scheduling patch for Gemini Live API.

Also patches a critical ADK bug where blocked ``transfer_to_agent`` tool calls
crash the Live session. ADK's ``run_live`` closes the Live connection whenever
it sees a function response named ``transfer_to_agent``, even if
``before_tool_callback`` blocked the tool and no transfer action was set. We
patch that close condition at runtime so blocked transfers keep the original
function name and the session stays alive.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from google.genai import types

logger = logging.getLogger(__name__)

# One-time patch state (kept module-level for deterministic test monkeypatching).
_ORIGINAL_BUILD_RESPONSE_EVENT: Callable[..., Any] | None = None
_ORIGINAL_RUN_LIVE: Callable[..., Any] | None = None

# Gemini Live only documents FunctionResponse.scheduling for NON_BLOCKING
# function calls. Applying scheduling metadata to ordinary blocking tool
# responses has correlated with 1008 websocket closes on the preview
# native-audio stack, so keep this opt-in and effectively dormant unless a
# function response explicitly behaves like a streaming/non-blocking one.
TOOL_RESPONSE_SCHEDULING: dict[str, str] = {
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
        if getattr(function_response, "will_continue", None) is None:
            return
        function_response.scheduling = scheduling
        break


def _first_function_response(event: Any) -> Any | None:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return None
    return getattr(parts[0], "function_response", None)


def _event_requests_live_transfer_close(event: Any) -> bool:
    """Return True only for events that request a real agent transfer.

    Live transfers should be driven by ``event.actions.transfer_to_agent``,
    not by the raw function-response name alone. This keeps blocked
    ``transfer_to_agent`` calls alive while also allowing deterministic
    recovery paths (for example, hallucinated ``catalog_agent`` tool calls)
    to hand off by setting the action directly.
    """
    actions = getattr(event, "actions", None)
    return bool(actions and getattr(actions, "transfer_to_agent", None))


def _event_requests_task_completion(event: Any) -> bool:
    function_response = _first_function_response(event)
    return bool(
        function_response is not None
        and getattr(function_response, "name", "") == "task_completed"
    )


def install_tool_response_scheduling_patch() -> bool:
    """Install one-time patch to set FunctionResponse.scheduling by tool name."""
    global _ORIGINAL_BUILD_RESPONSE_EVENT, _ORIGINAL_RUN_LIVE
    if _ORIGINAL_BUILD_RESPONSE_EVENT is not None and _ORIGINAL_RUN_LIVE is not None:
        return True

    try:
        functions_mod = importlib.import_module("google.adk.flows.llm_flows.functions")
        base_flow_mod = importlib.import_module("google.adk.flows.llm_flows.base_llm_flow")
        original = getattr(functions_mod, "__build_response_event", None)
        base_flow_cls = getattr(base_flow_mod, "BaseLlmFlow", None)
        original_run_live = getattr(base_flow_cls, "run_live", None) if base_flow_cls else None
        if not callable(original) or not callable(original_run_live):
            logger.warning(
                "Tool scheduling patch skipped: required hooks unavailable "
                "(build_response=%s, run_live=%s)",
                callable(original),
                callable(original_run_live),
            )
            return False
        _ORIGINAL_BUILD_RESPONSE_EVENT = original
        _ORIGINAL_RUN_LIVE = original_run_live

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

        async def _patched_run_live(self: Any, invocation_context: Any) -> Any:
            llm_request = base_flow_mod.LlmRequest()
            event_id = base_flow_mod.Event.new_id()

            async with base_flow_mod.Aclosing(
                self._preprocess_async(invocation_context, llm_request)
            ) as agen:
                async for event in agen:
                    yield event
            if invocation_context.end_invocation:
                return

            llm = getattr(self, "_BaseLlmFlow__get_llm")(invocation_context)
            base_flow_mod.logger.debug(
                "Establishing live connection for agent: %s with llm request: %s",
                invocation_context.agent.name,
                llm_request,
            )

            attempt = 1
            while True:
                try:
                    if invocation_context.live_session_resumption_handle:
                        base_flow_mod.logger.info(
                            "Attempting to reconnect (Attempt %s)...", attempt
                        )
                        attempt += 1
                        if not llm_request.live_connect_config:
                            llm_request.live_connect_config = base_flow_mod.types.LiveConnectConfig()
                        if not llm_request.live_connect_config.session_resumption:
                            llm_request.live_connect_config.session_resumption = (
                                base_flow_mod.types.SessionResumptionConfig()
                            )
                        llm_request.live_connect_config.session_resumption.handle = (
                            invocation_context.live_session_resumption_handle
                        )
                        llm_request.live_connect_config.session_resumption.transparent = True

                    base_flow_mod.logger.info(
                        "Establishing live connection for agent: %s",
                        invocation_context.agent.name,
                    )
                    async with llm.connect(llm_request) as llm_connection:
                        if llm_request.contents:
                            with base_flow_mod.tracer.start_as_current_span("send_data"):
                                base_flow_mod.logger.debug(
                                    "Sending history to model: %s", llm_request.contents
                                )
                                await llm_connection.send_history(llm_request.contents)
                                base_flow_mod.trace_send_data(
                                    invocation_context, event_id, llm_request.contents
                                )

                        send_task = base_flow_mod.asyncio.create_task(
                            self._send_to_model(llm_connection, invocation_context)
                        )

                        try:
                            async with base_flow_mod.Aclosing(
                                self._receive_from_model(
                                    llm_connection,
                                    event_id,
                                    invocation_context,
                                    llm_request,
                                )
                            ) as agen:
                                async for event in agen:
                                    if not event:
                                        break
                                    base_flow_mod.logger.debug(
                                        "Receive new event: %s", event
                                    )
                                    yield event
                                    if event.get_function_responses():
                                        base_flow_mod.logger.debug(
                                            "Sending back last function response event: %s",
                                            event,
                                        )
                                        invocation_context.live_request_queue.send_content(
                                            event.content
                                        )
                                    if _event_requests_live_transfer_close(event):
                                        await base_flow_mod.asyncio.sleep(
                                            base_flow_mod.DEFAULT_TRANSFER_AGENT_DELAY
                                        )
                                        send_task.cancel()
                                        base_flow_mod.logger.debug("Closing live connection")
                                        await llm_connection.close()
                                        base_flow_mod.logger.debug("Live connection closed.")
                                        transfer_to_agent = event.actions.transfer_to_agent
                                        base_flow_mod.logger.debug(
                                            "Transferring to agent: %s",
                                            transfer_to_agent,
                                        )
                                        agent_to_run = self._get_agent_to_run(
                                            invocation_context, transfer_to_agent
                                        )
                                        async with base_flow_mod.Aclosing(
                                            agent_to_run.run_live(invocation_context)
                                        ) as child_agen:
                                            async for item in child_agen:
                                                yield item
                                        return
                                    if _event_requests_task_completion(event):
                                        await base_flow_mod.asyncio.sleep(
                                            base_flow_mod.DEFAULT_TASK_COMPLETION_DELAY
                                        )
                                        send_task.cancel()
                                        return
                        finally:
                            if not send_task.done():
                                send_task.cancel()
                            try:
                                await send_task
                            except base_flow_mod.asyncio.CancelledError:
                                pass
                except (
                    base_flow_mod.ConnectionClosed,
                    base_flow_mod.ConnectionClosedOK,
                ) as exc:
                    base_flow_mod.logger.error("Connection closed: %s.", exc)
                    raise
                except Exception as exc:
                    base_flow_mod.logger.error(
                        "An unexpected error occurred in live flow: %s",
                        exc,
                        exc_info=True,
                    )
                    raise

        setattr(functions_mod, "__build_response_event", _patched_build_response_event)
        setattr(base_flow_cls, "run_live", _patched_run_live)
        logger.info("Installed live tool response scheduling patch")
        return True
    except Exception as exc:
        logger.warning("Failed to install live tool scheduling patch: %s", exc)
        return False
