"""Company knowledge grounding tools for support_agent (S12.5)."""

from __future__ import annotations

import json
import re
from typing import Any

from google.adk.tools.tool_context import ToolContext


def _profile_from_state(tool_context: ToolContext | None) -> dict[str, Any]:
    if tool_context is None:
        return {}
    profile = tool_context.state.get("app:company_profile")
    return profile if isinstance(profile, dict) else {}


def _knowledge_from_state(tool_context: ToolContext | None) -> list[dict[str, Any]]:
    if tool_context is None:
        return []
    entries = tool_context.state.get("app:company_knowledge")
    if not isinstance(entries, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in entries:
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


def _company_id_from_state(tool_context: ToolContext | None) -> str:
    if tool_context is None:
        return ""
    value = tool_context.state.get("app:company_id")
    return value if isinstance(value, str) else ""


def _connector_manifest_from_state(tool_context: ToolContext | None) -> tuple[dict[str, Any], bool]:
    """Return (manifest, present) from session state.

    ``present`` distinguishes:
    - False: legacy/compat mode (no manifest key in state) -> callers may fallback
    - True: registry/canonical mode (manifest key present, possibly empty) -> callers
      should treat the manifest as authoritative and fail closed when a connector is
      missing instead of falling back to profile connectors.
    """
    if tool_context is None:
        return {}, False
    state = getattr(tool_context, "state", None)
    if not isinstance(state, dict):
        return {}, False
    if "app:connector_manifest" not in state:
        return {}, False
    raw = state.get("app:connector_manifest")
    return (raw if isinstance(raw, dict) else {}), True


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _compile_word_patterns(tokens: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Compile unique word-boundary regex patterns once per search query."""
    patterns: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        patterns.append((token, re.compile(rf"\b{re.escape(token)}\b")))
    return patterns


def _nested_lookup(data: dict[str, Any], path: str) -> tuple[Any, bool]:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


async def get_company_profile_fact(
    fact_key: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Read one company fact from runtime profile context.

    Lookup order:
      1) profile.facts[fact_key]
      2) profile[fact_key]
      3) dotted path on profile (e.g. ``system_connectors.crm.provider``)
    """
    profile = _profile_from_state(tool_context)
    company_id = _company_id_from_state(tool_context)

    if not profile:
        return {
            "error": "Company profile is not loaded in session state.",
            "fact_key": fact_key,
            "company_id": company_id,
        }

    lookup_key = (fact_key or "").strip()
    if not lookup_key:
        return {
            "error": "fact_key is required.",
            "company_id": company_id,
        }

    facts = profile.get("facts", {})
    if isinstance(facts, dict) and lookup_key in facts:
        return {
            "company_id": company_id,
            "fact_key": lookup_key,
            "value": facts[lookup_key],
            "source": "profile.facts",
        }

    if lookup_key in profile:
        return {
            "company_id": company_id,
            "fact_key": lookup_key,
            "value": profile[lookup_key],
            "source": "profile",
        }

    nested_value, found = _nested_lookup(profile, lookup_key)
    if found:
        return {
            "company_id": company_id,
            "fact_key": lookup_key,
            "value": nested_value,
            "source": "profile.nested",
        }

    return {
        "error": f"Fact '{lookup_key}' not found in company profile.",
        "company_id": company_id,
        "fact_key": lookup_key,
    }


def _knowledge_score(
    entry: dict[str, Any],
    token_patterns: list[tuple[str, re.Pattern[str]]],
) -> int:
    if not token_patterns:
        return 0

    title = str(entry.get("title", "")).lower()
    text = str(entry.get("text", "")).lower()
    url = str(entry.get("url", "")).lower()
    tags = entry.get("tags", [])
    tags_blob = " ".join(str(tag).lower() for tag in tags) if isinstance(tags, list) else ""
    haystack = " ".join([title, text, url, tags_blob])

    score = 0
    for token, token_pattern in token_patterns:
        if token_pattern.search(title):
            score += 5
        if token_pattern.search(text):
            score += 3
        if token_pattern.search(tags_blob):
            score += 2
        if token_pattern.search(url):
            score += 1

    if all(token in haystack for token, _ in token_patterns):
        score += 5
    return score


async def search_company_knowledge(
    query: str,
    max_results: int = 3,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Keyword + lightweight semantic-ish scoring over loaded company KB."""
    company_id = _company_id_from_state(tool_context)
    entries = _knowledge_from_state(tool_context)

    if tool_context is None:
        return {
            "error": "tool_context is required for company knowledge search.",
            "query": query,
            "results": [],
            "company_id": company_id,
        }

    if not entries:
        return {
            "error": "No company knowledge entries loaded for this session.",
            "query": query,
            "results": [],
            "company_id": company_id,
        }

    safe_max = max(1, min(int(max_results or 3), 10))
    tokens = _tokenize(query)
    token_patterns = _compile_word_patterns(tokens)

    scored: list[tuple[int, dict[str, Any]]] = []
    for index, entry in enumerate(entries):
        score = _knowledge_score(entry, token_patterns)
        if tokens and score <= 0:
            continue
        normalized = {
            "id": str(entry.get("id", f"kb-{index + 1}")),
            "title": str(entry.get("title", "")).strip(),
            "text": str(entry.get("text", "")).strip(),
            "url": str(entry.get("url", "")).strip(),
            "tags": entry.get("tags", []) if isinstance(entry.get("tags"), list) else [],
            "source": str(entry.get("source", "unknown")),
            "score": score,
        }
        scored.append((score, normalized))

    scored.sort(key=lambda item: item[0], reverse=True)
    return {
        "query": query,
        "company_id": company_id,
        "results": [item for _, item in scored[:safe_max]],
    }


async def query_company_system(
    system: str,
    action: str,
    payload: str | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Invoke configured company connector action.

    Extension point:
      provider-dispatch is explicit so real connectors (CRM/PMS/MCP) can be
      added without changing agent code.

    Current supported provider:
      - mock: profile-defined static responses under mock_actions

    Note:
      `payload` is a JSON string for Live API tool-schema compatibility.
      For backward compatibility, direct Python callers may still pass a dict.
    """
    profile = _profile_from_state(tool_context)
    company_id = _company_id_from_state(tool_context)
    connector_manifest, manifest_present = _connector_manifest_from_state(tool_context)
    safe_payload: dict[str, Any] = {}
    if isinstance(payload, dict):  # Backward compatibility for direct callers/tests.
        safe_payload = payload
    elif isinstance(payload, str) and payload.strip():
        try:
            parsed_payload = json.loads(payload)
            if isinstance(parsed_payload, dict):
                safe_payload = parsed_payload
        except json.JSONDecodeError:
            # Keep payload empty and continue; connector errors remain deterministic.
            safe_payload = {}

    if not profile:
        return {
            "error": "Company profile is not loaded in session state.",
            "system": system,
            "action": action,
            "company_id": company_id,
        }

    if manifest_present:
        connectors = connector_manifest
    else:
        connectors = profile.get("system_connectors")

    if not isinstance(connectors, dict):
        connectors = {}

    if not connectors:
        return {
            "error": "No system connectors configured for this company.",
            "system": system,
            "action": action,
            "company_id": company_id,
        }

    connector = connectors.get(system)
    if not isinstance(connector, dict):
        return {
            "error": f"Connector '{system}' is not configured.",
            "system": system,
            "action": action,
            "company_id": company_id,
        }

    dispatch_result = _dispatch_connector(
        connector=connector,
        system=system,
        action=action,
        payload=safe_payload,
        company_id=company_id,
    )
    if dispatch_result is not None:
        return dispatch_result

    mock_actions = connector.get("mock_actions")
    configured_actions: list[str] = []
    if isinstance(mock_actions, dict):
        configured_actions = sorted(str(key) for key in mock_actions.keys())

    provider_raw = connector.get("provider", "mock")
    provider = str(provider_raw).strip().lower() or "mock"
    return {
        "error": (
            f"Provider '{provider}' is not implemented for connector '{system}'. "
            "Use provider='mock' for now."
        ),
        "company_id": company_id,
        "system": system,
        "action": action,
        "provider": provider,
        "configured_actions": configured_actions,
    }


def _dispatch_connector(
    connector: dict[str, Any],
    system: str,
    action: str,
    payload: dict[str, Any],
    company_id: str,
) -> dict[str, Any] | None:
    """Dispatch connector action by provider.

    Return dict when provider is handled, else None for unsupported provider.
    """
    provider_raw = connector.get("provider", "mock")
    provider = str(provider_raw).strip().lower() or "mock"

    if provider == "mock":
        mock_actions = connector.get("mock_actions")
        if isinstance(mock_actions, dict) and action in mock_actions:
            return {
                "company_id": company_id,
                "system": system,
                "action": action,
                "payload": payload,
                "result": mock_actions[action],
                "provider": provider,
                "source": "mock_connector",
            }

        configured_actions: list[str] = []
        if isinstance(mock_actions, dict):
            configured_actions = sorted(str(key) for key in mock_actions.keys())

        return {
            "error": f"Action '{action}' is not configured for connector '{system}'.",
            "company_id": company_id,
            "system": system,
            "action": action,
            "provider": provider,
            "configured_actions": configured_actions,
        }

    return None
