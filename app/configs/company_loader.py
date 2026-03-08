"""Company context loader for S12.5 grounding.

Loads company profile + knowledge snippets and maps them into ``app:*`` state
keys so agents can ground responses in business-specific data.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from typing import Any

from app.configs import RegistryDataMissingError  # noqa: F401 — re-export
from app.configs import registry_enabled as _registry_enabled
from app.configs import sanitize_log as _sanitize_log

logger = logging.getLogger(__name__)


def _default_registry_tenant_id() -> str:
    raw = os.getenv("REGISTRY_DEFAULT_TENANT_ID", "public").strip().lower()
    return raw or "public"


DEFAULT_COMPANY_PROFILE: dict[str, Any] = {
    "company_id": "default",
    "name": "Demo Company",
    "overview": "General customer support profile.",
    "facts": {},
    "links": [],
    "system_connectors": {},
}

LOCAL_COMPANY_PROFILES: dict[str, dict[str, Any]] = {
    "ekaette-electronics": {
        "name": "Ogabassey Gadgets",
        "overview": "Trade-in focused electronics store serving Lagos and Abuja.",
        "facts": {
            "primary_location": "Lagos - Ikeja",
            "support_hours": "09:00-19:00",
            "pickup_window": "10:00-18:00",
        },
        "links": [
            "https://example.com/electronics",
            "https://example.com/electronics/policies",
        ],
        "system_connectors": {
            "crm": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_customer": {"loyalty_tier": "silver"},
                },
            }
        },
    },
    "ekaette-hotel": {
        "name": "Ekaette Grand Hotel",
        "overview": "Business and leisure hotel with concierge and airport pickup.",
        "facts": {
            "rooms": 120,
            "check_in_time": "14:00",
            "check_out_time": "12:00",
        },
        "links": [
            "https://example.com/hotel",
            "https://example.com/hotel/policies",
        ],
        "system_connectors": {
            "pms": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_booking": {"status": "confirmed", "room_type": "Deluxe"},
                },
            }
        },
    },
    "ekaette-automotive": {
        "name": "Ekaette Auto Exchange",
        "overview": "Vehicle trade, inspection, and maintenance booking center.",
        "facts": {
            "inspection_slots_per_day": 24,
            "service_hours": "08:00-18:00",
            "pickup_service": True,
        },
        "links": [
            "https://example.com/automotive",
            "https://example.com/automotive/inspection",
        ],
        "system_connectors": {
            "dms": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_vehicle": {"status": "available"},
                },
            }
        },
    },
    "ekaette-fashion": {
        "name": "Ekaette Style House",
        "overview": "Retail fashion outlet with in-store and virtual styling sessions.",
        "facts": {
            "branches": 3,
            "same_day_delivery_cutoff": "15:00",
            "return_window_days": 14,
        },
        "links": [
            "https://example.com/fashion",
            "https://example.com/fashion/returns",
        ],
        "system_connectors": {
            "erp": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_order": {"status": "processing"},
                },
            }
        },
    },
    "acme-hotel": {
        "name": "Acme Grand Hotel",
        "overview": "Luxury hospitality with smart concierge service.",
        "facts": {
            "rooms": 120,
            "check_in_time": "14:00",
            "check_out_time": "12:00",
        },
        "links": ["https://example.com/rooms", "https://example.com/policies"],
        "system_connectors": {
            "crm": {
                "provider": "mock",
                "mock_actions": {
                    "lookup_guest": {"vip": False, "loyalty_tier": "standard"},
                },
            }
        },
    }
}

DEFAULT_KNOWLEDGE_ENTRIES: list[dict[str, Any]] = [
    {
        "id": "kb-default-1",
        "title": "General service policy",
        "text": "Always verify customer details before confirming a booking or order.",
        "tags": ["policy"],
        "source": "local_fallback",
    }
]

LOCAL_COMPANY_KNOWLEDGE: dict[str, list[dict[str, Any]]] = {
    "ekaette-electronics": [
        {
            "id": "kb-elec-hours",
            "title": "Support hours",
            "text": "Customer support is available daily from 9 AM to 7 PM.",
            "tags": ["support", "hours"],
            "source": "local_fallback",
        },
        {
            "id": "kb-elec-pickup",
            "title": "Pickup policy",
            "text": "Same-day pickup is available for confirmed bookings made before 2 PM.",
            "tags": ["pickup", "policy"],
            "source": "local_fallback",
        },
    ],
    "ekaette-hotel": [
        {
            "id": "kb-hotel-checkout",
            "title": "Late checkout policy",
            "text": "Late checkout until 1 PM is available for premium guests.",
            "tags": ["checkout", "policy"],
            "source": "local_fallback",
        },
        {
            "id": "kb-hotel-breakfast",
            "title": "Breakfast schedule",
            "text": "Breakfast is served from 6:30 AM to 10:30 AM daily.",
            "tags": ["breakfast", "amenities"],
            "source": "local_fallback",
        },
    ],
    "ekaette-automotive": [
        {
            "id": "kb-auto-inspection",
            "title": "Inspection checklist",
            "text": "Vehicle inspections cover engine, brakes, tires, electronics, and body condition.",
            "tags": ["inspection", "service"],
            "source": "local_fallback",
        },
        {
            "id": "kb-auto-finance",
            "title": "Financing support",
            "text": "Financing options are available through partner banks for qualified buyers.",
            "tags": ["finance", "sales"],
            "source": "local_fallback",
        },
    ],
    "ekaette-fashion": [
        {
            "id": "kb-fashion-returns",
            "title": "Return policy",
            "text": "Returns are accepted within 14 days for unworn items with tags attached.",
            "tags": ["returns", "policy"],
            "source": "local_fallback",
        },
        {
            "id": "kb-fashion-sizing",
            "title": "Sizing assistance",
            "text": "Virtual stylists can help with sizing and fit recommendations over chat.",
            "tags": ["sizing", "styling"],
            "source": "local_fallback",
        },
    ],
    "acme-hotel": [
        {
            "id": "kb-acme-checkout",
            "title": "Late checkout policy",
            "text": "Late checkout is available until 1 PM for premium guests.",
            "tags": ["checkout", "policy"],
            "source": "local_fallback",
        },
    ],
}


def _fallback_profile_for(company_id: str) -> dict[str, Any]:
    """.. deprecated:: Phase 7 — Legacy path, only used when REGISTRY_ENABLED=false."""
    normalized_id = (company_id or "default").strip().lower() or "default"
    logger.debug("company_loader: using legacy profile fallback for '%s'", _sanitize_log(normalized_id))
    base = LOCAL_COMPANY_PROFILES.get(normalized_id, DEFAULT_COMPANY_PROFILE)
    profile = dict(base)
    profile["company_id"] = normalized_id
    profile.setdefault("facts", {})
    profile.setdefault("links", [])
    profile.setdefault("system_connectors", {})
    profile.setdefault("overview", "")
    return profile


def _normalize_profile(company_id: str, raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        profile = dict(raw)
    else:
        profile = {}
    profile["company_id"] = (company_id or "default").strip().lower() or "default"

    name = profile.get("name")
    if not isinstance(name, str) or not name.strip():
        profile["name"] = "Demo Company"

    overview = profile.get("overview")
    if not isinstance(overview, str):
        profile["overview"] = ""

    facts = profile.get("facts")
    profile["facts"] = facts if isinstance(facts, dict) else {}

    links = profile.get("links")
    if isinstance(links, list):
        profile["links"] = [str(item) for item in links if item]
    else:
        profile["links"] = []

    connectors = profile.get("system_connectors")
    profile["system_connectors"] = connectors if isinstance(connectors, dict) else {}
    return profile


def _normalize_registry_company_profile(
    company_id: str,
    raw_company: Any,
) -> dict[str, Any]:
    """Project a tenant-scoped registry company doc into legacy company profile shape."""
    if not isinstance(raw_company, dict):
        return _normalize_profile(company_id, {})

    projected = {
        "company_id": company_id,
        "name": raw_company.get("display_name") or raw_company.get("name"),
        "overview": raw_company.get("overview"),
        "facts": raw_company.get("facts"),
        "links": raw_company.get("links"),
        "system_connectors": raw_company.get("connectors"),
    }
    return _normalize_profile(company_id, projected)


def _normalize_knowledge_entry(
    company_id: str,
    raw: dict[str, Any],
    entry_id: str,
) -> dict[str, Any]:
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        fallback_text = str(raw.get("text", "")).strip()
        title = fallback_text[:80] if fallback_text else "Untitled"

    text = raw.get("text")
    if not isinstance(text, str):
        text = str(text or "")

    tags = raw.get("tags")
    if isinstance(tags, list):
        norm_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    else:
        norm_tags = []

    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        url = ""

    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        source = "firestore"

    return {
        "id": entry_id,
        "company_id": (company_id or "default").strip().lower() or "default",
        "title": title.strip(),
        "text": text.strip(),
        "url": url.strip(),
        "tags": norm_tags,
        "source": source.strip(),
    }


def _fallback_knowledge_for(company_id: str) -> list[dict[str, Any]]:
    """.. deprecated:: Phase 7 — Legacy path, only used when REGISTRY_ENABLED=false."""
    normalized_id = (company_id or "default").strip().lower() or "default"
    logger.debug("company_loader: using legacy knowledge fallback for '%s'", _sanitize_log(normalized_id))
    local_entries = LOCAL_COMPANY_KNOWLEDGE.get(normalized_id)
    if isinstance(local_entries, list) and local_entries:
        entries: list[dict[str, Any]] = []
        for idx, entry in enumerate(local_entries, start=1):
            normalized = _normalize_knowledge_entry(
                company_id=normalized_id,
                raw=entry,
                entry_id=str(entry.get("id") or f"kb-local-{idx}"),
            )
            normalized["source"] = "local_fallback"
            entries.append(normalized)
        return entries

    entries = []
    for idx, entry in enumerate(DEFAULT_KNOWLEDGE_ENTRIES, start=1):
        normalized = _normalize_knowledge_entry(
            company_id=normalized_id,
            raw=entry,
            entry_id=str(entry.get("id") or f"kb-fallback-{idx}"),
        )
        normalized["source"] = "local_fallback"
        entries.append(normalized)
    return entries


def create_company_config_client(project: str | None = None) -> Any | None:
    """Create Firestore async client for company profile/knowledge lookups."""
    project_id = (project or os.getenv("GOOGLE_CLOUD_PROJECT", "")).strip()
    if not project_id:
        logger.warning(
            "GOOGLE_CLOUD_PROJECT not set — using local fallback company context"
        )
        return None

    try:
        from google.cloud import firestore

        return firestore.AsyncClient(project=project_id)
    except Exception as exc:
        logger.warning("Failed to initialize company Firestore client: %s", exc)
        return None


async def _call_firestore_get(doc_ref: Any) -> Any:
    """Get Firestore doc from async or sync client path."""
    get_fn = getattr(doc_ref, "get", None)
    if get_fn is None:
        raise RuntimeError("Document reference has no get()")

    if asyncio.iscoroutinefunction(get_fn):
        return await get_fn()
    return await asyncio.to_thread(get_fn)


async def load_company_profile(
    db: Any,
    company_id: str,
    *,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Load company profile from Firestore.

    When REGISTRY_ENABLED=true (default after Phase 7 cutover):
      - Registry is the ONLY source — raises RegistryDataMissingError on miss.
    When REGISTRY_ENABLED=false (legacy mode):
      - Falls back to old Firestore collection, then LOCAL_COMPANY_PROFILES.
    """
    normalized_id = (company_id or "default").strip().lower() or "default"

    if _registry_enabled():
        if db is None:
            raise RegistryDataMissingError(
                f"Firestore client required when REGISTRY_ENABLED=true "
                f"(company='{_sanitize_log(normalized_id)}')"
            )
        missing_error = (
            f"Registry company not found for company='{_sanitize_log(normalized_id)}' "
            f"(REGISTRY_ENABLED=true)"
        )
        try:
            registry_loader = importlib.import_module("app.configs.registry_loader")
            load_tenant_company = getattr(registry_loader, "load_tenant_company")
        except (ImportError, AttributeError) as exc:
            logger.warning(
                "Registry loader wiring failed for company profile '%s': %s",
                _sanitize_log(normalized_id),
                exc,
            )
            raise

        resolved_tenant = (tenant_id or _default_registry_tenant_id()).strip().lower() or "public"
        try:
            company_doc = await load_tenant_company(db, resolved_tenant, normalized_id)
        except RegistryDataMissingError as exc:
            raise RegistryDataMissingError(missing_error) from exc

        if isinstance(company_doc, dict):
            return _normalize_registry_company_profile(normalized_id, company_doc)
        raise RegistryDataMissingError(missing_error)

    # Legacy mode: REGISTRY_ENABLED=false
    if db is None:
        return _fallback_profile_for(normalized_id)

    try:
        doc_ref = db.collection("company_profiles").document(normalized_id)
        doc = await _call_firestore_get(doc_ref)
        if getattr(doc, "exists", False):
            raw = doc.to_dict()
            if isinstance(raw, dict):
                return _normalize_profile(normalized_id, raw)
    except Exception as exc:
        logger.warning("Failed to load company profile '%s': %s", _sanitize_log(normalized_id), exc)

    return _fallback_profile_for(normalized_id)


async def load_company_knowledge(
    db: Any,
    company_id: str,
    limit: int = 12,
    *,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load company knowledge entries for grounding.

    Data model (collection: company_knowledge):
      - company_id: str
      - title: str
      - text: str
      - url: str (optional)
      - tags: list[str] (optional)
      - source: str (optional, e.g. text|url|crm|mcp)

    When REGISTRY_ENABLED=true (default after Phase 7 cutover):
      - Uses only tenant-scoped registry knowledge path.
      - Returns an empty list when no registry entries exist.
      - Raises RegistryDataMissingError when Firestore is unavailable.
    When REGISTRY_ENABLED=false (legacy mode):
      - Uses legacy company_knowledge collection and local fallback.
    """
    normalized_id = (company_id or "default").strip().lower() or "default"
    safe_limit = max(1, min(limit, 50))
    if _registry_enabled():
        if db is None:
            raise RegistryDataMissingError(
                f"Firestore client required when REGISTRY_ENABLED=true "
                f"(company='{_sanitize_log(normalized_id)}')"
            )
        try:
            resolved_tenant = (tenant_id or _default_registry_tenant_id()).strip().lower() or "public"
            query = (
                db.collection("tenants")
                .document(resolved_tenant)
                .collection("companies")
                .document(normalized_id)
                .collection("knowledge")
                .limit(safe_limit)
            )

            stream_result = query.stream()
            if inspect.isawaitable(stream_result):
                stream_result = await stream_result

            if hasattr(stream_result, "__aiter__"):
                docs = [doc async for doc in stream_result]
            else:
                docs = await asyncio.to_thread(lambda: list(stream_result))

            entries: list[dict[str, Any]] = []
            for idx, doc in enumerate(docs, start=1):
                raw = doc.to_dict() if hasattr(doc, "to_dict") else {}
                if not isinstance(raw, dict):
                    continue
                entry_id = str(getattr(doc, "id", "") or raw.get("id") or f"kb-{idx}")
                entries.append(_normalize_knowledge_entry(normalized_id, raw, entry_id))
            # In registry mode, no entries is valid (empty knowledge base) and should not
            # silently fall back to legacy/global sources.
            return entries
        except Exception as exc:
            logger.warning(
                "Registry company knowledge lookup failed '%s' (tenant=%s): %s",
                _sanitize_log(normalized_id),
                _sanitize_log((tenant_id or _default_registry_tenant_id())),
                exc,
            )
            raise RegistryDataMissingError(
                f"Registry company knowledge unavailable for company='{_sanitize_log(normalized_id)}' "
                f"(tenant='{_sanitize_log(resolved_tenant)}', REGISTRY_ENABLED=true)"
            ) from exc

    # Legacy mode: REGISTRY_ENABLED=false
    if db is None:
        return _fallback_knowledge_for(normalized_id)

    try:
        query = (
            db.collection("company_knowledge")
            .where("company_id", "==", normalized_id)
            .limit(safe_limit)
        )

        stream_result = query.stream()
        if inspect.isawaitable(stream_result):
            stream_result = await stream_result

        if hasattr(stream_result, "__aiter__"):
            docs = [doc async for doc in stream_result]
        else:
            docs = await asyncio.to_thread(lambda: list(stream_result))

        entries: list[dict[str, Any]] = []
        for idx, doc in enumerate(docs, start=1):
            raw = doc.to_dict() if hasattr(doc, "to_dict") else {}
            if not isinstance(raw, dict):
                continue
            entry_id = str(getattr(doc, "id", "") or raw.get("id") or f"kb-{idx}")
            entries.append(_normalize_knowledge_entry(normalized_id, raw, entry_id))

        if entries:
            return entries
    except Exception as exc:
        logger.warning("Failed to load company knowledge '%s': %s", _sanitize_log(normalized_id), exc)

    return _fallback_knowledge_for(normalized_id)


def build_company_session_state(
    company_id: str,
    profile: Any,
    knowledge: Any,
) -> dict[str, Any]:
    """Build state payload for company grounding under ``app:*`` keys."""
    normalized_id = (company_id or "default").strip().lower() or "default"
    normalized_profile = _normalize_profile(normalized_id, profile or {})
    knowledge_items = knowledge if isinstance(knowledge, list) else []
    normalized_knowledge = []
    for idx, entry in enumerate(knowledge_items, start=1):
        if isinstance(entry, dict):
            entry_id = str(entry.get("id") or f"kb-runtime-{idx}")
            raw_entry = entry
        else:
            entry_id = f"kb-runtime-{idx}"
            raw_entry = {"text": str(entry)}

        normalized_knowledge.append(
            _normalize_knowledge_entry(
                normalized_id,
                raw_entry,
                entry_id,
            )
        )

    normalized_knowledge = [
        item
        for item in normalized_knowledge
        if item.get("text") or item.get("title")
    ]

    return {
        "app:company_id": normalized_id,
        "app:company_profile": normalized_profile,
        "app:company_knowledge": normalized_knowledge,
    }
