"""Agent isolation policy helpers for industry/template-scoped sessions."""

from __future__ import annotations

from typing import Any

KNOWN_SUB_AGENT_NAMES = {
    "vision_agent",
    "valuation_agent",
    "booking_agent",
    "catalog_agent",
    "support_agent",
}

_DEFAULT_ENABLED_AGENTS_BY_INDUSTRY: dict[str, list[str]] = {
    "electronics": [
        "vision_agent",
        "valuation_agent",
        "booking_agent",
        "catalog_agent",
        "support_agent",
    ],
    "hotel": ["booking_agent", "support_agent"],
    "automotive": [
        "vision_agent",
        "valuation_agent",
        "booking_agent",
        "catalog_agent",
        "support_agent",
    ],
    "fashion": ["catalog_agent", "support_agent"],
    "telecom": ["catalog_agent", "support_agent"],
    "aviation-support": ["support_agent"],
    "aviation": ["support_agent"],
}


def normalize_enabled_agents(raw: Any) -> list[str] | None:
    """Normalize an enabled_agents list to known sub-agent names.

    Returns None when the input isn't a list (meaning "policy not present").
    Returns an empty list when a list is present but contains no valid names
    (explicit fail-closed policy).
    """
    if not isinstance(raw, list):
        return None
    normalized = [
        str(agent).strip()
        for agent in raw
        if isinstance(agent, str) and str(agent).strip() in KNOWN_SUB_AGENT_NAMES
    ]
    return normalized


def infer_enabled_agents_from_capabilities(capabilities: Any) -> list[str] | None:
    """Best-effort compat fallback when explicit enabled_agents is unavailable."""
    if not isinstance(capabilities, list):
        return None
    caps = {
        str(cap).strip()
        for cap in capabilities
        if isinstance(cap, str) and str(cap).strip()
    }
    if not caps:
        return None

    allowed = ["support_agent"]
    if "catalog_lookup" in caps:
        allowed.append("catalog_agent")
    if "booking_reservations" in caps:
        allowed.append("booking_agent")
    if "valuation_tradein" in caps:
        allowed.extend(["vision_agent", "valuation_agent"])
    return allowed


def resolve_enabled_agents_from_template(
    template: dict[str, Any],
    capabilities: list[str],
) -> list[str]:
    """Resolve enabled sub-agents from registry template, with safe fallback."""
    template_enabled = normalize_enabled_agents(template.get("enabled_agents"))
    if template_enabled is not None:
        return template_enabled
    inferred = infer_enabled_agents_from_capabilities(capabilities)
    return inferred if inferred is not None else ["support_agent"]


def _state_get(state: Any, key: str, default: Any = None) -> Any:
    getter = getattr(state, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            # Some mapping-like objects may not accept the default parameter.
            value = getter(key)
            return default if value is None else value
    return default


def resolve_enabled_agents_from_state(state: Any) -> list[str] | None:
    """Resolve enabled sub-agents for a live session from canonical/compat state."""
    if state is None or not hasattr(state, "get"):
        return None

    direct = normalize_enabled_agents(_state_get(state, "app:enabled_agents"))
    if direct is not None:
        return direct

    template_id = _state_get(state, "app:industry_template_id")
    if isinstance(template_id, str):
        key = template_id.strip().lower()
        if key in _DEFAULT_ENABLED_AGENTS_BY_INDUSTRY:
            return list(_DEFAULT_ENABLED_AGENTS_BY_INDUSTRY[key])

    industry = _state_get(state, "app:industry")
    if isinstance(industry, str):
        key = industry.strip().lower()
        if key in _DEFAULT_ENABLED_AGENTS_BY_INDUSTRY:
            return list(_DEFAULT_ENABLED_AGENTS_BY_INDUSTRY[key])

    return infer_enabled_agents_from_capabilities(_state_get(state, "app:capabilities"))
