"""Registry loader — resolves industry templates + tenant companies from Firestore.

Phase 1 of the multi-tenant registry migration. Provides:
- ResolvedRegistryConfig: canonical config snapshot for a session
- load_industry_template: loads platform template from industry_templates/{id}
- load_tenant_company: loads company from tenants/{tenant}/companies/{company}
- resolve_registry_config: merges template + company overrides
- build_session_state_from_registry: produces both legacy + canonical session keys
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_LOG_UNSAFE_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def _sanitize_log(value: str | None) -> str:
    if value is None:
        return "<none>"
    return _LOG_UNSAFE_RE.sub("", value)[:200]


class RegistryMismatchError(Exception):
    """Raised when a company's industry_template_id doesn't match the resolved template."""


@dataclass(frozen=True)
class ResolvedRegistryConfig:
    """Canonical runtime config resolved from template + company."""

    tenant_id: str
    company_id: str
    industry_template_id: str
    template_category: str
    template_label: str
    capabilities: list[str]
    voice: str
    theme: dict[str, Any]
    greeting: str
    connector_manifest: dict[str, Any]
    registry_version: str


async def load_industry_template(
    db: Any,
    template_id: str,
) -> dict[str, Any] | None:
    """Load a platform industry template from Firestore.

    Returns None when the template doesn't exist or Firestore is unavailable.
    """
    if db is None:
        return None

    try:
        doc_ref = db.collection("industry_templates").document(template_id)
        if asyncio.iscoroutinefunction(doc_ref.get):
            doc = await doc_ref.get()
        else:
            doc = await asyncio.to_thread(doc_ref.get)

        if doc.exists:
            return doc.to_dict()
    except Exception as exc:
        logger.warning(
            "registry_loader: failed to load template %s: %s",
            _sanitize_log(template_id),
            exc,
        )

    return None


async def load_tenant_company(
    db: Any,
    tenant_id: str,
    company_id: str,
) -> dict[str, Any] | None:
    """Load a tenant-scoped company profile from Firestore.

    Path: tenants/{tenant_id}/companies/{company_id}
    Returns None when missing or unavailable.
    """
    if db is None:
        return None

    try:
        doc_ref = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
        )
        if asyncio.iscoroutinefunction(doc_ref.get):
            doc = await doc_ref.get()
        else:
            doc = await asyncio.to_thread(doc_ref.get)

        if doc.exists:
            return doc.to_dict()
    except Exception as exc:
        logger.warning(
            "registry_loader: failed to load company %s/%s: %s",
            _sanitize_log(tenant_id),
            _sanitize_log(company_id),
            exc,
        )

    return None


def _compute_registry_version(
    template: dict[str, Any],
    company: dict[str, Any],
) -> str:
    """Compute a short hash for observability."""
    def _stable_payload(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): _stable_payload(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, list):
            return [_stable_payload(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    payload = {
        "template": _stable_payload(template),
        "company": _stable_payload(company),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "v1-" + hashlib.sha256(raw.encode()).hexdigest()[:8]


def _string_or_default(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            items.append(normalized)
    return items


def _resolve_capabilities(
    template: dict[str, Any],
    company: dict[str, Any],
) -> list[str]:
    """Merge template capabilities with company overrides."""
    base = _list_of_strings(template.get("capabilities", []))
    overrides = company.get("capability_overrides", {})

    if isinstance(overrides, dict):
        for cap in _list_of_strings(overrides.get("add", [])):
            if cap not in base:
                base.append(cap)
        for cap in _list_of_strings(overrides.get("remove", [])):
            if cap in base:
                base.remove(cap)

    return base


async def resolve_registry_config(
    db: Any,
    tenant_id: str,
    company_id: str,
) -> ResolvedRegistryConfig | None:
    """Resolve a full runtime config by merging template + company.

    Returns None if template or company cannot be loaded.
    Raises RegistryMismatchError if company's template doesn't match.
    """
    if db is None:
        return None

    company = await load_tenant_company(db, tenant_id, company_id)
    if company is None:
        return None

    template_id = _string_or_default(company.get("industry_template_id"), "")
    if not template_id:
        logger.warning(
            "registry_loader: company %s/%s missing industry_template_id",
            _sanitize_log(tenant_id),
            _sanitize_log(company_id),
        )
        return None

    template = await load_industry_template(db, template_id)
    if template is None:
        return None

    # Validate template match
    if template.get("id", template_id) != template_id:
        raise RegistryMismatchError(
            f"Company {company_id} references template '{template_id}' "
            f"but loaded template has id '{template.get('id')}'"
        )

    # Resolve voice: company override > template default
    ui_overrides = _dict_or_empty(company.get("ui_overrides"))
    voice = _string_or_default(
        ui_overrides.get("voice"),
        _string_or_default(template.get("default_voice"), "Aoede"),
    )

    # Resolve theme: template default (company theme overrides could be added later)
    theme = _dict_or_empty(template.get("theme"))

    # Resolve greeting
    greeting = _string_or_default(template.get("greeting_policy"), "")

    # Resolve capabilities
    capabilities = _resolve_capabilities(template, company)

    # Resolve connectors
    connector_manifest = _dict_or_empty(company.get("connectors"))

    template_category = _string_or_default(template.get("category"), template_id)
    template_label = _string_or_default(
        template.get("label"),
        _string_or_default(template.get("name"), template_id.title()),
    )

    return ResolvedRegistryConfig(
        tenant_id=tenant_id,
        company_id=company_id,
        industry_template_id=template_id,
        template_category=template_category,
        template_label=template_label,
        capabilities=capabilities,
        voice=voice,
        theme=theme,
        greeting=greeting,
        connector_manifest=connector_manifest,
        registry_version=_compute_registry_version(template, company),
    )


def build_session_state_from_registry(
    config: ResolvedRegistryConfig,
) -> dict[str, Any]:
    """Build ADK session state with both legacy and canonical keys.

    Legacy keys (backward compat with Phase 0 characterization tests):
      - app:industry, app:industry_config, app:company_id, app:voice, app:greeting

    Canonical keys (new):
      - app:tenant_id, app:industry_template_id, app:capabilities,
        app:ui_theme, app:connector_manifest, app:registry_version
    """
    # Build legacy industry_config shape matching LOCAL_INDUSTRY_CONFIGS
    legacy_industry_config: dict[str, Any] = {
        # Compatibility alias should preserve legacy "industry config" semantics,
        # not UI title text ("Electronics Trade Desk", etc.).
        "name": config.template_label,
        "voice": config.voice,
        "greeting": config.greeting,
    }

    return {
        # Legacy keys
        # Legacy alias maps to the broader category (for example "aviation")
        # while canonical key retains the full template id (for example "aviation-support").
        "app:industry": config.template_category,
        "app:industry_config": legacy_industry_config,
        "app:company_id": config.company_id,
        "app:voice": config.voice,
        "app:greeting": config.greeting,
        # Canonical keys
        "app:tenant_id": config.tenant_id,
        "app:industry_template_id": config.industry_template_id,
        "app:capabilities": list(config.capabilities),
        "app:ui_theme": dict(config.theme),
        "app:connector_manifest": dict(config.connector_manifest),
        "app:registry_version": config.registry_version,
    }


# ═══ Onboarding Config Builder ═══

# Theme + capability defaults for local compat mode
_LOCAL_INDUSTRY_THEMES: dict[str, dict[str, str]] = {
    "electronics": {
        "accent": "oklch(74% 0.21 158)",
        "accentSoft": "oklch(74% 0.21 158 / 0.15)",
        "title": "Electronics Trade Desk",
        "hint": "Inspect. Value. Negotiate. Book pickup.",
    },
    "hotel": {
        "accent": "oklch(78% 0.15 55)",
        "accentSoft": "oklch(78% 0.15 55 / 0.15)",
        "title": "Hospitality Concierge",
        "hint": "Real-time booking and guest support voice assistant.",
    },
    "automotive": {
        "accent": "oklch(71% 0.18 240)",
        "accentSoft": "oklch(71% 0.18 240 / 0.15)",
        "title": "Automotive Service Lane",
        "hint": "Trade-ins, inspections, parts and service scheduling.",
    },
    "fashion": {
        "accent": "oklch(74% 0.2 20)",
        "accentSoft": "oklch(74% 0.2 20 / 0.15)",
        "title": "Fashion Client Studio",
        "hint": "Catalog recommendations and consultation workflows.",
    },
}

_LOCAL_INDUSTRY_CAPABILITIES: dict[str, list[str]] = {
    "electronics": ["catalog_lookup", "valuation_tradein", "booking_reservations"],
    "hotel": ["booking_reservations", "policy_qa"],
    "automotive": ["booking_reservations", "valuation_tradein", "catalog_lookup"],
    "fashion": ["catalog_lookup", "policy_qa"],
}

# Industry → default company mapping for compat mode
_LOCAL_INDUSTRY_COMPANY_MAP: dict[str, str] = {
    "electronics": "ekaette-electronics",
    "hotel": "ekaette-hotel",
    "automotive": "ekaette-automotive",
    "fashion": "ekaette-fashion",
}


async def build_onboarding_config(
    db: Any,
    tenant_id: str,
) -> dict[str, Any]:
    """Build the onboarding config response for the frontend.

    When db is None or registry is unavailable, builds from local configs.
    When registry is available, queries Firestore for templates + companies.
    """
    # TODO: When REGISTRY_ENABLED, query Firestore industry_templates + companies.
    # For now, always use compat mode (local configs).
    return _build_onboarding_config_compat(tenant_id)


def _build_onboarding_config_compat(tenant_id: str) -> dict[str, Any]:
    """Build onboarding config from LOCAL_INDUSTRY_CONFIGS (compat mode)."""
    from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS
    from app.configs.company_loader import LOCAL_COMPANY_PROFILES

    templates: list[dict[str, Any]] = []
    for industry_id, config in LOCAL_INDUSTRY_CONFIGS.items():
        theme = _LOCAL_INDUSTRY_THEMES.get(industry_id, {
            "accent": "oklch(70% 0.15 200)",
            "accentSoft": "oklch(70% 0.15 200 / 0.15)",
            "title": config.get("name", industry_id.title()),
            "hint": "",
        })
        templates.append({
            "id": industry_id,
            "label": config.get("name", industry_id.title()),
            "category": industry_id,
            "description": theme.get("hint", ""),
            "defaultVoice": config.get("voice", "Aoede"),
            "theme": dict(theme),
            "capabilities": list(_LOCAL_INDUSTRY_CAPABILITIES.get(industry_id, [])),
            "status": "active",
        })

    companies: list[dict[str, Any]] = []
    for company_id, profile in LOCAL_COMPANY_PROFILES.items():
        # Determine which industry this company belongs to
        template_id = ""
        for ind_id, mapped_co in _LOCAL_INDUSTRY_COMPANY_MAP.items():
            if mapped_co == company_id:
                template_id = ind_id
                break
        if not template_id:
            # Try to infer from company_id prefix
            for ind_id in LOCAL_INDUSTRY_CONFIGS:
                if ind_id in company_id:
                    template_id = ind_id
                    break

        companies.append({
            "id": company_id,
            "templateId": template_id,
            "displayName": profile.get("name", company_id),
        })

    return {
        "tenantId": tenant_id,
        "templates": templates,
        "companies": companies,
        "defaults": {
            "templateId": "electronics",
            "companyId": "ekaette-electronics",
        },
    }
