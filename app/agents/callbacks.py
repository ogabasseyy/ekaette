"""Shared ADK callbacks for model/tool lifecycle and structured events."""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions.state import State
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from app.api.v1.at import service_voice
from app.api.v1.realtime.voice_state_registry import (
    VOICE_STATE_BOOL_KEYS,
    VOICE_STATE_INT_KEYS,
    VOICE_STATE_JSON_KEYS,
    VOICE_STATE_KEYS,
    VOICE_STATE_STR_KEYS,
    get_registered_voice_state,
    update_voice_state,
)
from app.agents.dedup import dedup_before_agent
from app.configs.agent_policy import (
    KNOWN_SUB_AGENT_NAMES,
    resolve_enabled_agents_from_state,
)
from app.tools.sms_messaging import resolve_caller_phone_from_context
from app.tools.global_lessons import format_lessons_for_instruction

logger = logging.getLogger(__name__)

_INSTRUCTION_STATE_DEFAULTS: dict[str, Any] = {
    "temp:vision_media_handoff_state": "",
    "temp:background_vision_status": "",
    "temp:pending_handoff_target_agent": "",
    "temp:pending_handoff_latest_user": "",
    "temp:pending_handoff_latest_agent": "",
    "temp:pending_handoff_recent_customer_context": "",
}

_PRICE_PATTERN = re.compile(r"\b\d[\d,]{2,}\b")
_STORAGE_PATTERN = re.compile(r"\b\d+(?:gb|tb)\b", flags=re.IGNORECASE)
_OFFER_RESPONSE_PATTERN = re.compile(
    r"\b("
    r"offer|final offer|trade(?:\s|-)?in value|worth|valuation|best offer|"
    r"you(?:'d| would)? pay|pay on top|price it at"
    r")\b",
    re.IGNORECASE,
)
_DEVICE_BRAND_PATTERN = re.compile(
    r"\b(?:iphone|samsung|galaxy|pixel|redmi|tecno|infinix|itel|xiaomi|nokia|oppo|vivo|huawei)\b",
    re.IGNORECASE,
)
_SWAP_SIDE_GENERIC_TOKENS = frozenset({
    "a",
    "an",
    "and",
    "brand",
    "certified",
    "current",
    "device",
    "for",
    "from",
    "my",
    "new",
    "old",
    "one",
    "ones",
    "owned",
    "phone",
    "pre",
    "that",
    "the",
    "this",
    "to",
    "used",
})
_CALLBACK_REQUEST_PATTERNS = (
    re.compile(r"\bcall(?:ing)?\s+(?:me\s+)?back\b", re.IGNORECASE),
    re.compile(r"\bcallback\b", re.IGNORECASE),
    re.compile(r"\b(?:can|could|would|will)\s+you\s+call\s+me(?:\s+\w+)?\b", re.IGNORECASE),
    re.compile(r"\bplease\s+call\s+me(?:\s+\w+)?\b", re.IGNORECASE),
    re.compile(r"\byou\s+call\s+me(?:\s+\w+)?\b", re.IGNORECASE),
    re.compile(r"\blow(?:\s+on)?\s+airtime\b", re.IGNORECASE),
    re.compile(r"\b(?:no|not enough)\s+airtime\b", re.IGNORECASE),
    re.compile(r"\bdon(?:'|’)t\s+have\s+(?:enough\s+)?airtime\b", re.IGNORECASE),
    re.compile(r"\bdon(?:'|’)t\s+have\s+(?:the\s+|a\s+)?time\b", re.IGNORECASE),
)
_CALLBACK_PROMISE_PATTERNS = (
    re.compile(r"\bi(?:'| wi)?ll call you back\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll call back\b", re.IGNORECASE),
    re.compile(r"\bi can call you back\b", re.IGNORECASE),
    re.compile(r"\blet me call you back\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll make sure to call you back\b", re.IGNORECASE),
    re.compile(r"\bi can (?:certainly )?arrange a callback\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll (?:arrange|schedule) a callback\b", re.IGNORECASE),
    re.compile(r"\bi(?:'| wi)?ll request a callback\b", re.IGNORECASE),
    re.compile(r"\bwe(?:'| wi)?ll give you a call back\b", re.IGNORECASE),
    re.compile(r"\bcall you back shortly\b", re.IGNORECASE),
    re.compile(r"\bcall you back on this same number\b", re.IGNORECASE),
    re.compile(r"\brequest a callback for you right after this\b", re.IGNORECASE),
    re.compile(r"\bwhen i call back\b", re.IGNORECASE),
)
_WHATSAPP_DELIVERY_CLAIM_PATTERNS = (
    re.compile(r"\b(?:i(?:'|’)ve|i have|i just|we(?:'|’)ve|we have)\s+sent\b", re.IGNORECASE),
    re.compile(r"\bcheck\s+(?:your\s+)?whatsapp\b", re.IGNORECASE),
    re.compile(r"\bthe\s+message\s+(?:has\s+been|is)\s+sent\b", re.IGNORECASE),
    re.compile(r"\byou\s+should\s+(?:have|see)\s+it\s+on\s+whatsapp\b", re.IGNORECASE),
    re.compile(r"\bit(?:'|’)s\s+on\s+whatsapp\b", re.IGNORECASE),
)
_GREETING_ONLY_PATTERN = re.compile(
    r"^\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|yo)\s*[!.?]*\s*$",
    re.IGNORECASE,
)
_SELF_INTRO_ONLY_PATTERN = re.compile(
    r"^\s*(?:my name is|this is)\s+[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,3}\s*[!.?]*\s*$",
    re.IGNORECASE,
)
_ACK_ONLY_PATTERN = re.compile(
    r"^\s*(?:yes|yeah|yep|no|nope|okay|ok|alright|all right|sure|mm+h+m*|uh-huh)\s*[!.?]*\s*$",
    re.IGNORECASE,
)
_VOICE_REPAIR_PATTERN = re.compile(
    r"\b("
    r"can you hear me(?: now)?|are you there|i can't hear you|you can't hear me|"
    r"line is breaking|line is cutting|call is breaking|call is cutting|"
    r"network is bad|bad network|poor network|"
    r"too fast|slow down|speak slower|don't talk so fast|do not talk so fast|"
    r"repeat that|repeat yourself|say that again|come again|pardon|"
    r"i didn't catch that|i did not catch that|what did you say|"
    r"speak up|talk louder"
    r")\b",
    re.IGNORECASE,
)
_GENERIC_REQUEST_PATTERN = re.compile(
    r"\b("
    r"what|when|where|which|who|how|why|do|does|did|can|could|would|will|is|are|"
    r"help|issue|problem|broken|not working|repair|fix|return|refund|warranty|policy|"
    r"deliver|delivery|shipping|payment|paid|order|track|support|book|booking|schedule|"
    r"pickup|drop[- ]?off|price|cost|available|availability|stock|buy|sell|swap|trade(?:\s|-)?in|upgrade"
    r")\b",
    re.IGNORECASE,
)
_VALUATION_REQUEST_PATTERN = re.compile(
    r"\b("
    r"swap|trade(?:\s|-)?in|upgrade|exchange|sell|offer|value|valuation|worth|"
    r"condition|battery|screen|crack|scratch|damage|broken|repair|power on|"
    r"photo|video|image|picture"
    r")\b",
    re.IGNORECASE,
)
_CATALOG_REQUEST_PATTERN = re.compile(
    r"\b("
    r"do you have|have you got|available|availability|in stock|stock|price|cost|"
    r"how much|buy|looking for|want to buy|storage|spec|specification|compare|difference|gb|tb"
    r")\b",
    re.IGNORECASE,
)
_BOOKING_REQUEST_PATTERN = re.compile(
    r"\b("
    r"book|booking|schedule|appointment|pickup|drop[- ]?off|reserve|reservation|"
    r"visit|come in|tomorrow|today|time slot"
    r")\b",
    re.IGNORECASE,
)
_BOOKING_PROGRESS_PATTERN = re.compile(
    r"\b("
    r"proceed|go ahead|move ahead|continue|accept|accepted|"
    r"let(?:'|’)s proceed|let us proceed|let(?:'|’)s do it|"
    r"i(?:'|’)ll take it|i will take it|finali[sz]e|complete(?: the)? order|"
    r"confirm(?: the)? order|take the offer|take it"
    r")\b",
    re.IGNORECASE,
)
_BOOKING_CONTEXT_PATTERN = re.compile(
    r"\b("
    r"offer|swap|trade(?:\s|-)?in|upgrade|pickup|delivery|payment|pay|"
    r"order|account|price|quote|booking|appointment"
    r")\b",
    re.IGNORECASE,
)
_VISION_REQUEST_PATTERN = re.compile(
    r"\b("
    r"photo|video|image|picture|look at|see|visible|what model|which model|"
    r"what phone|what device|what color|colour|crack|scratch|damage|identify"
    r")\b",
    re.IGNORECASE,
)
_SUPPORT_REQUEST_PATTERN = re.compile(
    r"\b("
    r"track|tracking|order|delivery|shipping|return|refund|warranty|policy|"
    r"repair|fix|issue|problem|not working|complaint|support|help with my order|"
    r"compare|comparison|difference|better|camera|battery|feature|spec|specification|"
    r"location|address|contact|phone number|opening hours|store hours|"
    r"what time do you close|when do you close|what time do you open|when do you open|"
    r"close today|open today"
    r")\b",
    re.IGNORECASE,
)
_VISIBLE_CONDITION_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:describe|tell me about|walk me through)\b.{0,60}\b("
        r"condition|colour|color|screen|body|back|front|appearance|cosmetic"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what(?:'s| is)|how(?:'s| is))\b.{0,40}\b("
        r"condition|colour|color|screen|body|back|front"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:is there|does it have|do you see|can you see)\b.{0,40}\b("
        r"crack|scratch|dent|damage|mark|scuff|chip"
        r")\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat does (?:the )?(?:phone|device|screen|body|back)\b.{0,20}\blook like\b",
        re.IGNORECASE,
    ),
)
_COLOR_REQUEST_PATTERN = re.compile(
    r"\b(?:what|which|confirm|check|verify|tell me|can you confirm|can you tell me)\b"
    r".{0,60}\b(?:colour|color)\b",
    re.IGNORECASE,
)
_DEVICE_COLOR_PATTERN = re.compile(
    r"\b(black|white|silver|gray|grey|blue|red|gold|green|pink|purple|yellow|orange|brown)\b",
    re.IGNORECASE,
)
_TEXT_ASSISTANT_NAME_PATTERN = re.compile(r"\b(?:ehkaitay|eh[-\s]?kai[-\s]?tay)\b", re.IGNORECASE)
_TRANSFER_DISCLOSURE_PATTERNS = (
    re.compile(r"\btransfer(?:ring)?\s+(?:you|this call)\b", re.IGNORECASE),
    re.compile(r"\bconnect(?:ing)?\s+(?:you|this call)\b", re.IGNORECASE),
    re.compile(r"\brouting\s+you\b", re.IGNORECASE),
    re.compile(r"\bpass(?:ing)?\s+you\s+to\b", re.IGNORECASE),
    re.compile(r"\bbooking agent\b", re.IGNORECASE),
    re.compile(r"\bvaluation specialist\b", re.IGNORECASE),
)

# Tools that require caller phone identity for outbound actions.
_OUTBOUND_CALLER_TOOLS = frozenset({
    "request_callback",
    "send_sms_message",
    "send_whatsapp_message",
})


def looks_like_callback_request(text: str) -> bool:
    """Return True when customer text sounds like a callback request."""
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _CALLBACK_REQUEST_PATTERNS)


def looks_like_callback_promise(text: str) -> bool:
    """Return True when the agent's output text contains a callback promise."""
    normalized = text.strip()
    if not normalized:
        return False
    return any(p.search(normalized) for p in _CALLBACK_PROMISE_PATTERNS)


def _latest_user_turn_text(state: Any, *, session: Any = None) -> str:
    latest_user_raw = _state_get(state, "temp:last_user_turn", "")
    if isinstance(latest_user_raw, str) and latest_user_raw.strip():
        return latest_user_raw.strip()
    session_state = getattr(session, "state", None)
    if session_state is not None:
        session_latest_user_raw = _state_get(session_state, "temp:last_user_turn", "")
        if isinstance(session_latest_user_raw, str) and session_latest_user_raw.strip():
            return session_latest_user_raw.strip()
    return ""


def _latest_agent_turn_text(state: Any, *, session: Any = None) -> str:
    latest_agent_raw = _state_get(state, "temp:last_agent_turn", "")
    if isinstance(latest_agent_raw, str) and latest_agent_raw.strip():
        return latest_agent_raw.strip()
    session_state = getattr(session, "state", None)
    if session_state is not None:
        session_latest_agent_raw = _state_get(session_state, "temp:last_agent_turn", "")
        if isinstance(session_latest_agent_raw, str) and session_latest_agent_raw.strip():
            return session_latest_agent_raw.strip()
    return ""


def _opening_progress_seen(state: Any, *, session: Any = None) -> bool:
    """Return True when the first real user turn has started or completed.

    Live voice/tool callbacks can observe a slightly stale immediate state while
    the authoritative ADK session state already contains the first-turn flags.
    We treat any of these markers across either store as evidence that the
    protected opening phase is already progressing.
    """
    if bool(_state_get(state, "temp:opening_phase_complete", False)):
        return True
    if bool(_state_get(state, "temp:first_user_turn_started", False)):
        return True
    if bool(_state_get(state, "temp:first_user_turn_complete", False)):
        return True
    last_user_turn = _state_get(state, "temp:last_user_turn", "")
    if isinstance(last_user_turn, str) and last_user_turn.strip():
        return True

    session_state = getattr(session, "state", None)
    if session_state is None:
        return False

    if bool(_state_get(session_state, "temp:opening_phase_complete", False)):
        return True
    if bool(_state_get(session_state, "temp:first_user_turn_started", False)):
        return True
    if bool(_state_get(session_state, "temp:first_user_turn_complete", False)):
        return True
    session_last_user_turn = _state_get(session_state, "temp:last_user_turn", "")
    return isinstance(session_last_user_turn, str) and bool(session_last_user_turn.strip())


def _looks_like_tradein_or_upgrade_request(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    lower = normalized.lower()
    if any(
        phrase in lower
        for phrase in (
            "swap",
            "trade in",
            "trade-in",
            "upgrade",
            "switch to",
            "exchange",
        )
    ):
        return True
    brand_matches = list(_DEVICE_BRAND_PATTERN.finditer(normalized))
    if len(brand_matches) >= 2 and " to " in lower:
        return True
    if len(brand_matches) >= 1 and any(token in lower for token in (" from ", " to ")):
        return True
    return False


def _swap_side_has_device_signal(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip(" ,.;:!?")
    if not normalized:
        return False
    tokens = re.findall(r"[A-Za-z0-9+]+", normalized.lower())
    if not tokens:
        return False
    return any(token not in _SWAP_SIDE_GENERIC_TOKENS for token in tokens)


def _looks_like_explicit_device_swap_request(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized or not _looks_like_tradein_or_upgrade_request(normalized):
        return False

    fragments = [
        fragment.strip()
        for fragment in re.split(r"[\n.!?]+", normalized)
        if fragment and fragment.strip()
    ]
    if not fragments:
        fragments = [normalized]

    explicit_side_patterns = (
        re.compile(
            r"\bfrom\b\s+([A-Za-z0-9+\-/ ]{1,40}?)\s+\bto\b\s+([A-Za-z0-9+\-/ ]{1,40})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:swap|trade(?:\s|-)?in|exchange|switch)\b.{0,32}?\b"
            r"([A-Za-z0-9+\-/ ]{1,40}?)\s+\bfor\b\s+([A-Za-z0-9+\-/ ]{1,40})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:swap|trade(?:\s|-)?in|upgrade|switch|exchange)\b.{0,32}?\b"
            r"([A-Za-z0-9+\-/ ]{1,40}?)\s+\bto\b\s+([A-Za-z0-9+\-/ ]{1,40})",
            re.IGNORECASE,
        ),
    )
    for fragment in fragments:
        for pattern in explicit_side_patterns:
            match = pattern.search(fragment)
            if not match:
                continue
            left = match.group(1).strip()
            right = match.group(2).strip()
            if _swap_side_has_device_signal(left) and _swap_side_has_device_signal(right):
                return True

        brand_matches = list(_DEVICE_BRAND_PATTERN.finditer(fragment))
        lower = fragment.lower()
        if len(brand_matches) >= 2 and any(token in lower for token in (" for ", " to ", " from ")):
            return True
    return False


def _recent_customer_context_text(state: Any, *, session: Any = None) -> str:
    recent_raw = _state_get(state, "temp:recent_customer_context", "")
    if isinstance(recent_raw, str) and recent_raw.strip():
        return recent_raw.strip()
    session_state = getattr(session, "state", None)
    if session_state is not None:
        session_recent_raw = _state_get(session_state, "temp:recent_customer_context", "")
        if isinstance(session_recent_raw, str) and session_recent_raw.strip():
            return session_recent_raw.strip()
    return ""


def _voice_session_identity(state: Any, *, session: Any = None) -> tuple[str, str]:
    session_state = getattr(session, "state", None)
    user_id = str(_state_get(state, "app:user_id", "") or "").strip()
    session_id = str(_state_get(state, "app:session_id", "") or "").strip()
    if not user_id and session_state is not None:
        user_id = str(_state_get(session_state, "app:user_id", "") or "").strip()
    if not session_id and session_state is not None:
        session_id = str(_state_get(session_state, "app:session_id", "") or "").strip()
    return user_id, session_id


def _persist_voice_string_state(
    *,
    state: Any,
    session: Any = None,
    key: str,
    value: str,
) -> None:
    if key not in VOICE_STATE_STR_KEYS:
        return
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized:
        return
    try:
        state[key] = normalized
    except Exception:
        logger.debug("Failed to persist callback state key %s", key, exc_info=True)
    session_state = getattr(session, "state", None)
    if session_state is not None:
        try:
            session_state[key] = normalized
        except Exception:
            logger.debug("Failed to persist session state key %s", key, exc_info=True)
    user_id, session_id = _voice_session_identity(state, session=session)
    if not (user_id and session_id):
        return
    try:
        update_voice_state(user_id=user_id, session_id=session_id, **{key: normalized})
    except Exception:
        logger.debug("Failed to update voice registry key %s", key, exc_info=True)


def _persist_voice_json_state(
    *,
    state: Any,
    session: Any = None,
    key: str,
    value: dict[str, Any],
) -> None:
    if key not in VOICE_STATE_JSON_KEYS or not isinstance(value, dict) or not value:
        return
    payload = copy.deepcopy(value)
    try:
        state[key] = payload
    except Exception:
        logger.debug("Failed to persist callback JSON state key %s", key, exc_info=True)
    session_state = getattr(session, "state", None)
    if session_state is not None:
        try:
            session_state[key] = copy.deepcopy(payload)
        except Exception:
            logger.debug("Failed to persist session JSON state key %s", key, exc_info=True)
    user_id, session_id = _voice_session_identity(state, session=session)
    if not (user_id and session_id):
        return
    try:
        update_voice_state(user_id=user_id, session_id=session_id, **{key: payload})
    except Exception:
        logger.debug("Failed to update voice registry JSON key %s", key, exc_info=True)


def _persist_runtime_hint_state(
    *,
    state: Any,
    session: Any = None,
    key: str,
    value: Any,
) -> None:
    """Persist non-instruction runtime state into callback and session views."""
    try:
        state[key] = value
    except Exception:
        logger.debug("Failed to persist callback hint key %s", key, exc_info=True)
    session_state = getattr(session, "state", None)
    if session_state is not None:
        try:
            session_state[key] = copy.deepcopy(value)
        except Exception:
            logger.debug("Failed to persist session hint key %s", key, exc_info=True)
    if key not in VOICE_STATE_KEYS:
        return
    user_id, session_id = _voice_session_identity(state, session=session)
    if not (user_id and session_id):
        return
    try:
        update_voice_state(
            user_id=user_id,
            session_id=session_id,
            **{key: copy.deepcopy(value)},
        )
    except Exception:
        logger.debug("Failed to update voice registry hint key %s", key, exc_info=True)


def _vision_media_handoff_state(state: Any, *, session: Any = None) -> str:
    user_id, session_id = _voice_session_identity(state, session=session)
    if user_id and session_id:
        registry_state = get_registered_voice_state(user_id=user_id, session_id=session_id)
        registry_raw = registry_state.get("temp:vision_media_handoff_state", "")
        if isinstance(registry_raw, str) and registry_raw.strip():
            return registry_raw.strip().lower()
    session_state = getattr(session, "state", None)
    if session_state is not None:
        session_raw = _state_get(session_state, "temp:vision_media_handoff_state", "")
        if isinstance(session_raw, str) and session_raw.strip():
            return session_raw.strip().lower()
    raw = _state_get(state, "temp:vision_media_handoff_state", "")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return ""


def _background_vision_status(state: Any, *, session: Any = None) -> str:
    user_id, session_id = _voice_session_identity(state, session=session)
    if user_id and session_id:
        registry_state = get_registered_voice_state(user_id=user_id, session_id=session_id)
        registry_raw = registry_state.get("temp:background_vision_status", "")
        if isinstance(registry_raw, str) and registry_raw.strip():
            return registry_raw.strip().lower()
    session_state = getattr(session, "state", None)
    if session_state is not None:
        session_raw = _state_get(session_state, "temp:background_vision_status", "")
        if isinstance(session_raw, str) and session_raw.strip():
            return session_raw.strip().lower()
    raw = _state_get(state, "temp:background_vision_status", "")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return ""


def _latest_tool_backed_analysis(state: Any, *, session: Any = None) -> dict[str, Any] | None:
    background_status = _background_vision_status(state, session=session)
    handoff_state = _vision_media_handoff_state(state, session=session)
    if background_status in {"awaiting_media", "running", "failed"}:
        return None
    if handoff_state in {"pending", "transferring"} and background_status != "ready":
        return None

    user_id, session_id = _voice_session_identity(state, session=session)
    if user_id and session_id:
        registry_state = get_registered_voice_state(user_id=user_id, session_id=session_id)
        registry_raw = registry_state.get("temp:last_analysis")
        if isinstance(registry_raw, dict):
            return registry_raw
    session_state = getattr(session, "state", None)
    if session_state is not None:
        session_raw = _state_get(session_state, "temp:last_analysis", None)
        if isinstance(session_raw, dict):
            return session_raw
    raw = _state_get(state, "temp:last_analysis", None)
    if isinstance(raw, dict):
        return raw
    return None


def _voice_tradein_context_text(state: Any, *, session: Any = None) -> str:
    channel = _state_get(state, "app:channel", "")
    normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
    if normalized_channel != "voice":
        return ""
    return " ".join(
        part.strip()
        for part in (
            _latest_user_turn_text(state, session=session),
            _recent_customer_context_text(state, session=session),
        )
        if isinstance(part, str) and part.strip()
    ).strip()


def _router_voice_swap_handoff_instruction(
    state: State,
    agent_name: str,
    *,
    session: Any = None,
) -> str:
    normalized_agent = agent_name.strip() if isinstance(agent_name, str) else ""
    if normalized_agent != "ekaette_router":
        return ""

    channel = _state_get(state, "app:channel", "")
    normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
    if normalized_channel != "voice":
        return ""

    combined_text = _voice_tradein_context_text(state, session=session)
    if not _looks_like_explicit_device_swap_request(combined_text):
        return ""

    return (
        "VOICE SWAP ROUTING — MANDATORY: The caller has already stated both the phone they "
        "currently have and the phone they want. Your next tool action in THIS turn must be "
        "transfer_to_agent(agent_name=\"valuation_agent\"). Before the tool call, say one short "
        "continuity phrase like 'Let me sort that out for you right now.' Do NOT ask catalog "
        "questions such as brand new or certified pre-owned, storage size, colour, price, "
        "availability, delivery, or card/cash before valuation_agent takes over."
    )


def _is_voice_tradein_flow(state: Any, *, session: Any = None) -> bool:
    combined_text = _voice_tradein_context_text(state, session=session)
    return bool(combined_text) and _looks_like_tradein_or_upgrade_request(combined_text)


def _uses_canonical_background_vision_path(state: Any, *, session: Any = None) -> bool:
    if not _is_voice_tradein_flow(state, session=session):
        return False
    background_status = _background_vision_status(state, session=session)
    if background_status in {"awaiting_media", "running", "ready", "failed"}:
        return True
    handoff_state = _vision_media_handoff_state(state, session=session)
    return handoff_state in {"pending", "transferring"}


def _looks_like_non_task_customer_turn(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return True
    if _GREETING_ONLY_PATTERN.fullmatch(normalized):
        return True
    if _SELF_INTRO_ONLY_PATTERN.fullmatch(normalized):
        return True
    if _ACK_ONLY_PATTERN.fullmatch(normalized):
        return True
    if _VOICE_REPAIR_PATTERN.search(normalized):
        return True
    return False


def _looks_like_meaningful_customer_request(text: str) -> bool:
    normalized = text.strip()
    if not normalized or _looks_like_non_task_customer_turn(normalized):
        return False
    tokens = re.findall(r"[A-Za-z0-9']+", normalized)
    if not tokens:
        return False
    return bool(_GENERIC_REQUEST_PATTERN.search(normalized))


def _has_booking_commitment_intent(
    *,
    state: Any,
    session: Any = None,
    latest_user: str,
    recent_customer: str,
) -> bool:
    combined_parts = [
        text.strip()
        for text in (latest_user, recent_customer)
        if isinstance(text, str) and text.strip()
    ]
    combined_text = " ".join(combined_parts).strip()
    if not combined_text:
        return False
    if _BOOKING_REQUEST_PATTERN.search(combined_text):
        return True
    latest_user_text = latest_user.strip() if isinstance(latest_user, str) else ""
    if not latest_user_text or not _BOOKING_PROGRESS_PATTERN.search(latest_user_text):
        return False
    offer_amount = _state_get(state, "temp:last_offer_amount", 0)
    try:
        has_offer_amount = float(offer_amount or 0) > 0
    except (TypeError, ValueError):
        has_offer_amount = False
    context_text = recent_customer.strip() if isinstance(recent_customer, str) else ""
    return bool(
        has_offer_amount
        or _BOOKING_CONTEXT_PATTERN.search(context_text)
        or _looks_like_tradein_or_upgrade_request(context_text)
    )


def _has_explicit_transfer_request(
    *,
    state: Any,
    session: Any = None,
    target_agent: str,
    latest_user: str,
    recent_customer: str,
) -> bool:
    combined_parts = [text.strip() for text in (latest_user, recent_customer) if isinstance(text, str) and text.strip()]
    combined_text = " ".join(combined_parts).strip()
    if not combined_text:
        return False

    if target_agent == "valuation_agent":
        return bool(
            _looks_like_tradein_or_upgrade_request(combined_text)
            or _VALUATION_REQUEST_PATTERN.search(combined_text)
        )
    if target_agent == "catalog_agent":
        return bool(_CATALOG_REQUEST_PATTERN.search(combined_text))
    if target_agent == "booking_agent":
        return _has_booking_commitment_intent(
            state=state,
            session=session,
            latest_user=latest_user,
            recent_customer=recent_customer,
        )
    if target_agent == "vision_agent":
        return bool(_VISION_REQUEST_PATTERN.search(combined_text))
    if target_agent == "support_agent":
        return bool(_SUPPORT_REQUEST_PATTERN.search(combined_text))
    return _looks_like_meaningful_customer_request(combined_text)


def _request_callback_has_explicit_intent(
    state: State,
    args: dict[str, Any],
) -> bool:
    """Return True when request_callback is backed by real user intent.

    This prevents voice startup/attach turns from invoking request_callback
    before the caller has actually asked for one. We accept either:
    - a callback-like latest user turn captured in session state
    - a callback-like explicit tool reason argument
    """
    latest_user_raw = _state_get(state, "temp:last_user_turn", "")
    latest_user = latest_user_raw.strip() if isinstance(latest_user_raw, str) else ""
    if latest_user and looks_like_callback_request(latest_user):
        return True

    reason_raw = args.get("reason", "")
    reason = reason_raw.strip() if isinstance(reason_raw, str) else ""
    if reason and looks_like_callback_request(reason):
        return True

    return False


def _is_callback_leg(state: State, *, session_id_override: str = "") -> bool:
    """Return True when the current session is an outbound callback leg.

    Callback sessions are created by the SIP bridge with session IDs
    prefixed ``sip-callback-``.  On a callback leg the agent must NOT
    call ``request_callback`` again (that would create an infinite loop).

    ``session_id_override`` allows callers with access to the ADK Session
    object (e.g. ``tool_context.session.id``) to pass the authoritative
    session ID directly, bypassing state-lookup timing issues.
    """
    # Prefer the authoritative session ID from the ADK session object.
    if isinstance(session_id_override, str) and session_id_override.strip().startswith("sip-callback-"):
        return True
    session_id = _state_get(state, "app:session_id", "")
    if isinstance(session_id, str) and session_id.strip().startswith("sip-callback-"):
        return True
    return False


# ═══ Capability Guard ═══

TOOL_CAPABILITY_MAP: dict[str, str] = {
    "create_booking": "booking_reservations",
    "cancel_booking": "booking_reservations",
    "check_availability": "booking_reservations",
    "search_catalog": "catalog_lookup",
    "get_product_details": "catalog_lookup",
    "analyze_device_image_tool": "valuation_tradein",
    "grade_and_value_tool": "valuation_tradein",
    "grade_condition": "valuation_tradein",
    "calculate_trade_in_value": "valuation_tradein",
    "search_company_knowledge": "policy_qa",
    "get_company_profile_fact": "policy_qa",
    "query_company_system": "connector_dispatch",
    "send_whatsapp_message": "outbound_messaging",
    "send_sms_message": "outbound_messaging",
    "request_callback": "outbound_messaging",
    "get_device_questionnaire_tool": "valuation_tradein",
    "request_media_via_whatsapp": "valuation_tradein",
}

AGENT_NOT_ENABLED_ERROR_CODE = "AGENT_NOT_ENABLED"


def _next_server_message_id(state: State) -> int:
    """Return monotonically increasing ID for websocket server messages."""
    raw = state.get("temp:server_message_seq", 0)
    try:
        current = int(raw)
    except (TypeError, ValueError):
        current = 0
    return current + 1


def queue_server_message(state: State, payload: dict[str, Any]) -> None:
    """Queue one structured server message in state delta for downstream emit."""
    message_id = _next_server_message_id(state)
    message = dict(payload)
    message["id"] = message_id
    state["temp:server_message_seq"] = message_id
    state["temp:last_server_message"] = message


def _queue_end_after_speaking_control(state: State, *, reason: str) -> None:
    """Ask the telephony bridge to end the call once the current agent turn drains."""
    if bool(_state_get(state, "temp:call_end_after_speaking_requested", False)):
        return
    state["temp:call_end_after_speaking_requested"] = True
    queue_server_message(
        state,
        {
            "type": "call_control",
            "action": "end_after_speaking",
            "reason": reason,
        },
    )


def _state_get(state: Any, key: str, default: Any = None) -> Any:
    getter = getattr(state, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    return default


def _ensure_instruction_state_defaults(state: Any) -> None:
    """Seed optional instruction-template keys so ADK never raises KeyError.

    ADK treats any missing ``{temp:...}`` placeholder in an agent instruction as
    a hard error during template expansion. Optional state that may not exist on
    a fresh session must therefore be initialized before each model turn.
    """
    for key, default in _INSTRUCTION_STATE_DEFAULTS.items():
        existing = _state_get(state, key, None)
        if existing is not None:
            continue
        try:
            state[key] = default
        except Exception:
            logger.debug("Failed to seed instruction state key %s", key, exc_info=True)


def _is_greeted_state(state: Any, *, session: Any = None) -> bool:
    """Return greeting completion from either immediate or session-backed state.

    Live voice paths can update the ADK session state before the per-tool state
    snapshot reflects the change. For first-turn transfer guards we therefore
    treat session.state as a fallback source of truth.
    """
    if bool(_state_get(state, "temp:greeted", False)):
        return True
    session_state = getattr(session, "state", None)
    if session_state is not None and bool(_state_get(session_state, "temp:greeted", False)):
        return True
    last_user_turn = _state_get(state, "temp:last_user_turn", "")
    if isinstance(last_user_turn, str) and last_user_turn.strip():
        return True
    if session_state is not None:
        session_last_user_turn = _state_get(session_state, "temp:last_user_turn", "")
        if isinstance(session_last_user_turn, str) and session_last_user_turn.strip():
            return True
    last_agent_turn = _state_get(state, "temp:last_agent_turn", "")
    if isinstance(last_agent_turn, str) and last_agent_turn.strip():
        return True
    if session_state is not None:
        session_last_agent_turn = _state_get(session_state, "temp:last_agent_turn", "")
        if isinstance(session_last_agent_turn, str) and session_last_agent_turn.strip():
            return True
    try:
        turn_count = int(_state_get(state, "temp:model_turn_count", 0) or 0)
    except (TypeError, ValueError):
        turn_count = 0
    if turn_count > 0:
        return True
    if session_state is not None:
        try:
            session_turn_count = int(_state_get(session_state, "temp:model_turn_count", 0) or 0)
        except (TypeError, ValueError):
            session_turn_count = 0
        if session_turn_count > 0:
            return True
    return False


def _is_voice_opening_complete(state: Any, *, session: Any = None) -> bool:
    """Return whether the protected voice opening phase has fully completed.

    Voice transfers should only happen after:
    1. the greeting has actually completed, and
    2. the first real user turn has clearly started.

    This state is stricter than ``temp:greeted`` and avoids letting weak
    startup transcripts or model-side attach noise drive early routing.
    """
    if bool(_state_get(state, "temp:opening_phase_complete", False)):
        return True
    if bool(_state_get(state, "temp:first_user_turn_complete", False)):
        return True
    session_state = getattr(session, "state", None)
    if session_state is not None and bool(
        _state_get(session_state, "temp:opening_phase_complete", False)
    ):
        return True
    if session_state is not None and bool(
        _state_get(session_state, "temp:first_user_turn_complete", False)
    ):
        return True

    opening_greeting_complete = bool(_state_get(state, "temp:opening_greeting_complete", False))
    if session_state is not None:
        opening_greeting_complete = opening_greeting_complete or bool(
            _state_get(session_state, "temp:opening_greeting_complete", False)
        )

    if _opening_progress_seen(state, session=session) and (
        opening_greeting_complete or _is_greeted_state(state, session=session)
    ):
        return True
    return False


def _hydrate_voice_opening_state_from_session(
    state: Any, *, session: Any = None,
) -> None:
    """Copy canonical opening/turn flags from session.state into callback state.

    In Live API mode, stream_tasks writes guard-relevant flags to the raw
    session.state dict, but ADK's tool_context.state (a snapshot) may not
    see those writes.  This helper bridges the gap so that transfer guards
    make decisions against the most up-to-date values.

    Semantics:
    - Boolean flags: upgrade-only (False → True, never downgrade).
    - String values: session.state is canonical when non-empty, overwriting
      stale callback values so guards see the latest user turn.
    """
    session_state = getattr(session, "state", None) if session is not None else None

    _BOOL_KEYS = (
        "temp:greeted",
        "temp:opening_greeting_complete",
        "temp:opening_phase_complete",
        "temp:first_user_turn_started",
        "temp:first_user_turn_complete",
    )
    _STR_KEYS = (
        "temp:last_user_turn",
        "temp:last_agent_turn",
        "temp:recent_customer_context",
        "temp:vision_media_handoff_state",
        "temp:pending_handoff_target_agent",
        "temp:pending_handoff_latest_user",
        "temp:pending_handoff_latest_agent",
        "temp:pending_handoff_recent_customer_context",
    )

    hydrated_any = False
    if session_state is not None:
        # Boolean flags: upgrade-only (never downgrade True → False)
        for key in _BOOL_KEYS:
            session_value = _state_get(session_state, key, False)
            if bool(session_value) and not bool(_state_get(state, key, False)):
                try:
                    state[key] = session_value
                    hydrated_any = True
                except Exception:
                    pass
        # String values: session.state is canonical when non-empty
        for key in _STR_KEYS:
            session_value = _state_get(session_state, key, "")
            if isinstance(session_value, str) and session_value.strip():
                current = _state_get(state, key, "")
                current_str = current.strip() if isinstance(current, str) else ""
                if current_str != session_value.strip():
                    try:
                        state[key] = session_value
                        hydrated_any = True
                    except Exception:
                        pass
        if hydrated_any:
            logger.debug(
                "hydrate_voice_state session_state_has_opening_complete=%s greeted=%s first_user_turn_complete=%s",
                bool(_state_get(session_state, "temp:opening_phase_complete", False)),
                bool(_state_get(session_state, "temp:greeted", False)),
                bool(_state_get(session_state, "temp:first_user_turn_complete", False)),
            )

    user_id = str(_state_get(state, "app:user_id", "") or "").strip()
    session_id = str(_state_get(state, "app:session_id", "") or "").strip()
    if not user_id and session_state is not None:
        user_id = str(_state_get(session_state, "app:user_id", "") or "").strip()
    if not session_id and session_state is not None:
        session_id = str(_state_get(session_state, "app:session_id", "") or "").strip()
    if not (user_id and session_id):
        return

    registry_state = get_registered_voice_state(user_id=user_id, session_id=session_id)
    if not registry_state:
        return

    registry_hydrated_any = False
    for key in VOICE_STATE_BOOL_KEYS:
        if bool(registry_state.get(key)) and not bool(_state_get(state, key, False)):
            try:
                state[key] = True
                registry_hydrated_any = True
            except Exception:
                pass
    for key in VOICE_STATE_STR_KEYS:
        registry_value = registry_state.get(key, "")
        if isinstance(registry_value, str) and registry_value.strip():
            current = _state_get(state, key, "")
            current_str = current.strip() if isinstance(current, str) else ""
            if current_str != registry_value.strip():
                try:
                    state[key] = registry_value
                    registry_hydrated_any = True
                except Exception:
                    pass
    for key in VOICE_STATE_JSON_KEYS:
        registry_value = registry_state.get(key, None)
        if isinstance(registry_value, dict) and registry_value:
            current = _state_get(state, key, None)
            if current != registry_value:
                try:
                    state[key] = copy.deepcopy(registry_value)
                    registry_hydrated_any = True
                except Exception:
                    pass
    for key in VOICE_STATE_INT_KEYS:
        current_raw = _state_get(state, key, 0)
        try:
            current_value = int(current_raw or 0)
        except (TypeError, ValueError):
            current_value = 0
        try:
            registry_value = int(registry_state.get(key, 0) or 0)
        except (TypeError, ValueError):
            registry_value = 0
        if registry_value > current_value:
            try:
                state[key] = registry_value
                registry_hydrated_any = True
            except Exception:
                pass
    if registry_hydrated_any:
        logger.debug(
            "hydrate_voice_state_from_registry session=%s opening_complete=%s greeted=%s first_user_turn_complete=%s",
            session_id,
            bool(registry_state.get("temp:opening_phase_complete", False)),
            bool(registry_state.get("temp:greeted", False)),
            bool(registry_state.get("temp:first_user_turn_complete", False)),
        )


def _hallucinated_transfer_signature(
    state: Any,
    target_agent: str,
    *,
    session: Any = None,
) -> str:
    latest_user = _latest_user_turn_text(state, session=session)
    turn_marker = latest_user or "<no-user-turn>"
    return f"{target_agent}::{turn_marker}"


def _maybe_inject_caller_phone(tool_context: Any) -> None:
    """Inject caller phone into tool state from the ephemeral registry.

    ADK's live-streaming mode sometimes does not surface ``user:caller_phone``
    in the tool context's session state.  This bridging logic resolves it from
    the per-process ephemeral registry (populated at session init time) and
    writes it into the state so that downstream tool code can find it.
    """
    state = getattr(tool_context, "state", None)
    if state is None:
        return
    existing = _state_get(state, "user:caller_phone", "")
    if isinstance(existing, str) and existing.strip():
        return  # already present — nothing to do

    from app.api.v1.realtime.caller_phone_registry import get_registered_caller_phone

    user_id = str(_state_get(state, "app:user_id", "") or "").strip()
    session_id = str(_state_get(state, "app:session_id", "") or "").strip()
    if not user_id:
        user_id = str(getattr(tool_context, "user_id", "") or "").strip()
    if not session_id:
        session_id = str(getattr(getattr(tool_context, "session", None), "id", "") or "").strip()
    if (not user_id or not session_id) and getattr(getattr(tool_context, "session", None), "state", None) is not None:
        session_state = getattr(tool_context.session, "state", None)
        user_id = user_id or str(_state_get(session_state, "app:user_id", "") or "").strip()
        session_id = session_id or str(_state_get(session_state, "app:session_id", "") or "").strip()
    if not user_id:
        return
    phone = get_registered_caller_phone(user_id=user_id, session_id=session_id)
    if phone:
        try:
            state["user:caller_phone"] = phone
        except Exception:
            pass
        logger.info(
            "Injected caller phone from registry user_id=%s session_id=%s",
            user_id,
            session_id,
        )


def _industry_scope_label(state: Any) -> str:
    template_id = _state_get(state, "app:industry_template_id")
    if isinstance(template_id, str) and template_id.strip():
        return template_id.strip()
    industry = _state_get(state, "app:industry")
    if isinstance(industry, str) and industry.strip():
        return industry.strip()
    return "current"


def _response_commits_to_callback(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _CALLBACK_PROMISE_PATTERNS)


def _agent_not_enabled_message(scope_label: str, agent_name: str) -> str:
    return (
        f"This {scope_label} session is isolated and cannot switch to '{agent_name}'. "
        "I can only use the agents enabled for the current industry."
    )


def _agent_not_enabled_payload(
    *,
    state: Any,
    agent_name: str,
    allowed_agents: list[str],
) -> dict[str, Any]:
    scope_label = _industry_scope_label(state)
    payload: dict[str, Any] = {
        "type": "error",
        "code": AGENT_NOT_ENABLED_ERROR_CODE,
        "message": _agent_not_enabled_message(scope_label, agent_name),
        "agentName": agent_name,
        "allowedAgents": list(allowed_agents),
    }
    tenant = _state_get(state, "app:tenant_id")
    if isinstance(tenant, str) and tenant:
        payload["tenantId"] = tenant
    template_id = _state_get(state, "app:industry_template_id")
    if isinstance(template_id, str) and template_id:
        payload["industryTemplateId"] = template_id
    return payload


def _agent_not_enabled_content(scope_label: str, agent_name: str) -> types.Content:
    return types.Content(
        role="model",
        parts=[types.Part(text=_agent_not_enabled_message(scope_label, agent_name))],
    )


def _requested_transfer_agent_name(args: dict[str, Any]) -> str | None:
    raw = args.get("agent_name", args.get("agentName"))
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _is_transfer_tool_name(tool_name: str) -> bool:
    return tool_name == "transfer_to_agent" or tool_name.startswith("transfer_to_")


def _tool_transfer_target_agent_name(tool_name: str, args: dict[str, Any]) -> str | None:
    if not _is_transfer_tool_name(tool_name):
        return None
    requested = _requested_transfer_agent_name(args)
    if requested:
        return requested
    if tool_name.startswith("transfer_to_"):
        candidate = tool_name.removeprefix("transfer_to_").strip()
        if candidate in KNOWN_SUB_AGENT_NAMES:
            return candidate
    return None


async def before_agent_isolation_guard(
    callback_context: CallbackContext,
) -> types.Content | None:
    """Block sub-agent invocations not enabled for the current industry session."""
    agent_name = callback_context.agent_name
    if agent_name == "ekaette_router":
        return None

    state = callback_context.state
    if state is None or not hasattr(state, "get"):
        return None

    enabled_agents = resolve_enabled_agents_from_state(state)
    if enabled_agents is None or agent_name in enabled_agents:
        return None

    scope_label = _industry_scope_label(state)
    logger.warning(
        "agent_isolation_blocked phase=before_agent agent=%s industry=%s enabled_agents=%s",
        agent_name,
        scope_label,
        enabled_agents,
    )
    queue_server_message(
        state,
        _agent_not_enabled_payload(
            state=state,
            agent_name=agent_name,
            allowed_agents=enabled_agents,
        ),
    )
    return _agent_not_enabled_content(scope_label, agent_name)


async def before_agent_isolation_guard_and_dedup(
    callback_context: CallbackContext,
) -> types.Content | None:
    """Compose isolation guard and dedup mitigation for router sub-agent transfers."""
    blocked = await before_agent_isolation_guard(callback_context)
    if blocked is not None:
        return blocked
    return await dedup_before_agent(callback_context)


def _response_text(llm_response: LlmResponse) -> str:
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return ""
    chunks = [part.text for part in parts if getattr(part, "text", None)]
    return " ".join(chunks).strip()


def _response_has_content(llm_response: LlmResponse) -> bool:
    """Return True if the response has any meaningful content (text or audio).

    In native-audio Live API mode, the model generates inline_data audio
    parts instead of text parts. This helper detects both, so callers can
    reliably determine whether the model actually spoke.
    """
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return False
    for part in parts:
        if getattr(part, "text", None):
            return True
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            return True
    return False


def _rewrite_response_text(llm_response: LlmResponse, replacement: str) -> bool:
    """Replace the first text part in a model response with a safer reply."""
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return False
    replaced = False
    for part in parts:
        if getattr(part, "text", None):
            part.text = replacement
            replaced = True
            break
    if not replaced:
        return False
    for part in parts:
        if getattr(part, "text", None) and part.text != replacement:
            part.text = ""
    return True


def _indefinite_article(noun_phrase: str) -> str:
    normalized = str(noun_phrase or "").strip()
    if not normalized:
        return ""
    article = "an" if normalized[0].lower() in {"a", "e", "i", "o", "u"} else "a"
    return f"{article} {normalized}"


def _normalized_analysis_detail(value: Any) -> str:
    if isinstance(value, dict):
        description = value.get("description")
        if isinstance(description, str):
            value = description
        else:
            return ""
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split()).strip().rstrip(".")
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered in {"not visible", "no visible damage", "all features working"}:
        return ""
    return normalized


def _format_tradein_analysis_summary(analysis: dict[str, Any]) -> str:
    if not isinstance(analysis, dict):
        return ""

    device_name = str(analysis.get("device_name", "") or "").strip()
    brand = str(analysis.get("brand", "") or "").strip()
    device_color = str(analysis.get("device_color", "") or "").strip().lower()
    condition = str(analysis.get("condition", "") or "").strip()
    power_state = str(analysis.get("power_state", "") or "").strip().lower()
    color_confirmed = bool(device_color and device_color != "unknown")

    details = analysis.get("details", {})
    if not isinstance(details, dict):
        details = {}
    screen_detail = _normalized_analysis_detail(details.get("screen"))
    body_detail = _normalized_analysis_detail(details.get("body"))

    device_clause = ""
    if device_name:
        device_clause = f"it looks like {_indefinite_article(device_name)}"
        if brand and brand.lower() not in device_name.lower():
            device_clause += f" from {brand}"
    elif brand:
        device_clause = f"it looks like {_indefinite_article(f'{brand} device')}"
    elif color_confirmed:
        device_clause = "the device is visible in the video"

    if device_clause and color_confirmed:
        device_clause += f" in {device_color}"

    summary_clauses: list[str] = []
    if device_clause:
        summary_clauses.append(device_clause)
    if power_state == "on":
        summary_clauses.append("it appears to power on")
    elif power_state == "off":
        summary_clauses.append("it appears switched off in the clip")
    if condition and condition.lower() != "unknown":
        summary_clauses.append(f"overall it looks to be in {condition} condition")
    if not color_confirmed:
        summary_clauses.append("I could not clearly confirm the exact colour from the video")

    detail_sentences: list[str] = []
    if screen_detail:
        detail_sentences.append(f"I can see {screen_detail.lower()} on the screen")
    if body_detail:
        detail_sentences.append(f"the body shows {body_detail.lower()}")

    if not summary_clauses and not detail_sentences:
        return ""

    summary = "Here's what I can confirm from the video: "
    if summary_clauses:
        summary += ", ".join(summary_clauses[:-1])
        if len(summary_clauses) > 1:
            summary = summary.rstrip(", ")
            summary += f", and {summary_clauses[-1]}."
        else:
            summary += f"{summary_clauses[0]}."
    if detail_sentences:
        if not summary.endswith("."):
            summary += "."
        summary += " " + " and ".join(detail_sentences) + "."
    return summary


def _analysis_supports_tradein_offer(analysis: dict[str, Any] | None) -> bool:
    if not isinstance(analysis, dict):
        return False
    condition = str(analysis.get("condition", "") or "").strip().lower()
    return bool(condition and condition != "unknown")


def _looks_like_offer_response(text: str, offer_amount: int | float | None = None) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if _OFFER_RESPONSE_PATTERN.search(normalized):
        return True
    if "₦" in normalized or "NGN" in normalized.upper():
        return True
    expected_amount: int | None = None
    if isinstance(offer_amount, (int, float)) and offer_amount > 0:
        expected_amount = int(round(float(offer_amount)))
    for token in _PRICE_PATTERN.findall(normalized):
        parsed = token.replace(",", "")
        if not parsed.isdigit():
            continue
        value = int(parsed)
        if expected_amount is None or abs(value - expected_amount) <= 1:
            return True
    return False


def _offer_followup_prompt(text: str) -> str:
    normalized = text.strip().lower()
    if not normalized:
        return "Would you like to go ahead with that offer, or would you like to negotiate?"
    if "booking" in normalized or "proceed" in normalized or "go ahead" in normalized:
        return "If you'd like to go ahead, I can take the next step from there."
    if "negot" in normalized or "counter" in normalized:
        return "Would you like to go ahead with that offer, or would you like to negotiate?"
    if "accept" in normalized:
        return "Would you like to accept that offer, or would you like to negotiate?"
    return "Would you like to go ahead with that offer, or would you like to negotiate?"


def _tradein_offer_replacement(
    *,
    offer_amount: int | float,
    analysis: dict[str, Any],
    original_text: str,
) -> str:
    summary = _format_tradein_analysis_summary(analysis)
    offer_text = f"Based on that, our trade-in offer is ₦{int(round(float(offer_amount))):,}."
    followup = _offer_followup_prompt(original_text)
    pieces = [piece for piece in (summary, offer_text, followup) if piece]
    return " ".join(pieces).strip()


def _unguarded_tradein_offer_replacement(
    *,
    state: Any,
    session: Any = None,
) -> str:
    background_status = _background_vision_status(state, session=session)
    if background_status == "awaiting_media":
        filler = _background_tradein_followup_question(state, session=session)
        return (
            "I've sent the WhatsApp request and I'm waiting for the new video to come through, "
            f"so I don't want to quote a trade-in price from guesswork yet. {filler}"
        )
    if background_status == "running":
        filler = _background_tradein_followup_question(state, session=session)
        return (
            "I'm still checking the video, so I don't want to quote a trade-in price from "
            f"guesswork just yet. {filler}"
        )
    if background_status == "failed":
        return (
            "I couldn't finish checking that video clearly enough to price the phone fairly. "
            "Please resend the video or a few clear photos on WhatsApp, and once I can verify "
            "what I see I'll read back the analysis before I quote the price."
        )
    return (
        "I still need a grounded video analysis before I can price the phone fairly. "
        "Once that analysis is ready, I'll read back what I can confirm from the video "
        "before I quote the offer."
    )


def _normalize_text_assistant_name(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text
    return _TEXT_ASSISTANT_NAME_PATTERN.sub("Ekaette", text)


def _asks_for_visible_condition_details(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _VISIBLE_CONDITION_REQUEST_PATTERNS)


def _asks_to_confirm_device_color(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return bool(_COLOR_REQUEST_PATTERN.search(normalized))


def _normalized_color_token(text: str) -> str:
    if not isinstance(text, str):
        return ""
    match = _DEVICE_COLOR_PATTERN.search(text)
    if not match:
        return ""
    color = match.group(1).strip().lower()
    return "gray" if color == "grey" else color


def _grounded_color_confirmation_replacement(
    *,
    analysis: dict[str, Any] | None,
    state: Any,
    session: Any = None,
    original_text: str = "",
) -> str:
    background_status = _background_vision_status(state, session=session)
    if background_status in {"awaiting_media", "running"}:
        return (
            "I'm still checking the video, so I can't confirm the phone's colour just yet. "
            f"{_background_tradein_followup_question(state, session=session)}"
        )
    if background_status == "failed":
        return (
            "I couldn't verify the phone's colour from that media. Please resend the video "
            "or a few clear photos on WhatsApp and I'll confirm it from the new analysis."
        )
    if not isinstance(analysis, dict):
        return (
            "I don't have a verified colour from the video yet, so I don't want to guess. "
            "Please resend the media on WhatsApp and I'll confirm it once the analysis is ready."
        )

    device_color = _normalized_color_token(str(analysis.get("device_color", "") or ""))
    if not device_color or device_color == "unknown":
        return (
            "I couldn't confirm the phone's colour clearly from the analysis, so I don't want "
            "to guess."
        )

    sentence = f"The video analysis shows the phone is {device_color}."
    if original_text and ("offer" in original_text.lower() or "proceed" in original_text.lower()):
        sentence += f" {_offer_followup_prompt(original_text)}"
    return sentence


def _claims_whatsapp_delivery(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _WHATSAPP_DELIVERY_CLAIM_PATTERNS)


def _contains_transfer_disclosure(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _TRANSFER_DISCLOSURE_PATTERNS)


def _transfer_disclosure_replacement(
    *,
    state: Any,
    session: Any = None,
    agent_name: str,
) -> str:
    latest_user = _latest_user_turn_text(state, session=session)
    recent_customer = _recent_customer_context_text(state, session=session)
    if agent_name == "valuation_agent" and _has_booking_commitment_intent(
        state=state,
        session=session,
        latest_user=latest_user,
        recent_customer=recent_customer,
    ):
        return "Great, let me get the next step sorted for you now."
    if agent_name == "valuation_agent" and _is_voice_tradein_flow(state, session=session):
        return "Let me sort that out for you right now."
    return "Let me take care of that now."


def _media_request_status(state: Any, *, session: Any = None) -> str:
    raw_status = _state_get(state, "temp:last_media_request_status", "")
    if isinstance(raw_status, str) and raw_status.strip():
        return raw_status.strip().lower()
    session_state = getattr(session, "state", None)
    if session_state is not None:
        session_raw = _state_get(session_state, "temp:last_media_request_status", "")
        if isinstance(session_raw, str) and session_raw.strip():
            return session_raw.strip().lower()
    return ""


def _background_tradein_followup_question(state: Any, *, session: Any = None) -> str:
    waiting_for_upload = _background_vision_status(state, session=session) == "awaiting_media"
    preface = (
        "While it comes through, "
        if waiting_for_upload
        else "I'm checking the video now. While I do that, "
    )
    combined_text = " ".join(
        part.strip()
        for part in (
            _latest_user_turn_text(state, session=session),
            _recent_customer_context_text(state, session=session),
        )
        if isinstance(part, str) and part.strip()
    ).lower()
    if _looks_like_tradein_or_upgrade_request(combined_text):
        if not _STORAGE_PATTERN.search(combined_text):
            return f"{preface}which storage size would you like for the new phone?"
        return f"{preface}what's the battery health percentage on the phone?"
    return f"{preface}has the phone ever been exposed to water damage or had any repairs?"


def _tool_backed_analysis_available(state: Any, *, session: Any = None) -> bool:
    raw = _latest_tool_backed_analysis(state, session=session)
    if not isinstance(raw, dict):
        return False
    return any(
        bool(str(raw.get(key, "") or "").strip())
        for key in ("device_name", "brand", "condition")
    ) or bool(raw.get("details"))


def _valuation_tool_requires_media_analysis(
    *,
    tool_name: str,
    state: Any,
    session: Any = None,
) -> bool:
    if tool_name not in {"get_device_questionnaire_tool", "grade_and_value_tool"}:
        return False
    if not _is_voice_tradein_flow(state, session=session):
        return False
    background_status = _background_vision_status(state, session=session)
    handoff_state = _vision_media_handoff_state(state, session=session)
    if background_status == "ready" and _tool_backed_analysis_available(state, session=session):
        return False
    if background_status in {"awaiting_media", "running", "failed"}:
        return True
    if handoff_state in {"pending", "transferring"}:
        return True
    return not _tool_backed_analysis_available(state, session=session)


def _industry_instruction(industry_config: dict[str, Any], *, include_greeting: bool = True) -> str:
    name = industry_config.get("name", "General")
    line = f"Runtime config: industry='{name}'."
    if include_greeting:
        greeting = industry_config.get("greeting", "")
        if greeting:
            line += f" Preferred greeting='{greeting}'."
    return line


def _first_turn_opening(company_name: str, customer_name: str) -> str:
    """Return the exact opening sentence to lock first-turn identity."""
    if customer_name:
        return f"Welcome back, {customer_name}. This is ehkaitay from {company_name}."
    return f"Hello, this is ehkaitay from {company_name}."


def _resolve_first_turn_customer_name(state: State) -> str:
    """Return the preferred first-turn customer name, if present."""
    for key in ("user:name", "user:first_name", "app:customer_name", "temp:customer_name"):
        value = state.get(key)
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.split()).strip()
        if normalized:
            return normalized[:60]
    return ""


def _resolve_company_names(company_profile: dict[str, Any]) -> tuple[str, str]:
    """Return ``(display_name, spoken_name)`` for company identity."""
    display_name_raw = company_profile.get("display_name") if isinstance(company_profile, dict) else ""
    display_name = str(display_name_raw).strip() if isinstance(display_name_raw, str) else ""
    spoken_name_raw = company_profile.get("spoken_name") if isinstance(company_profile, dict) else ""
    spoken_name = str(spoken_name_raw).strip() if isinstance(spoken_name_raw, str) else ""
    legacy_name_raw = company_profile.get("name") if isinstance(company_profile, dict) else ""
    legacy_name = str(legacy_name_raw).strip() if isinstance(legacy_name_raw, str) else ""

    if not display_name:
        display_name = legacy_name or "our service desk"
    if not spoken_name:
        spoken_name = legacy_name or display_name
    return display_name, spoken_name


def _first_turn_greeting_instruction(
    *,
    company_profile: dict[str, Any],
    state: State,
) -> str:
    """Build strict first-turn greeting guidance with company personalization."""
    _display_name, spoken_name = _resolve_company_names(company_profile)
    customer_name = _resolve_first_turn_customer_name(state)
    opening = _first_turn_opening(spoken_name, customer_name)
    question = "How can I help you today?"

    return (
        "First-turn greeting policy: This is the first spoken response in the session. "
        "Identity lock: Your assistant name is exactly 'ehkaitay', pronounced 'eh-KAI-tay'. "
        "The middle syllable must sound exactly like 'kai', rhyming with 'sky'. "
        f"The spoken business name for this session is exactly '{spoken_name}'. "
        "Never substitute, paraphrase, or invent another assistant or company name. "
        "Never use the business name as your personal name. "
        f"Say this opening sentence exactly: '{opening}' "
        "Do not begin with phrases like 'welcome to <company>' and do not make "
        "the company sound like the speaker. "
        f"Immediately follow with exactly one short actionable question: '{question}' "
        "and nothing before the opening sentence. "
        "On this first turn, do NOT call any tool, do NOT transfer, do NOT mention "
        "specialists, and do NOT say you are connecting or routing the customer anywhere."
    )


def _opening_phase_callback_recovery_detail(state: State) -> str:
    """Return a dynamic opening-phase recovery message for blocked callbacks."""
    company_profile = state.get("app:company_profile")
    if not isinstance(company_profile, dict):
        company_profile = {}
    _display_name, spoken_name = _resolve_company_names(company_profile)
    customer_name = _resolve_first_turn_customer_name(state)
    opening = _first_turn_opening(spoken_name, customer_name)
    return (
        "OPENING PHASE — the caller has not spoken yet. "
        "Do NOT call any tools or transfer. "
        f"Your only next action is to speak the required opening greeting now: "
        f"'{opening} How can I help you today?' "
        "Do not retry request_callback."
    )


def _company_instruction(
    company_id: str,
    company_profile: dict[str, Any],
    company_knowledge: list[dict[str, Any]],
    *,
    channel: str = "",
) -> str:
    if not company_profile:
        return ""

    display_name, spoken_name = _resolve_company_names(company_profile)
    company_name = spoken_name if channel == "voice" else display_name
    overview = str(company_profile.get("overview", "")).strip()

    fact_pairs: list[str] = []
    facts = company_profile.get("facts")
    if isinstance(facts, dict):
        for key, value in facts.items():
            key_text = str(key).strip()
            value_text = str(value).strip()
            if not key_text or not value_text:
                continue
            fact_pairs.append(f"{key_text}={value_text}")
            if len(fact_pairs) >= 6:
                break

    knowledge_topics: list[str] = []
    for item in company_knowledge:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        text = str(item.get("text", "")).strip()
        excerpt = text[:100]
        if excerpt:
            knowledge_topics.append(f"{title}: {excerpt}")
        else:
            knowledge_topics.append(title)
        if len(knowledge_topics) >= 3:
            break

    parts = [
        (
            "Company context: "
            f"name='{company_name}'. "
            "Use this exact company name in customer-facing replies when needed. "
            "Do not invent alternate business or brand names. "
            f"If the customer asks what company you work for, who you work for, or the business name, answer with the exact company name '{company_name}'. "
            "Do not replace it with generic phrases like 'our company' or 'the business'. "
            "Never mention internal company IDs, slugs, tenant labels, or system identifiers."
        )
    ]
    if channel == "voice" and display_name != spoken_name:
        parts.append(
            f"Display vs pronunciation: The public-facing company display name is '{display_name}', "
            f"but when speaking aloud on voice calls, pronounce it as '{spoken_name}'."
        )
    if overview:
        parts.append(f"Overview='{overview[:320]}'.")
    if fact_pairs:
        parts.append("Facts: " + "; ".join(fact_pairs) + ".")
    if knowledge_topics:
        parts.append("Knowledge topics: " + "; ".join(knowledge_topics) + ".")
    parts.append(
        "Trust policy: For company-specific claims, ground responses in company facts, "
        "knowledge topics, or system query results. If data is unavailable, say so clearly."
    )
    return " ".join(parts)


def _text_identity_instruction(channel: str) -> str:
    normalized_channel = (channel or "").strip().lower()
    if normalized_channel not in {"whatsapp", "sms", "text"}:
        return ""
    return (
        "TEXT IDENTITY / SPELLING: In written replies, if you mention your assistant name, "
        "spell it exactly as 'Ekaette'. Never type the phonetic spelling 'ehkaitay' or "
        "'eh-KAI-tay'. Pronunciation guidance is only for speech, not text."
    )


def _handoff_instruction(state: State, agent_name: str, *, session: Any = None) -> str:
    """Return explicit continuity guidance for the first turn after a transfer."""
    target_agent = _state_get(state, "temp:pending_handoff_target_agent", "")
    normalized_agent = agent_name.strip() if isinstance(agent_name, str) else ""
    normalized_target = target_agent.strip() if isinstance(target_agent, str) else ""
    if not normalized_agent or not normalized_target or normalized_target != normalized_agent:
        return ""

    latest_user_raw = _state_get(state, "temp:pending_handoff_latest_user", "")
    latest_agent_raw = _state_get(state, "temp:pending_handoff_latest_agent", "")
    recent_customer_raw = _state_get(state, "temp:pending_handoff_recent_customer_context", "")

    latest_user = latest_user_raw.strip() if isinstance(latest_user_raw, str) else ""
    latest_agent = latest_agent_raw.strip() if isinstance(latest_agent_raw, str) else ""
    recent_customer = (
        recent_customer_raw.strip() if isinstance(recent_customer_raw, str) else ""
    )

    parts = [
        "LIVE HANDOFF — STRICT CONTINUITY RULES: "
        "This is the first response immediately after an internal transfer "
        f"to '{normalized_agent}'. "
        "You MUST NOT: greet, say hello, introduce yourself, say your name, "
        "say 'how can I help you', or repeat anything the previous agent said. "
        "You MUST: continue the same conversation seamlessly as if you are the "
        "same person. The customer should not notice the transfer at all.",
    ]
    if latest_user:
        parts.append(
            f"The customer's latest request before the transfer was: '{latest_user}'."
        )
    if latest_agent:
        parts.append(
            f"The previous agent's latest spoken line was: '{latest_agent}'. "
            "Acknowledge and advance from there without paraphrasing it back."
        )
    if recent_customer:
        parts.append(f"Recent customer-only context: '{recent_customer}'.")
    if not _has_explicit_transfer_request(
        state=state,
        session=session,
        target_agent=normalized_agent,
        latest_user=latest_user,
        recent_customer=recent_customer,
    ):
        parts.append(
            "Handoff context quality: The captured customer turn does not yet contain a "
            "concrete task. Ask one short clarifying question immediately instead of "
            "staying silent. Do not greet or re-introduce yourself."
        )
    return " ".join(parts)


def _outbound_delivery_instruction(state: State) -> str:
    """Tell the model the latest written delivery/send outcome."""
    raw_status = _state_get(state, "temp:last_outbound_delivery_status", "")
    status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
    if not status:
        return ""

    raw_channels = _state_get(state, "temp:last_outbound_delivery_channels", "")
    channels = raw_channels.strip() if isinstance(raw_channels, str) else ""
    raw_phone = _state_get(state, "temp:last_outbound_delivery_phone", "")
    phone = raw_phone.strip() if isinstance(raw_phone, str) else ""

    if status == "success":
        return (
            "Outbound delivery status: Written details were already sent successfully"
            f"{' via ' + channels if channels else ''}"
            f"{' to ' + phone if phone else ' to the caller'}. "
            "If the customer asks, confirm that they were sent. "
            "Do not claim there was a sending problem unless a later tool result fails."
        )

    if status == "partial":
        return (
            "Outbound delivery status: A written follow-up only partially succeeded"
            f"{' via ' + channels if channels else ''}. "
            "Be explicit about which channel worked, and offer the other channel as a fallback."
        )

    if status == "failure":
        return (
            "Outbound delivery status: The latest written follow-up attempt failed. "
            "Do not say it was sent. Explain the failure plainly and offer the alternative channel."
        )

    return ""


def _media_request_instruction(state: State, *, session: Any = None) -> str:
    status = _media_request_status(state, session=session)
    if status == "sending":
        return (
            "WhatsApp media request status: A send attempt is in progress. Do not say the "
            "message was already sent or ask the caller to check WhatsApp until the tool "
            "returns success."
        )
    if status == "sent":
        return (
            "WhatsApp media request status: The latest media request was sent successfully. "
            "You may briefly tell the caller to check WhatsApp or resend only if they say "
            "it did not arrive."
        )
    if status == "failure":
        return (
            "WhatsApp media request status: The latest media request did not send. Say that "
            "plainly and resend instead of pretending the message already arrived."
        )
    return ""


def _latest_analysis_instruction(
    state: State,
    agent_name: str,
    *,
    session: Any = None,
) -> str:
    """Safely summarize the latest tool-backed vision analysis for valuation flows.

    This must stay in the callback layer instead of static agent instructions so
    missing optional state can never crash ADK template expansion.
    """
    normalized_agent = agent_name.strip() if isinstance(agent_name, str) else ""
    if normalized_agent != "valuation_agent":
        return ""
    if _background_vision_status(state, session=session) in {"awaiting_media", "running"}:
        return ""

    raw = _latest_tool_backed_analysis(state, session=session)
    if not isinstance(raw, dict):
        if _uses_canonical_background_vision_path(state, session=session):
            background_status = _background_vision_status(state, session=session)
            if background_status == "awaiting_media":
                return (
                    "Vision handoff state: The WhatsApp media request already succeeded and the "
                    "current live swap flow is waiting for the customer's new upload. Do NOT "
                    "transfer to vision_agent for this same media, do not guess any visible "
                    "detail, and keep the call moving with one short non-visual follow-up "
                    "question at a time."
                )
            if background_status == "failed":
                return (
                    "Vision handoff state: The current live swap media could not be analyzed. "
                    "Do NOT transfer to vision_agent for this same media. Ask the customer to "
                    "resend the photo or video on WhatsApp and do not guess any visible detail."
                )
            return (
                "Vision handoff state: The current live swap media uses a canonical background "
                "analysis path. Do NOT transfer to vision_agent for this same media. If the "
                "customer asks about a visible attribute before the analysis is ready, explain "
                "that you are still checking and do not guess."
            )
        return (
            "Vision handoff state: No tool-backed vision analysis is currently available. "
            "If the customer asks you to confirm any visible attribute or a new photo/video "
            "arrives, transfer to vision_agent before answering."
        )

    device_name = str(raw.get("device_name", "") or "").strip()
    brand = str(raw.get("brand", "") or "").strip()
    device_color = str(raw.get("device_color", "") or "").strip().lower()
    condition = str(raw.get("condition", "") or "").strip()
    power_state = str(raw.get("power_state", "") or "").strip().lower()
    details = raw.get("details", {})
    if (
        not device_name
        and not brand
        and device_color in {"", "unknown"}
        and not condition
        and not details
        and power_state not in {"on", "off"}
    ):
        return (
            "Vision handoff state: No tool-backed vision analysis is currently available. "
            "If the customer asks you to confirm any visible attribute or a new photo/video "
            "arrives, transfer to vision_agent before answering."
        )

    summary_parts: list[str] = []
    if device_name:
        summary_parts.append(f"device_name='{device_name}'")
    if brand:
        summary_parts.append(f"brand='{brand}'")
    color_confirmed = bool(device_color and device_color != "unknown")
    if color_confirmed:
        summary_parts.append(f"device_color='{device_color}'")
    if condition:
        summary_parts.append(f"condition='{condition}'")
    if power_state in {"on", "off"}:
        summary_parts.append(f"power_state='{power_state}'")
    if isinstance(details, dict) and details:
        summary_parts.append(f"details={details!r}")

    instruction = (
        "Vision handoff state: Latest tool-backed vision analysis is available. "
        "Treat it as the source of truth for visible attributes and trade-in condition. "
        + ("Latest analysis: " + "; ".join(summary_parts) + "." if summary_parts else "")
    )
    instruction += (
        " Before you quote any trade-in price, briefly read back the grounded analysis "
        "in plain language first, then give the amount."
    )
    if not color_confirmed:
        instruction += (
            " The analysis did not confirm the device colour. "
            "If the customer asks about colour, say you cannot confirm it from the analysis "
            "and do not guess."
        )
    return instruction


def _canonical_background_vision_transfer_block(
    *,
    state: Any,
    session: Any,
    agent_name: str,
    target_agent: str,
) -> dict[str, Any] | None:
    if target_agent != "vision_agent":
        return None
    if not _uses_canonical_background_vision_path(state, session=session):
        return None

    background_status = _background_vision_status(state, session=session)
    handoff_state = _vision_media_handoff_state(state, session=session)
    logger.info(
        "transfer_blocked_canonical_background_vision agent=%s target=%s background_status=%s handoff_state=%s",
        agent_name,
        target_agent,
        background_status or "none",
        handoff_state or "none",
    )
    if background_status == "ready" and _tool_backed_analysis_available(state, session=session):
        detail = (
            "Transfer blocked. This live swap media already has a canonical tool-backed "
            "analysis result in shared state. Do not transfer to vision_agent for the same "
            "media again. Answer from the existing analysis, and if a visible attribute is "
            "missing or unknown, say you cannot confirm it."
        )
    elif background_status == "failed":
        detail = (
            "Transfer blocked. This live swap media uses a canonical background analysis "
            "path and that analysis failed. Do not transfer to vision_agent for the same "
            "media. Ask the customer to resend the photo or video on WhatsApp."
        )
    else:
        detail = (
            "Transfer blocked. This live swap media is already on the canonical background "
            "analysis path. Do not transfer to vision_agent for the same media. Keep the "
            "call moving with one short non-visual trade-in question while you wait."
        )
    return {
        "error": "canonical_background_vision_only",
        "detail": detail,
    }


def _background_vision_instruction(
    state: State,
    agent_name: str,
    *,
    session: Any = None,
) -> str:
    normalized_agent = agent_name.strip() if isinstance(agent_name, str) else ""
    if normalized_agent != "valuation_agent":
        return ""

    channel = _state_get(state, "app:channel", "")
    normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
    if normalized_channel != "voice":
        return ""

    status = _background_vision_status(state, session=session)
    if status == "awaiting_media":
        return (
            "BACKGROUND VISION ANALYSIS: The WhatsApp media request has already been sent and "
            "the call is waiting for the customer's current upload. Do NOT start the valuation "
            "questionnaire, do NOT quote a price yet, do NOT ask the customer to describe any "
            "visible condition, and do NOT transfer to vision_agent for this same media. Keep "
            "the call moving with one safe non-visual follow-up question at a time while the "
            "upload comes in."
        )
    if status == "running":
        return (
            "BACKGROUND VISION ANALYSIS: The customer's latest photo or video is already "
            "being analyzed in the background. Do NOT request media again and do NOT "
            "transfer to vision_agent for this same media. This background path is the only "
            "vision-analysis path for the current live swap flow. Keep the call moving with one safe "
            "non-visual follow-up question at a time while the analysis runs. Safe topics "
            "include the desired new device storage, desired new device colour, battery "
            "health, water exposure, repairs, Face ID or fingerprint status, and accessories. "
            "Do NOT ask the customer to describe any visible attribute such as colour, "
            "cracks, scratches, dents, screen condition, body condition, or overall appearance. "
            "Do NOT state any visual finding until the tool-backed analysis becomes available."
        )
    if status == "failed":
        return (
            "BACKGROUND VISION ANALYSIS: The latest background media analysis failed. "
            "Explain plainly that you could not check the media yet, ask the customer to "
            "resend the photo or video on WhatsApp, do not transfer to vision_agent for the "
            "same media, and do not invent any visual finding."
        )
    return ""


def _voice_tradein_media_instruction(
    state: State,
    agent_name: str,
    *,
    session: Any = None,
) -> str:
    normalized_agent = agent_name.strip() if isinstance(agent_name, str) else ""
    if normalized_agent != "valuation_agent":
        return ""

    channel = _state_get(state, "app:channel", "")
    normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
    if normalized_channel != "voice":
        return ""
    if _background_vision_status(state, session=session) in {"awaiting_media", "running", "ready"}:
        return ""

    raw_analysis = _state_get(state, "temp:last_analysis", None)
    if isinstance(raw_analysis, dict):
        device_name = str(raw_analysis.get("device_name", "") or "").strip()
        brand = str(raw_analysis.get("brand", "") or "").strip()
        condition = str(raw_analysis.get("condition", "") or "").strip()
        details = raw_analysis.get("details", {})
        if device_name or brand or condition or (isinstance(details, dict) and bool(details)):
            return ""

    latest_user_turn = _latest_user_turn_text(state, session=session)
    recent_customer = _recent_customer_context_text(state, session=session)
    combined_text = " ".join(
        part.strip()
        for part in (latest_user_turn, recent_customer)
        if isinstance(part, str) and part.strip()
    ).strip()
    if not _looks_like_tradein_or_upgrade_request(combined_text):
        return ""

    return (
        "VOICE TRADE-IN MEDIA COLLECTION (MANDATORY): The customer is in a swap/trade-in flow "
        "and no tool-backed vision analysis exists yet. Your next tool action must be "
        "request_media_via_whatsapp. Do NOT ask the caller to send media on the audio call, "
        "do NOT ask them to send a photo or video 'here', and do NOT continue the swap flow "
        "until the WhatsApp media request has been sent. Do NOT ask the customer to describe "
        "visible condition, colour, cracks, scratches, dents, or other cosmetic details verbally "
        "before the media request is sent."
    )


def _clear_pending_handoff_state(state: State) -> None:
    """Clear one-shot transfer continuity keys after the new agent speaks.

    IMPORTANT: Set to empty string, never delete (pop). ADK's
    inject_session_state raises KeyError if a template variable referenced
    in an agent instruction is missing from state entirely. The sub-agent
    instructions reference these keys via ``{temp:pending_handoff_*}``
    placeholders, so the keys must always exist.
    """
    keys = (
        "temp:pending_handoff_target_agent",
        "temp:pending_handoff_latest_user",
        "temp:pending_handoff_latest_agent",
        "temp:pending_handoff_recent_customer_context",
        "temp:pending_transfer_bootstrap_target_agent",
        "temp:pending_transfer_bootstrap_reason",
    )
    for key in keys:
        try:
            state[key] = ""
        except Exception:
            logger.debug("Failed to clear pending handoff key %s", key, exc_info=True)


async def before_model_inject_config(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Inject runtime industry + company context into system instruction.

    The greeting is only injected on the first model turn (before
    ``temp:greeted`` is set) to prevent the model from re-greeting
    every turn.
    """
    instruction_lines: list[str] = []
    agent_name = getattr(callback_context, "agent_name", "") or ""
    _ensure_instruction_state_defaults(callback_context.state)

    already_greeted = _is_greeted_state(
        callback_context.state,
        session=getattr(callback_context, "session", None),
    )

    company_profile = callback_context.state.get("app:company_profile")
    if not isinstance(company_profile, dict):
        company_profile = {}

    channel = _state_get(callback_context.state, "app:channel", "")
    normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""

    industry_config = callback_context.state.get("app:industry_config")
    if isinstance(industry_config, dict):
        instruction_lines.append(_industry_instruction(industry_config, include_greeting=False))
        if not already_greeted:
            if normalized_channel in {"", "voice"}:
                instruction_lines.append(
                    _first_turn_greeting_instruction(
                        company_profile=company_profile,
                        state=callback_context.state,
                    )
                )
            else:
                text_identity_line = _text_identity_instruction(normalized_channel)
                if text_identity_line:
                    instruction_lines.append(text_identity_line)

    has_runtime_context = isinstance(industry_config, dict)

    if already_greeted:
        # Build conversation recovery context so the model picks up mid-call
        # even after a Live API crash/reconnect that wipes conversation history.
        last_agent_turn = _state_get(callback_context.state, "temp:last_agent_turn", "")
        last_user_turn = _state_get(callback_context.state, "temp:last_user_turn", "")
        continuity_parts = [
            "CONVERSATION CONTINUITY — STRICT RULES: "
            "A greeting has already been delivered in this session. "
            "Do NOT greet, say hello, say 'how can I help you today', "
            "or introduce yourself again under any circumstances. "
            "Resume the conversation naturally from where it left off.",
        ]
        if isinstance(last_agent_turn, str) and last_agent_turn.strip():
            continuity_parts.append(
                f"Your last spoken line was: '{last_agent_turn.strip()[:200]}'. "
                "Continue from there."
            )
        if isinstance(last_user_turn, str) and last_user_turn.strip():
            continuity_parts.append(
                f"The customer last said: '{last_user_turn.strip()[:200]}'."
            )
        if not last_agent_turn and not last_user_turn:
            continuity_parts.append(
                "Ask the customer what they need help with today, but do NOT "
                "re-introduce yourself or repeat a greeting."
            )
        instruction_lines.append(" ".join(continuity_parts))
        instruction_lines.append(
            "Style guard: Do not re-introduce your role (for example, never say "
            "'I am the support agent'). Keep responses task-focused."
        )
        has_runtime_context = True

    company_id_raw = callback_context.state.get("app:company_id")
    company_id = company_id_raw if isinstance(company_id_raw, str) else "default"

    company_knowledge_raw = callback_context.state.get("app:company_knowledge")
    company_knowledge: list[dict[str, Any]] = []
    if isinstance(company_knowledge_raw, list):
        company_knowledge = [
            item for item in company_knowledge_raw if isinstance(item, dict)
        ]

    company_line = _company_instruction(
        company_id,
        company_profile,
        company_knowledge,
        channel=normalized_channel,
    )
    if company_line:
        instruction_lines.append(company_line)
        has_runtime_context = True

    text_identity_line = _text_identity_instruction(normalized_channel)
    if text_identity_line:
        instruction_lines.append(text_identity_line)
        has_runtime_context = True

    handoff_line = _handoff_instruction(
        callback_context.state,
        agent_name,
        session=getattr(callback_context, "session", None),
    )
    if handoff_line:
        instruction_lines.append(handoff_line)
        has_runtime_context = True

    router_voice_swap_line = _router_voice_swap_handoff_instruction(
        callback_context.state,
        agent_name,
        session=getattr(callback_context, "session", None),
    )
    if router_voice_swap_line:
        instruction_lines.append(router_voice_swap_line)
        has_runtime_context = True

    outbound_line = _outbound_delivery_instruction(callback_context.state)
    if outbound_line:
        instruction_lines.append(outbound_line)
        has_runtime_context = True

    media_request_line = _media_request_instruction(
        callback_context.state,
        session=getattr(callback_context, "session", None),
    )
    if media_request_line:
        instruction_lines.append(media_request_line)
        has_runtime_context = True

    latest_analysis_line = _latest_analysis_instruction(
        callback_context.state,
        agent_name,
        session=getattr(callback_context, "session", None),
    )
    if latest_analysis_line:
        instruction_lines.append(latest_analysis_line)
        has_runtime_context = True

    background_vision_line = _background_vision_instruction(
        callback_context.state,
        agent_name,
        session=getattr(callback_context, "session", None),
    )
    if background_vision_line:
        instruction_lines.append(background_vision_line)
        has_runtime_context = True

    _cb_session_obj = getattr(callback_context, "session", None)
    _cb_session_id = getattr(_cb_session_obj, "id", "") if _cb_session_obj else ""
    voice_tradein_media_line = _voice_tradein_media_instruction(
        callback_context.state,
        agent_name,
        session=_cb_session_obj,
    )
    if voice_tradein_media_line:
        instruction_lines.append(voice_tradein_media_line)
        has_runtime_context = True
    if _is_callback_leg(callback_context.state, session_id_override=str(_cb_session_id or "")):
        instruction_lines.append(
            "CALLBACK LEG — OUTBOUND FOLLOW-UP CALL: "
            "This is an outbound call back to the customer. "
            "Do NOT call request_callback — you are ALREADY on the callback. "
            "Do NOT say 'as requested' or imply the customer explicitly asked for a callback "
            "unless runtime context clearly confirms that. "
            "Your FIRST words must be a warm greeting, for example "
            "'Hi, this is ehkaitay calling from the store.' "
            "Then briefly say you are following up and continue helping. "
            "NEVER call request_callback on this call — it will fail and waste time."
        )
        has_runtime_context = True

    # Inject global lessons (Tier 2 learning — cross-session behavioral rules)
    global_lessons = callback_context.state.get("app:global_lessons")
    if isinstance(global_lessons, list) and global_lessons:
        lessons_text = format_lessons_for_instruction(
            global_lessons, agent_name=agent_name,
        )
        if lessons_text:
            instruction_lines.append(lessons_text)
            has_runtime_context = True

    if has_runtime_context:
        if normalized_channel == "voice":
            _display_name, spoken_name = _resolve_company_names(company_profile)
            voice_opening_complete = _is_voice_opening_complete(
                callback_context.state,
                session=getattr(callback_context, "session", None),
            )
            # This intentionally overlaps with the router's static voice supplement:
            # the router carries the base voice playbook, while this runtime check
            # reinforces the low-latency filler rule for any voice-session agent.
            instruction_lines.append(
                "NIGERIAN ACCENT AND PERSONA (MANDATORY): You are ehkaitay, pronounced "
                "'eh-KAI-tay'. The middle syllable must sound exactly like 'kai', rhyming "
                "with 'sky'. You are a warm, confident Nigerian virtual assistant and Lagos "
                "business professional. You MUST speak in Nigerian English at all times. Speak with rhythmic, buoyant pacing. Keep "
                "consonants sharp and vowels rounded, letting your sentences end with a natural "
                "upward lilt. Use everyday Nigerian phrasing naturally ('Ehen', 'No problem at all', "
                "'Absolutely', 'We go sort you out'). If the customer switches into Pidgin, respond "
                "with clear, respectful Pidgin. Do NOT sound generic, American, or British."
            )
            instruction_lines.append(
                "NIGERIAN HOSTING (NO TRANSFERS): Good Nigerian hospitality means handling things "
                "personally. NEVER let the customer know you are transferring them to another agent. "
                "NEVER say 'I will transfer you', 'routing you to', 'let me pass you to', "
                "'let me connect you', 'I'm just connecting you now', or 'valuation specialist'. "
                "Instead, say things like 'Let me sort that out for you right now', 'Let me get that "
                "set up for you', or 'I'll take care of that now'. The customer must feel like YOU "
                "are doing all the work."
            )
            instruction_lines.append(
                f"VOICE PRONUNCIATION: When saying the business name aloud, pronounce it as "
                f"'{spoken_name}'."
            )
            instruction_lines.append(
                "NIGERIAN PACING (NO SILENCE): On a phone call, silence feels like a dropped "
                "connection. You MUST speak a conversational filler BEFORE any tool call or "
                "agent transfer. Ask an engaging follow-up question (e.g., 'Let me look that up — "
                "do you have a colour preference?') to keep the warm connection. Generate "
                "spoken text FIRST, then the tool call, in the same turn. Never leave more than "
                "2 seconds of silence. Always say 'naira' after prices (translate 'NGN' aloud to 'naira')."
            )
            instruction_lines.append(
                "VOICE REPAIR AND UNCLEAR INPUT: If the customer's latest turn is a connection "
                "check like 'can you hear me?', a request to slow down or repeat yourself, a "
                "greeting after the opening, a self-introduction, a short acknowledgement, or "
                "garbled/unclear speech, do NOT transfer and do NOT call tools. Stay on the "
                "current agent, answer briefly, and ask at most one short clarifying question."
            )
            if voice_opening_complete:
                instruction_lines.append(
                    "NIGERIAN HOSPITALITY (CALLBACKS): If the customer asks to be called back, says they "
                    "do not have enough airtime, or are out of time, be a gracious host. Use "
                    "request_callback immediately to save their time. Do NOT interrogate them with "
                    "follow-up questions about the callback. Just warmly tell them you'll call back shortly "
                    "and end the topic."
                )
            instruction_lines.append(
                "NIGERIAN FAREWELLS: When the conversation naturally concludes or the customer says "
                "goodbye, give one brief, warm closing line and then immediately use end_call. "
                "Do not remain silent on the line, and don't drag out the goodbye."
            )
            if (
                bool(_state_get(callback_context.state, "temp:callback_requested", False))
                and not _is_callback_leg(callback_context.state, session_id_override=str(_cb_session_id or ""))
            ):
                instruction_lines.append(
                    "CALLBACK WRAP-UP (MANDATORY): A callback has already been registered. "
                    "Say ONE brief warm sentence confirming you will call them back on this "
                    "same number shortly, then IMMEDIATELY call end_call. Example: "
                    "'No wahala, I'll ring you right back — talk soon!' then call end_call. "
                    "Do NOT ask follow-up questions, do NOT start new topics, do NOT keep talking."
                )

    if not instruction_lines:
        return None

    instruction_line = "\n".join(instruction_lines)
    if llm_request.config is None:
        llm_request.config = types.GenerateContentConfig(
            system_instruction=instruction_line
        )
        return None

    existing = llm_request.config.system_instruction
    if existing is None:
        llm_request.config.system_instruction = instruction_line
    elif isinstance(existing, str) and instruction_line not in existing:
        llm_request.config.system_instruction = (
            f"{existing}\n\n{instruction_line}"
        )
    return None


async def after_model_valuation_sanity(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> None:
    """Soft checks for valuation responses to reduce pricing drift."""
    # Resolve authoritative session ID for callback-leg detection.
    _am_so = getattr(callback_context, "session", None)
    _am_session_id = str(getattr(_am_so, "id", "") or "") if _am_so else ""

    # Lock greeting only after the first real model response, so agent transfers
    # before speaking do not accidentally suppress the initial greeting.
    # In native-audio mode the response may contain only audio inline_data
    # (no text parts), so we check _response_has_content as well.
    # Belt-and-suspenders: track model turn count and mark greeted after the
    # second turn — in native-audio mode, audio isn't in LlmResponse, so
    # has_content may be False even though the greeting was delivered.
    text = _response_text(llm_response)
    has_content = text or _response_has_content(llm_response)
    channel = _state_get(callback_context.state, "app:channel", "")
    normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
    _turn_count = int(_state_get(callback_context.state, "temp:model_turn_count", 0)) + 1
    callback_context.state["temp:model_turn_count"] = _turn_count
    if not bool(callback_context.state.get("temp:greeted", False)):
        if has_content or _turn_count > 1:
            callback_context.state["temp:greeted"] = True
    # On turn 2+, the model has processed at least one customer message.
    # Set the canonical opening-phase flag so _is_voice_opening_complete
    # can allow transfers even when stream_tasks user-turn markers (written
    # to a separate in-memory dict) are not visible in tool_context.state.
    if _turn_count >= 2 and not bool(
        callback_context.state.get("temp:opening_phase_complete", False)
    ):
        callback_context.state["temp:opening_phase_complete"] = True
    # Bridge guard-relevant flags from session.state into ADK-visible state
    # so that subsequent tool callbacks (transfer guards) see them.
    if hasattr(callback_context, "session"):
        _s_state = getattr(callback_context.session, "state", None)
        if isinstance(_s_state, dict):
            for _bk in (
                "temp:last_user_turn",
                "temp:first_user_turn_started",
                "temp:first_user_turn_complete",
                "temp:opening_phase_complete",
                "temp:opening_greeting_complete",
            ):
                _bv = _s_state.get(_bk)
                if _bv in (None, "", False):
                    continue
                if isinstance(_bv, bool):
                    # Boolean: upgrade-only (never downgrade True → False)
                    if not bool(callback_context.state.get(_bk, False)):
                        callback_context.state[_bk] = _bv
                elif isinstance(_bv, str) and _bv.strip():
                    # String: session.state is canonical when non-empty
                    callback_context.state[_bk] = _bv
    if normalized_channel in {"whatsapp", "sms", "text"} and text:
        normalized_text = _normalize_text_assistant_name(text)
        if normalized_text != text and _rewrite_response_text(llm_response, normalized_text):
            text = normalized_text
            has_content = True
    if normalized_channel == "voice" and text and _contains_transfer_disclosure(text):
        replacement = _transfer_disclosure_replacement(
            state=callback_context.state,
            session=getattr(callback_context, "session", None),
            agent_name=callback_context.agent_name,
        )
        if replacement and replacement != text and _rewrite_response_text(llm_response, replacement):
            text = replacement
            has_content = True
            logger.warning(
                "voice transfer disclosure rewritten agent=%s",
                callback_context.agent_name,
            )
    pending_target = _state_get(callback_context.state, "temp:pending_handoff_target_agent", "")
    if (
        has_content
        and isinstance(pending_target, str)
        and pending_target.strip() == callback_context.agent_name
    ):
        _clear_pending_handoff_state(callback_context.state)

    if (
        text
        and normalized_channel == "voice"
        and _response_commits_to_callback(text)
        and not bool(_state_get(callback_context.state, "temp:callback_requested", False))
        and not _is_callback_leg(callback_context.state, session_id_override=_am_session_id)
    ):
        caller_phone = resolve_caller_phone_from_context(callback_context)
        tenant_id = _state_get(callback_context.state, "app:tenant_id", "public")
        company_id = _state_get(
            callback_context.state,
            "app:company_id",
            "ekaette-electronics",
        )
        if isinstance(caller_phone, str) and caller_phone.strip():
            result = service_voice.register_callback_request(
                phone=caller_phone.strip(),
                tenant_id=str(tenant_id or "public"),
                company_id=str(company_id or "ekaette-electronics"),
                source="voice_ai_auto_callback",
                reason="Auto-queued from spoken callback commitment",
                trigger_after_hangup=True,
            )
            status = str(result.get("status", "")).strip().lower()
            if status in {"pending", "queued", "cooldown"}:
                callback_context.state["temp:callback_requested"] = True
                _queue_end_after_speaking_control(
                    callback_context.state,
                    reason="callback_registered",
                )
                logger.info(
                    "Auto-queued callback from spoken commitment agent=%s phone=%s status=%s",
                    callback_context.agent_name,
                    caller_phone.strip(),
                    status,
                )
            else:
                logger.warning(
                    "Auto-callback queue failed after spoken commitment agent=%s phone=%s result=%r",
                    callback_context.agent_name,
                    caller_phone.strip(),
                    result,
                )

    if (
        text
        and normalized_channel == "voice"
        and _response_commits_to_callback(text)
        and bool(_state_get(callback_context.state, "temp:callback_requested", False))
        and not _is_callback_leg(callback_context.state, session_id_override=_am_session_id)
    ):
        _queue_end_after_speaking_control(
            callback_context.state,
            reason="callback_acknowledged",
        )

    if (
        callback_context.agent_name == "valuation_agent"
        and normalized_channel == "voice"
        and text
        and _background_vision_status(
            callback_context.state,
            session=getattr(callback_context, "session", None),
        ) in {"awaiting_media", "running"}
        and _asks_for_visible_condition_details(text)
    ):
        replacement = _background_tradein_followup_question(
            callback_context.state,
            session=getattr(callback_context, "session", None),
        )
        if _rewrite_response_text(llm_response, replacement):
            text = replacement
            logger.warning(
                "valuation_agent visible-condition question rewritten during background analysis"
            )

    if callback_context.agent_name == "valuation_agent" and normalized_channel == "voice" and text:
        active_session = getattr(callback_context, "session", None)
        latest_user_turn = _latest_user_turn_text(callback_context.state, session=active_session)
        if _asks_to_confirm_device_color(latest_user_turn):
            analysis = _latest_tool_backed_analysis(
                callback_context.state,
                session=active_session,
            )
            replacement = _grounded_color_confirmation_replacement(
                analysis=analysis,
                state=callback_context.state,
                session=active_session,
                original_text=text,
            )
            if replacement and replacement != text and _rewrite_response_text(llm_response, replacement):
                text = replacement
                logger.warning(
                    "valuation_agent color confirmation rewritten from grounded analysis state"
                )

    if (
        callback_context.agent_name == "valuation_agent"
        and normalized_channel == "voice"
        and text
        and _claims_whatsapp_delivery(text)
        and _media_request_status(
            callback_context.state,
            session=getattr(callback_context, "session", None),
        )
        != "sent"
    ):
        replacement = (
            "I'll send the WhatsApp message now so you can reply there with the photo or "
            "short video. If it doesn't arrive in a few seconds, tell me and I'll resend it."
        )
        if _rewrite_response_text(llm_response, replacement):
            text = replacement
            logger.warning(
                "valuation_agent whatsapp-delivery claim rewritten without tool-backed send state"
            )

    if callback_context.agent_name != "valuation_agent":
        return None

    offer_amount = callback_context.state.get("temp:last_offer_amount")
    if not isinstance(offer_amount, (int, float)) or offer_amount <= 0:
        return None

    if not text:
        return None

    active_session = getattr(callback_context, "session", None)
    if (
        normalized_channel == "voice"
        and _is_voice_tradein_flow(callback_context.state, session=active_session)
        and _looks_like_offer_response(text, offer_amount)
    ):
        analysis = _latest_tool_backed_analysis(
            callback_context.state,
            session=active_session,
        )
        if not _analysis_supports_tradein_offer(analysis):
            replacement = _unguarded_tradein_offer_replacement(
                state=callback_context.state,
                session=active_session,
            )
            if _rewrite_response_text(llm_response, replacement):
                text = replacement
                logger.warning(
                    "valuation_agent offer rewritten without grounded trade-in analysis"
                )
                return None
        else:
            replacement = _tradein_offer_replacement(
                offer_amount=offer_amount,
                analysis=analysis,
                original_text=text,
            )
            if replacement and replacement != text and _rewrite_response_text(llm_response, replacement):
                text = replacement
                logger.info(
                    "valuation_agent offer normalized to lead with grounded analysis summary"
                )

    if "₦" not in text and "NGN" not in text.upper():
        logger.warning(
            "valuation_agent response missing NGN marker (offer=%s)",
            offer_amount,
        )

    parsed_values: list[int] = []
    for token in _PRICE_PATTERN.findall(text):
        parsed = token.replace(",", "")
        if parsed.isdigit():
            parsed_values.append(int(parsed))
    if any(value > int(offer_amount) * 2 for value in parsed_values):
        logger.warning(
            "valuation_agent response contains unusually high number(s): %s",
            parsed_values,
        )
    return None


async def before_tool_capability_guard(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    """Block tool calls when the session lacks the required capability.

    Returns None to allow the call, or a dict error to short-circuit.
    Tools not in TOOL_CAPABILITY_MAP are always allowed.
    When ``app:capabilities`` is absent from state, all tools are allowed
    (backward-compatible / compat mode).
    """
    # Hard-block request_callback on callback legs to prevent infinite loops.
    # Use session.id from the ADK session object as the authoritative source
    # because app:session_id in state may not yet be populated when the model
    # fires its first tool call on a fresh callback session.
    _session_obj = getattr(tool_context, "session", None)
    _adk_session_id = getattr(_session_obj, "id", "") if _session_obj else ""
    if tool.name == "request_callback" and _is_callback_leg(
        tool_context.state, session_id_override=str(_adk_session_id or ""),
    ):
        logger.warning(
            "Blocked request_callback on callback leg agent=%s session_id=%s",
            tool_context.agent_name,
            _adk_session_id,
        )
        return {
            "status": "error",
            "error": "already_on_callback",
            "detail": "You are already on a callback call. Do not request another callback.",
        }

    if tool.name == "request_callback" and not _request_callback_has_explicit_intent(
        tool_context.state,
        args,
    ):
        latest_user_raw = _state_get(tool_context.state, "temp:last_user_turn", "")
        latest_user = latest_user_raw.strip() if isinstance(latest_user_raw, str) else ""
        channel_raw = _state_get(tool_context.state, "app:channel", "")
        normalized_channel = channel_raw.strip().lower() if isinstance(channel_raw, str) else ""
        opening_phase_active = (
            normalized_channel == "voice"
            and not latest_user
            and not _is_voice_opening_complete(
                tool_context.state,
                session=getattr(tool_context, "session", None),
            )
        )
        logger.warning(
            "Blocked request_callback without explicit user intent agent=%s latest_user=%r args=%s",
            tool_context.agent_name,
            latest_user[:160],
            sorted(args.keys()),
        )
        detail = (
            _opening_phase_callback_recovery_detail(tool_context.state)
            if opening_phase_active
            else (
                "Callback request blocked. The customer has not explicitly asked for a callback yet. "
                "First greet and help the caller. Only request a callback after the caller clearly asks for one."
            )
        )
        return {
            "status": "error",
            "error": "callback_intent_required",
            "detail": detail,
        }

    if _valuation_tool_requires_media_analysis(
        tool_name=tool.name,
        state=tool_context.state,
        session=getattr(tool_context, "session", None),
    ):
        logger.warning(
            "Blocked %s until tool-backed media analysis is ready agent=%s",
            tool.name,
            tool_context.agent_name,
        )
        if tool.name == "grade_and_value_tool":
            detail = (
                "Pricing blocked. The customer's latest photo or video has not finished "
                "tool-backed analysis yet. Ask one short non-visual trade-in question "
                "while you wait, and do not give an offer yet."
            )
        else:
            detail = (
                "Questionnaire blocked. The customer's latest photo or video has not "
                "finished tool-backed analysis yet. Ask one short non-visual trade-in "
                "question while you wait instead of starting the valuation questionnaire."
            )
        return {
            "status": "error",
            "error": "vision_analysis_pending",
            "detail": detail,
        }

    required_cap = TOOL_CAPABILITY_MAP.get(tool.name)
    if required_cap is None:
        return None

    capabilities = tool_context.state.get("app:capabilities")
    if not isinstance(capabilities, list):
        return None  # Compat mode — no guard

    if required_cap in capabilities:
        return None

    logger.warning(
        "capability_blocked agent=%s tool=%s required=%s capabilities=%s",
        tool_context.agent_name,
        tool.name,
        required_cap,
        capabilities,
    )
    return {
        "error": "capability_not_enabled",
        "tool": tool.name,
        "required": required_cap,
    }


def _guard_transfer_before_greeting(
    *,
    state: Any,
    session: Any,
    agent_name: str,
    target_agent: str,
) -> dict[str, Any] | None:
    """Return an actionable error when voice transfer is attempted too early."""
    # Hydrate callback-visible state from session.state so that flags
    # written by stream_tasks (opening_phase_complete, last_user_turn, etc.)
    # are visible to the guard decision.
    _hydrate_voice_opening_state_from_session(state, session=session)
    channel = _state_get(state, "app:channel", "")
    is_voice = isinstance(channel, str) and channel.strip().lower() == "voice"
    opening_complete = _is_voice_opening_complete(state, session=session)
    latest_user_turn = _latest_user_turn_text(state, session=session)
    tradein_fast_path = (
        is_voice
        and target_agent == "valuation_agent"
        and _is_greeted_state(state, session=session)
        and _looks_like_tradein_or_upgrade_request(latest_user_turn)
    )
    if not is_voice or opening_complete or tradein_fast_path:
        state["temp:last_blocked_transfer_signature"] = ""
        state["temp:last_blocked_transfer_attempts"] = 0
        return None

    signature = _hallucinated_transfer_signature(state, target_agent, session=session)
    previous_signature = str(_state_get(state, "temp:last_blocked_transfer_signature", "") or "")
    previous_attempts_raw = _state_get(state, "temp:last_blocked_transfer_attempts", 0)
    try:
        previous_attempts = int(previous_attempts_raw or 0)
    except (TypeError, ValueError):
        previous_attempts = 0
    current_attempts = previous_attempts + 1 if previous_signature == signature else 1
    state["temp:last_blocked_transfer_signature"] = signature
    state["temp:last_blocked_transfer_attempts"] = current_attempts

    blocked_count = int(state.get("temp:greeting_block_count", 0))
    blocked_count += 1
    state["temp:greeting_block_count"] = blocked_count

    logger.warning(
        "transfer_blocked_before_greeting agent=%s target=%s attempt=%d",
        agent_name,
        target_agent,
        blocked_count,
    )
    if current_attempts > 1:
        return {
            "error": "routing_retry_suppressed",
            "detail": (
                "Do not retry the same transfer again for this turn. Respond directly to the "
                "caller or ask one short clarifying question."
            ),
        }
    return {
        "error": "greeting_required",
        "detail": (
            "Transfer blocked. Finish your opening with the caller first. "
            "You must greet the caller aloud and speak to their first real reply "
            "before transferring."
        ),
    }


def _guard_transfer_without_explicit_request(
    *,
    state: Any,
    session: Any,
    agent_name: str,
    target_agent: str,
) -> dict[str, Any] | None:
    """Require a concrete customer task before specialist handoff on voice calls."""
    _hydrate_voice_opening_state_from_session(state, session=session)
    channel = _state_get(state, "app:channel", "")
    is_voice = isinstance(channel, str) and channel.strip().lower() == "voice"
    if not is_voice:
        return None

    canonical_block = _canonical_background_vision_transfer_block(
        state=state,
        session=session,
        agent_name=agent_name,
        target_agent=target_agent,
    )
    if canonical_block is not None:
        return canonical_block

    latest_user_turn = _latest_user_turn_text(state, session=session)
    recent_customer = _recent_customer_context_text(state, session=session)
    if _has_explicit_transfer_request(
        state=state,
        session=session,
        target_agent=target_agent,
        latest_user=latest_user_turn,
        recent_customer=recent_customer,
    ):
        return None

    logger.warning(
        "transfer_blocked_without_explicit_request agent=%s target=%s latest_user=%r recent_customer=%r",
        agent_name,
        target_agent,
        latest_user_turn[:160],
        recent_customer[:160],
    )
    return {
        "error": "explicit_request_required",
        "detail": (
            "Transfer blocked. The customer has not given a concrete request yet. "
            "Respond directly or ask one short clarifying question before handing off."
        ),
    }


async def before_tool_agent_transfer_guard(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    """Block transfer-to-agent tool calls that violate session isolation policy."""
    tool_name = str(getattr(tool, "name", "") or "")
    target_agent = _tool_transfer_target_agent_name(tool_name, args)
    if target_agent is None:
        return None

    # Block transfers before greeting — forces the router to greet the
    # customer before handing off to a sub-agent.
    # NOTE: In Live API mode, ADK's base_llm_flow.py mistakenly closes
    # the Live connection when it sees ANY function_response named
    # "transfer_to_agent" — even blocked ones.  We patch that in
    # app/agents/tool_scheduling.py so this guard works safely.
    guard_result = _guard_transfer_before_greeting(
        state=tool_context.state,
        session=getattr(tool_context, "session", None),
        agent_name=tool_context.agent_name,
        target_agent=target_agent,
    )
    if guard_result is not None:
        return guard_result
    guard_result = _guard_transfer_without_explicit_request(
        state=tool_context.state,
        session=getattr(tool_context, "session", None),
        agent_name=tool_context.agent_name,
        target_agent=target_agent,
    )
    if guard_result is not None:
        return guard_result

    enabled_agents = resolve_enabled_agents_from_state(tool_context.state)
    if enabled_agents is None or target_agent in enabled_agents:
        return None

    logger.warning(
        "agent_isolation_blocked phase=before_tool caller=%s tool=%s target_agent=%s enabled_agents=%s",
        tool_context.agent_name,
        tool_name,
        target_agent,
        enabled_agents,
    )
    payload = _agent_not_enabled_payload(
        state=tool_context.state,
        agent_name=target_agent,
        allowed_agents=enabled_agents,
    )
    payload["error"] = "agent_not_enabled"
    payload["tool"] = tool_name
    return payload


async def before_tool_log(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> None:
    """Structured log before each tool invocation."""
    redacted_args = dict(args)
    if "image_base64" in redacted_args:
        redacted_args["image_base64"] = "<redacted>"
    if tool.name == "search_catalog":
        query_raw = redacted_args.get("query")
        category_raw = redacted_args.get("category")
        query = query_raw if isinstance(query_raw, str) else ""
        category = category_raw if isinstance(category_raw, str) else ""
        storage_tokens = sorted(
            {match.group(0).lower() for match in _STORAGE_PATTERN.finditer(query)}
        )
        logger.info(
            "tool_start agent=%s tool=%s query=%r category=%r storage=%s args=%s",
            tool_context.agent_name,
            tool.name,
            query[:160],
            category[:80],
            storage_tokens,
            sorted(redacted_args.keys()),
        )
        return None
    if tool.name == "request_media_via_whatsapp":
        session = getattr(tool_context, "session", None)
        _persist_runtime_hint_state(
            state=tool_context.state,
            session=session,
            key="temp:last_media_request_status",
            value="sending",
        )
        _persist_runtime_hint_state(
            state=tool_context.state,
            session=session,
            key="temp:pending_media_request_voice_ack",
            value="",
        )
        for key in (
            "temp:last_outbound_delivery_status",
            "temp:last_outbound_delivery_channels",
            "temp:last_outbound_delivery_phone",
        ):
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=session,
                key=key,
                value="",
            )
    target_agent = _tool_transfer_target_agent_name(tool.name, args)
    if target_agent is not None:
        session = getattr(tool_context, "session", None)
        latest_user = _latest_user_turn_text(tool_context.state, session=session)
        latest_agent = _latest_agent_turn_text(tool_context.state, session=session)
        recent_customer = _recent_customer_context_text(tool_context.state, session=session)
        signature = f"{target_agent}|{latest_user}|{latest_agent}|{recent_customer}"
        tool_context.state["temp:last_transfer_handoff_signature"] = signature
        tool_context.state["temp:pending_handoff_target_agent"] = target_agent
        tool_context.state["temp:pending_handoff_latest_user"] = latest_user
        tool_context.state["temp:pending_handoff_latest_agent"] = latest_agent
        tool_context.state["temp:pending_handoff_recent_customer_context"] = recent_customer
        if target_agent == "vision_agent" and _vision_media_handoff_state(
            tool_context.state, session=session
        ) == "pending":
            _persist_voice_string_state(
                state=tool_context.state,
                session=session,
                key="temp:vision_media_handoff_state",
                value="transferring",
            )
        logger.info(
            "Prepared transfer handoff target=%s has_user=%s has_agent=%s",
            target_agent,
            bool(latest_user),
            bool(latest_agent),
        )
        channel = _state_get(tool_context.state, "app:channel", "")
        is_voice = isinstance(channel, str) and channel.strip().lower() == "voice"
        if is_voice:
            bootstrap_reason = (
                "voice_tradein_handoff"
                if target_agent == "valuation_agent"
                and _is_voice_tradein_flow(tool_context.state, session=session)
                else "voice_handoff"
            )
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=session,
                key="temp:pending_transfer_bootstrap_target_agent",
                value=target_agent,
            )
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=session,
                key="temp:pending_transfer_bootstrap_reason",
                value=bootstrap_reason,
            )
    logger.info(
        "tool_start agent=%s tool=%s args=%s",
        tool_context.agent_name,
        tool.name,
        sorted(redacted_args.keys()),
    )
    return None


async def before_tool_capability_guard_and_log(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    """Enforce capability guards, then emit structured tool-start logs.

    Returning a dict short-circuits the tool call in ADK. Logging is skipped when
    the tool is blocked so denied operations do not emit a misleading tool_start.
    """
    blocked = await before_tool_agent_transfer_guard(tool, args, tool_context)
    if blocked is not None:
        return blocked
    blocked = await before_tool_capability_guard(tool, args, tool_context)
    if blocked is not None:
        return blocked
    # Inject caller phone from ephemeral registry when session state lacks it.
    # ADK live-streaming mode sometimes fails to surface user:caller_phone
    # in the tool context's session state.
    if tool.name in _OUTBOUND_CALLER_TOOLS:
        _maybe_inject_caller_phone(tool_context)
    await before_tool_log(tool, args, tool_context)
    return None


def _tool_error_server_message(effective_result: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool callback error payloads into structured server messages."""
    code_raw = effective_result.get("code")
    code = str(code_raw).strip() if isinstance(code_raw, str) and code_raw.strip() else ""
    error_raw = effective_result.get("error")
    message_raw = effective_result.get("message")
    detail_raw = effective_result.get("detail")

    if code:
        message = (
            str(message_raw).strip()
            if isinstance(message_raw, str) and str(message_raw).strip()
            else str(error_raw or "Tool error")
        )
        payload: dict[str, Any] = {
            "type": "error",
            "code": code,
            "message": message,
        }
        for key in (
            "agentName",
            "allowedAgents",
            "tenantId",
            "industryTemplateId",
            "tool",
            "required",
        ):
            if key in effective_result:
                payload[key] = effective_result[key]
        return payload

    if error_raw == "capability_not_enabled":
        payload = {
            "type": "error",
            "code": "CAPABILITY_NOT_ENABLED",
            "message": "This action is not enabled for the current session.",
        }
        for key in ("tool", "required"):
            if key in effective_result:
                payload[key] = effective_result[key]
        return payload

    return {
        "type": "error",
        "code": "TOOL_ERROR",
        "message": (
            str(error_raw).strip()
            if isinstance(error_raw, str) and str(error_raw).strip()
            else (
                str(message_raw).strip()
                if isinstance(message_raw, str) and str(message_raw).strip()
                else (
                    str(detail_raw).strip()
                    if isinstance(detail_raw, str) and str(detail_raw).strip()
                    else "Tool error"
                )
            )
        ),
    }


def _format_product_description(product: dict[str, Any]) -> str:
    features = product.get("features")
    if isinstance(features, list) and features:
        return ", ".join(str(item) for item in features[:3])
    description = product.get("description")
    if isinstance(description, str):
        return description
    return ""


async def after_tool_emit_messages(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    result: dict[str, Any] | None = None,
    *,
    tool_response: dict[str, Any] | None = None,
    **_: Any,
) -> None:
    """Emit structured server messages after successful tool calls."""
    effective_result = tool_response if isinstance(tool_response, dict) else result
    try:
        from app.api.v1.at import voice_analytics
    except Exception:  # pragma: no cover - analytics should not break tool flow
        voice_analytics = None
    logger.info(
        "tool_end agent=%s tool=%s success=%s",
        tool_context.agent_name,
        tool.name,
        isinstance(effective_result, dict)
        and not effective_result.get("error")
        and str(effective_result.get("status", "")).strip().lower() != "error",
    )

    if not isinstance(effective_result, dict):
        return None

    if effective_result.get("error") or str(effective_result.get("status", "")).strip().lower() == "error":
        if tool.name == "request_media_via_whatsapp":
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=getattr(tool_context, "session", None),
                key="temp:last_media_request_status",
                value="failure",
            )
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=getattr(tool_context, "session", None),
                key="temp:pending_media_request_voice_ack",
                value="",
            )
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=getattr(tool_context, "session", None),
                key="temp:last_outbound_delivery_status",
                value="failure",
            )
        if tool.name in {"send_whatsapp_message", "send_sms_message"}:
            tool_context.state["temp:last_outbound_delivery_status"] = "failure"
        queue_server_message(tool_context.state, _tool_error_server_message(effective_result))
        return None

    if tool.name in {"send_whatsapp_message", "send_sms_message"}:
        channel = "whatsapp" if tool.name == "send_whatsapp_message" else "sms"
        phone = ""
        if tool.name == "send_whatsapp_message":
            caller_phone = _state_get(tool_context.state, "user:caller_phone", "")
            phone = caller_phone.strip() if isinstance(caller_phone, str) else ""
        else:
            recipient = effective_result.get("recipient")
            phone = recipient.strip() if isinstance(recipient, str) else ""
        tool_context.state["temp:last_outbound_delivery_status"] = "success"
        tool_context.state["temp:last_outbound_delivery_channels"] = channel
        tool_context.state["temp:last_outbound_delivery_phone"] = phone
        return None

    if tool.name == "request_media_via_whatsapp":
        phone = str(effective_result.get("phone", "") or "").strip()
        session = getattr(tool_context, "session", None)
        _persist_runtime_hint_state(
            state=tool_context.state,
            session=session,
            key="temp:last_media_request_status",
            value="sent",
        )
        _persist_runtime_hint_state(
            state=tool_context.state,
            session=session,
            key="temp:last_outbound_delivery_status",
            value="success",
        )
        _persist_runtime_hint_state(
            state=tool_context.state,
            session=session,
            key="temp:last_outbound_delivery_channels",
            value="whatsapp",
        )
        _persist_runtime_hint_state(
            state=tool_context.state,
            session=session,
            key="temp:last_outbound_delivery_phone",
            value=phone,
        )
        channel = _state_get(tool_context.state, "app:channel", "")
        is_voice = isinstance(channel, str) and channel.strip().lower() == "voice"
        if is_voice and _is_voice_tradein_flow(tool_context.state, session=session):
            _persist_voice_string_state(
                state=tool_context.state,
                session=session,
                key="temp:vision_media_handoff_state",
                value="pending",
            )
            _persist_voice_string_state(
                state=tool_context.state,
                session=session,
                key="temp:background_vision_status",
                value="awaiting_media",
            )
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=session,
                key="temp:pending_media_request_voice_ack",
                value="ready",
            )
            try:
                tool_context.state["temp:last_analysis"] = {}
                tool_context.state["temp:last_offer_amount"] = 0
            except Exception:
                logger.debug("Failed to clear callback trade-in analysis state", exc_info=True)
            session_state = getattr(session, "state", None)
            if session_state is not None:
                try:
                    session_state["temp:last_analysis"] = {}
                    session_state["temp:last_offer_amount"] = 0
                except Exception:
                    logger.debug("Failed to clear session trade-in analysis state", exc_info=True)
        return None

    if tool.name == "end_call":
        status = str(effective_result.get("status", "")).strip().lower()
        channel = _state_get(tool_context.state, "app:channel", "")
        normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
        if status == "ok" and normalized_channel == "voice":
            reason = str(
                effective_result.get("reason")
                or args.get("reason")
                or "conversation_complete"
            ).strip() or "conversation_complete"
            _queue_end_after_speaking_control(
                tool_context.state,
                reason=reason,
            )
        return None

    if tool.name == "request_callback":
        status = str(effective_result.get("status", "")).strip().lower()
        if status in {"pending", "queued", "cooldown"}:
            tool_context.state["temp:callback_requested"] = True
            if voice_analytics is not None:
                try:
                    voice_analytics.mark_callback_requested(
                        session_id=str(tool_context.state.get("app:session_id", "") or ""),
                        phone=str(effective_result.get("phone", "") or ""),
                    )
                except Exception:
                    logger.debug("Voice analytics callback request skipped", exc_info=True)
            channel = _state_get(tool_context.state, "app:channel", "")
            normalized_channel = channel.strip().lower() if isinstance(channel, str) else ""
            _at_so = getattr(tool_context, "session", None)
            _at_sid = str(getattr(_at_so, "id", "") or "") if _at_so else ""
            if normalized_channel == "voice" and not _is_callback_leg(
                tool_context.state, session_id_override=_at_sid,
            ):
                _queue_end_after_speaking_control(
                    tool_context.state,
                    reason="callback_registered",
                )
        return None

    if tool.name == "create_virtual_account_payment":
        sms_sent = bool(effective_result.get("sms_sent"))
        whatsapp_sent = bool(effective_result.get("whatsapp_sent"))
        channels = [
            channel
            for channel, sent in (("sms", sms_sent), ("whatsapp", whatsapp_sent))
            if sent
        ]
        if channels:
            tool_context.state["temp:last_outbound_delivery_status"] = (
                "success" if len(channels) == 2 else "partial"
            )
            tool_context.state["temp:last_outbound_delivery_channels"] = " and ".join(channels)
            phone = effective_result.get("notification_phone", "")
            if isinstance(phone, str):
                tool_context.state["temp:last_outbound_delivery_phone"] = phone.strip()
        elif effective_result.get("notification_phone"):
            tool_context.state["temp:last_outbound_delivery_status"] = "failure"

    if tool.name == "analyze_device_image_tool":
        _persist_voice_string_state(
            state=tool_context.state,
            session=getattr(tool_context, "session", None),
            key="temp:vision_media_handoff_state",
            value="consumed",
        )
        _persist_voice_string_state(
            state=tool_context.state,
            session=getattr(tool_context, "session", None),
            key="temp:background_vision_status",
            value="ready",
        )
        analysis_state = {
            "device_name": effective_result.get("device_name", "Unknown"),
            "brand": effective_result.get("brand", "Unknown"),
            "device_color": effective_result.get("device_color", "unknown"),
            "color_confidence": effective_result.get("color_confidence", 0.0),
            "condition": effective_result.get("condition", "Unknown"),
            "power_state": effective_result.get("power_state", "unknown"),
            "details": effective_result.get("details", {}),
        }
        _persist_voice_json_state(
            state=tool_context.state,
            session=getattr(tool_context, "session", None),
            key="temp:last_analysis",
            value=analysis_state,
        )
        message: dict[str, Any] = {
            "type": "image_received",
            "status": "complete",
        }
        gcs_uri = effective_result.get("gcs_uri")
        if isinstance(gcs_uri, str) and gcs_uri:
            message["previewUrl"] = gcs_uri
        queue_server_message(tool_context.state, message)
        return None

    if tool.name == "get_device_questionnaire_tool":
        questions = effective_result.get("questions", [])
        queue_server_message(
            tool_context.state,
            {
                "type": "questionnaire_started",
                "questionCount": len(questions) if isinstance(questions, list) else 0,
            },
        )
        return None

    if tool.name == "grade_and_value_tool":
        offer_amount = int(effective_result.get("offer_amount") or 0)
        tool_context.state["temp:last_offer_amount"] = offer_amount

        message: dict[str, Any] = {
            "type": "valuation_result",
            "deviceName": effective_result.get("device_name", "Unknown"),
            "condition": effective_result.get("grade", "Fair"),
            "price": offer_amount,
            "currency": effective_result.get("currency", "NGN"),
            "details": effective_result.get("summary", ""),
            "negotiable": offer_amount > 0,
        }
        # Include adjustment info when questionnaire was used
        if "original_vision_grade" in effective_result:
            message["originalGrade"] = effective_result["original_vision_grade"]
        if "adjustments" in effective_result:
            message["adjustments"] = effective_result["adjustments"]

        queue_server_message(tool_context.state, message)
        return None

    if tool.name == "create_booking":
        queue_server_message(
            tool_context.state,
            {
                "type": "booking_confirmation",
                "confirmationId": effective_result.get("confirmation_id", ""),
                "date": effective_result.get("date", ""),
                "time": effective_result.get("time", ""),
                "location": effective_result.get("location", ""),
                "service": effective_result.get("service_type", ""),
            },
        )
        return None

    if tool.name == "search_catalog":
        products_raw = effective_result.get("products")
        if not isinstance(products_raw, list):
            return None
        products: list[dict[str, Any]] = []
        for item in products_raw[:3]:
            if not isinstance(item, dict):
                continue
            raw_price = item.get("price", 0)
            try:
                price_value = int(raw_price) if isinstance(raw_price, (int, float)) else 0
            except (TypeError, ValueError):
                price_value = 0
            products.append(
                {
                    "name": item.get("name", "Unknown"),
                    "price": str(raw_price) if isinstance(raw_price, str) else price_value,
                    "currency": item.get("currency", "NGN"),
                    "available": bool(item.get("in_stock", False)),
                    "description": _format_product_description(item),
                }
            )
        queue_server_message(
            tool_context.state,
            {
                "type": "product_recommendation",
                "products": products,
            },
        )
        return None

    return None


_KNOWN_AGENT_NAMES = frozenset(KNOWN_SUB_AGENT_NAMES)


async def on_tool_error_emit(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    exception: Exception | None = None,
    *,
    error: Exception | None = None,
    **_: Any,
) -> dict[str, Any] | None:
    """Recover from tool errors so the Live API flow stays alive.

    Returns a dict so ADK feeds the error back to the model instead of
    crashing the session.  For hallucinated sub-agent calls, the hint
    tells the model to use transfer_to_agent instead.
    """
    effective_exception = error or exception or Exception("Unknown tool error")
    logger.error(
        "tool_exception agent=%s tool=%s error=%s",
        tool_context.agent_name,
        tool.name,
        effective_exception,
    )
    queue_server_message(
        tool_context.state,
        {
            "type": "error",
            "code": "TOOL_EXCEPTION",
            "message": f"{tool.name} failed. Please try again.",
        },
    )

    if tool.name == "analyze_device_image_tool":
        _persist_voice_string_state(
            state=tool_context.state,
            session=getattr(tool_context, "session", None),
            key="temp:vision_media_handoff_state",
            value="failed",
        )
        _persist_voice_string_state(
            state=tool_context.state,
            session=getattr(tool_context, "session", None),
            key="temp:background_vision_status",
            value="failed",
        )

    # Hallucinated sub-agent name as direct function call
    if tool.name in _KNOWN_AGENT_NAMES:
        # Hydrate callback-visible state before evaluating opening guards.
        _hydrate_voice_opening_state_from_session(
            tool_context.state, session=getattr(tool_context, "session", None),
        )
        channel = _state_get(tool_context.state, "app:channel", "")
        is_voice = isinstance(channel, str) and channel.strip().lower() == "voice"
        opening_complete = _is_voice_opening_complete(
            tool_context.state,
            session=getattr(tool_context, "session", None),
        )
        latest_user_turn = _latest_user_turn_text(
            tool_context.state,
            session=getattr(tool_context, "session", None),
        )
        tradein_fast_path = (
            is_voice
            and tool.name == "valuation_agent"
            and _is_greeted_state(
                tool_context.state, session=getattr(tool_context, "session", None)
            )
            and _looks_like_tradein_or_upgrade_request(latest_user_turn)
        )
        signature = _hallucinated_transfer_signature(
            tool_context.state,
            tool.name,
            session=getattr(tool_context, "session", None),
        )
        previous_signature = str(
            _state_get(tool_context.state, "temp:last_hallucinated_handoff_signature", "") or ""
        )
        previous_attempts_raw = _state_get(
            tool_context.state, "temp:last_hallucinated_handoff_attempts", 0
        )
        try:
            previous_attempts = int(previous_attempts_raw or 0)
        except (TypeError, ValueError):
            previous_attempts = 0
        current_attempts = previous_attempts + 1 if previous_signature == signature else 1
        tool_context.state["temp:last_hallucinated_handoff_signature"] = signature
        tool_context.state["temp:last_hallucinated_handoff_attempts"] = current_attempts
        if is_voice and not opening_complete and not tradein_fast_path:
            logger.warning(
                "Suppressing hallucinated sub-agent recovery during protected opening agent=%s target=%s signature=%s attempt=%d",
                tool_context.agent_name,
                tool.name,
                signature,
                current_attempts,
            )
            tool_context.actions.transfer_to_agent = None
            if current_attempts > 1:
                return {
                    "error": "routing_retry_suppressed",
                    "detail": (
                        "Do not retry the same agent handoff again for this turn. "
                        "Finish the opening and respond directly to the caller's first real reply."
                    ),
                }
            return {
                "error": "opening_phase_in_progress",
                "detail": (
                    "Opening phase still in progress. Do not transfer or call another agent yet. "
                    "Finish the greeting and respond directly to the caller's first real reply."
                ),
            }
        if current_attempts > 1:
            logger.warning(
                "Suppressing repeated hallucinated sub-agent recovery agent=%s target=%s signature=%s attempt=%d",
                tool_context.agent_name,
                tool.name,
                signature,
                current_attempts,
            )
            tool_context.actions.transfer_to_agent = None
            return {
                "error": "routing_retry_suppressed",
                "detail": (
                    "Do not retry the same agent handoff again for this turn. "
                    "Respond directly to the caller or ask one short clarifying question."
                ),
            }
        enabled_agents = resolve_enabled_agents_from_state(tool_context.state)
        if enabled_agents is not None and tool.name not in enabled_agents:
            payload = _agent_not_enabled_payload(
                state=tool_context.state,
                agent_name=tool.name,
                allowed_agents=enabled_agents,
            )
            payload["error"] = "agent_not_enabled"
            payload["tool"] = tool.name
            return payload
        guard_result = _guard_transfer_before_greeting(
            state=tool_context.state,
            session=getattr(tool_context, "session", None),
            agent_name=tool_context.agent_name,
            target_agent=tool.name,
        )
        if guard_result is not None:
            return guard_result
        guard_result = _guard_transfer_without_explicit_request(
            state=tool_context.state,
            session=getattr(tool_context, "session", None),
            agent_name=tool_context.agent_name,
            target_agent=tool.name,
        )
        if guard_result is not None:
            return guard_result
        session = getattr(tool_context, "session", None)
        latest_user = _latest_user_turn_text(tool_context.state, session=session)
        latest_agent = _latest_agent_turn_text(tool_context.state, session=session)
        recent_customer = _recent_customer_context_text(tool_context.state, session=session)
        tool_context.state["temp:pending_handoff_target_agent"] = tool.name
        tool_context.state["temp:pending_handoff_latest_user"] = latest_user
        tool_context.state["temp:pending_handoff_latest_agent"] = latest_agent
        tool_context.state["temp:pending_handoff_recent_customer_context"] = recent_customer
        if tool.name == "vision_agent" and _vision_media_handoff_state(
            tool_context.state, session=session
        ) == "pending":
            _persist_voice_string_state(
                state=tool_context.state,
                session=session,
                key="temp:vision_media_handoff_state",
                value="transferring",
            )
        if is_voice:
            bootstrap_reason = (
                "voice_tradein_recovery"
                if tool.name == "valuation_agent"
                and _is_voice_tradein_flow(tool_context.state, session=session)
                else "voice_handoff_recovery"
            )
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=session,
                key="temp:pending_transfer_bootstrap_target_agent",
                value=tool.name,
            )
            _persist_runtime_hint_state(
                state=tool_context.state,
                session=session,
                key="temp:pending_transfer_bootstrap_reason",
                value=bootstrap_reason,
            )
        tool_context.actions.transfer_to_agent = tool.name
        logger.info(
            "Recovering hallucinated sub-agent call via transfer_to_agent agent=%s target=%s",
            tool_context.agent_name,
            tool.name,
        )
        return {
            "error": f"'{tool.name}' is not a callable function.",
            "hint": f"Use transfer_to_agent(agent_name='{tool.name}') instead.",
        }

    # Generic tool error — still return a dict to keep the session alive
    return {
        "error": str(effective_exception),
        "hint": "Please try a different approach.",
    }
