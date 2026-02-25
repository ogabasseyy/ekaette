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
import logging
import re
from dataclasses import dataclass, field
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
    raw = f"{template.get('id', '')}:{company.get('company_id', '')}:{template.get('status', '')}"
    return "v1-" + hashlib.sha256(raw.encode()).hexdigest()[:8]


def _resolve_capabilities(
    template: dict[str, Any],
    company: dict[str, Any],
) -> list[str]:
    """Merge template capabilities with company overrides."""
    base = list(template.get("capabilities", []))
    overrides = company.get("capability_overrides", {})

    if isinstance(overrides, dict):
        for cap in overrides.get("add", []):
            if cap not in base:
                base.append(cap)
        for cap in overrides.get("remove", []):
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

    template_id = company.get("industry_template_id", "")
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
    ui_overrides = company.get("ui_overrides", {}) or {}
    voice = ui_overrides.get("voice") or template.get("default_voice", "Aoede")

    # Resolve theme: template default (company theme overrides could be added later)
    theme = dict(template.get("theme", {}))

    # Resolve greeting
    greeting = template.get("greeting_policy", "")

    # Resolve capabilities
    capabilities = _resolve_capabilities(template, company)

    # Resolve connectors
    connector_manifest = dict(company.get("connectors", {}))

    return ResolvedRegistryConfig(
        tenant_id=tenant_id,
        company_id=company_id,
        industry_template_id=template_id,
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
        "name": config.theme.get("title", config.industry_template_id.title()),
        "voice": config.voice,
        "greeting": config.greeting,
    }

    return {
        # Legacy keys
        "app:industry": config.industry_template_id,
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
