"""ADK Bug #3395 dedup mitigation + token telemetry.

before_agent_callback: Prevents duplicate agent invocations that occur
when the Live API repeatedly transfers to the same sub-agent within a
short window (a known ADK issue with multi-agent + session resumption).

telemetry_after_agent: Logs cumulative token usage per-session for
cost tracking and debugging.
"""

import hashlib
import logging
import os
import time
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

logger = logging.getLogger(__name__)

# Seconds within which a repeated transfer to the same agent is suppressed.
DEDUP_COOLDOWN_SECONDS = 2.0

# Root agent name — never suppress the root router.
ROOT_AGENT_NAME = "ekaette_router"
TOKEN_PRICE_PROMPT_PER_MILLION = float(
    os.getenv("TOKEN_PRICE_PROMPT_PER_MILLION_USD", "0")
)
TOKEN_PRICE_COMPLETION_PER_MILLION = float(
    os.getenv("TOKEN_PRICE_COMPLETION_PER_MILLION_USD", "0")
)


def _content_fingerprint(content: Any) -> str:
    """Build a stable, text-first fingerprint from callback user content."""
    if content is None:
        return ""
    parts = getattr(content, "parts", None)
    if not parts:
        return str(content)

    chunks: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            chunks.append(f"text:{text.strip()}")
            continue

        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            fn_name = getattr(function_call, "name", "")
            fn_args = getattr(function_call, "args", {})
            chunks.append(f"call:{fn_name}:{fn_args}")
            continue

        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None:
            mime_type = getattr(inline_data, "mime_type", "")
            data = getattr(inline_data, "data", b"") or b""
            size = len(data) if isinstance(data, (bytes, bytearray)) else 0
            chunks.append(f"blob:{mime_type}:{size}")
            continue

    return "|".join(chunks)


def _content_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _extract_int(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def _state_int(state: Any, key: str, default: int = 0) -> int:
    value = state.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _state_float(state: Any, key: str, default: float = 0.0) -> float:
    value = state.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def dedup_before_agent(
    callback_context: CallbackContext,
) -> types.Content | None:
    """Suppress duplicate agent transfers within cooldown window.

    ADK Bug #3395: After N agent transfers + session resumption, the model
    can repeatedly transfer to the same sub-agent in a tight loop. This
    callback detects rapid re-invocations and returns a polite Content
    to short-circuit the loop.

    Returns:
        None to proceed normally, or Content to skip this agent invocation.
    """
    agent_name = callback_context.agent_name

    # Never suppress the root agent
    if agent_name == ROOT_AGENT_NAME:
        return None

    state = callback_context.state
    last_agent = state.get("temp:dedup_last_agent")
    last_ts = state.get("temp:dedup_last_ts")
    last_signature = state.get("temp:dedup_last_signature")
    last_turn_hash = state.get("temp:dedup_last_turn_hash")

    turn_fingerprint = _content_fingerprint(getattr(callback_context, "user_content", None))
    turn_hash = _content_hash(turn_fingerprint)
    signature = _content_hash(f"{agent_name}|{turn_hash}")

    now = time.time()

    # Check for duplicate: same signature within cooldown on same turn.
    if (
        isinstance(last_agent, str)
        and last_agent == agent_name
        and isinstance(last_signature, str)
        and last_signature == signature
        and isinstance(last_turn_hash, str)
        and last_turn_hash == turn_hash
        and isinstance(last_ts, (int, float))
        and (now - last_ts) < DEDUP_COOLDOWN_SECONDS
    ):
        logger.warning(
            "Dedup: suppressing repeated transfer to %s (%.1fs since last, signature=%s)",
            agent_name,
            now - last_ts,
            signature[:10],
        )
        return types.Content(
            role="model",
            parts=[types.Part(
                text="I'm already working on that. Let me continue where I left off."
            )],
        )

    # Record this invocation
    state["temp:dedup_last_agent"] = agent_name
    state["temp:dedup_last_ts"] = now
    state["temp:dedup_last_signature"] = signature
    state["temp:dedup_last_turn_hash"] = turn_hash

    return None


async def telemetry_after_agent(
    callback_context: CallbackContext,
) -> types.Content | None:
    """Log cumulative token usage from the latest agent events.

    Reads usage_metadata from recent session events and accumulates
    total tokens in session state for cost monitoring.

    Returns:
        Always None (never produces visible content).
    """
    state = callback_context.state
    session = callback_context.session

    events = getattr(session, "events", None)
    if not events:
        return None

    cursor_raw = state.get("temp:telemetry_event_cursor", 0)
    try:
        cursor = int(cursor_raw)
    except (TypeError, ValueError):
        cursor = 0
    if cursor < 0 or cursor > len(events):
        cursor = 0

    delta_prompt_tokens = 0
    delta_completion_tokens = 0
    delta_total_tokens = 0
    for event in events[cursor:]:
        usage = getattr(event, "usage_metadata", None)
        if usage is None:
            continue

        prompt_tokens = _extract_int(usage, "prompt_token_count", "prompt_tokens")
        completion_tokens = _extract_int(
            usage,
            "candidates_token_count",
            "completion_token_count",
            "completion_tokens",
        )
        total_tokens = _extract_int(usage, "total_token_count", "total_tokens")
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens

        delta_prompt_tokens += prompt_tokens
        delta_completion_tokens += completion_tokens
        delta_total_tokens += total_tokens

    state["temp:telemetry_event_cursor"] = len(events)
    if delta_total_tokens <= 0:
        return None

    session_prompt_tokens = _state_int(state, "temp:total_prompt_tokens", 0)
    session_completion_tokens = _state_int(state, "temp:total_completion_tokens", 0)
    session_total_tokens = _state_int(state, "temp:total_tokens", 0)
    session_cost_usd = _state_float(state, "temp:total_cost_usd", 0.0)

    session_prompt_tokens += delta_prompt_tokens
    session_completion_tokens += delta_completion_tokens
    session_total_tokens += delta_total_tokens

    delta_cost = (
        (delta_prompt_tokens / 1_000_000) * TOKEN_PRICE_PROMPT_PER_MILLION
        + (delta_completion_tokens / 1_000_000) * TOKEN_PRICE_COMPLETION_PER_MILLION
    )
    session_cost_usd += delta_cost

    state["temp:total_prompt_tokens"] = session_prompt_tokens
    state["temp:total_completion_tokens"] = session_completion_tokens
    state["temp:total_tokens"] = session_total_tokens
    state["temp:total_cost_usd"] = session_cost_usd

    logger.info(
        "Telemetry: agent=%s turn_prompt=%d turn_completion=%d turn_total=%d session_total=%d session_cost_usd=%.6f",
        callback_context.agent_name,
        delta_prompt_tokens,
        delta_completion_tokens,
        delta_total_tokens,
        session_total_tokens,
        session_cost_usd,
    )

    return None
