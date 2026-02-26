"""Shared schema validation for the multi-tenant registry.

Used by:
- scripts/registry.py (provisioning CLI)
- scripts/migrate_to_tenant_scoped.py (data migration)
- app/configs/registry_loader.py (runtime validation, optional)
"""

from __future__ import annotations

from typing import Any

# ═══ Template Validation ═══

_TEMPLATE_REQUIRED_STRINGS = ("id", "label", "category", "status")


def validate_template(data: Any) -> list[str]:
    """Validate an industry template document. Returns list of error strings (empty = valid)."""
    if not isinstance(data, dict):
        return ["template must be a dict"]

    errors: list[str] = []

    for field in _TEMPLATE_REQUIRED_STRINGS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty required string field: {field}")

    caps = data.get("capabilities")
    if not isinstance(caps, list):
        errors.append("missing or invalid required field: capabilities (must be a list)")
    elif not all(isinstance(c, str) for c in caps):
        errors.append("capabilities must contain only strings")

    theme = data.get("theme")
    if theme is None:
        errors.append("missing required field: theme")
    else:
        errors.extend(validate_theme(theme))

    status = data.get("status")
    if status is not None and not isinstance(status, str):
        errors.append("status must be a string if present")

    display_name = data.get("display_name")
    if display_name is not None and not isinstance(display_name, str):
        errors.append("display_name must be a string if present")

    return errors


# ═══ Company Validation ═══

_COMPANY_REQUIRED_STRINGS = ("company_id", "tenant_id", "industry_template_id")


def validate_company(data: Any) -> list[str]:
    """Validate a tenant-scoped company document. Returns list of error strings."""
    if not isinstance(data, dict):
        return ["company must be a dict"]

    errors: list[str] = []

    for field in _COMPANY_REQUIRED_STRINGS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty required string field: {field}")

    return errors


def validate_capability_overrides(
    overrides: Any,
    template_capabilities: list[str],
) -> list[str]:
    """Validate capability overrides against a template's capability list.

    - 'add' entries are always valid (extending the base set).
    - 'remove' entries must reference capabilities that exist in the template.
    """
    if not isinstance(overrides, dict):
        return [] if overrides is None else ["capability_overrides must be a dict"]

    errors: list[str] = []

    remove_list = overrides.get("remove", [])
    if isinstance(remove_list, list):
        for cap in remove_list:
            if isinstance(cap, str) and cap.strip() and cap.strip() not in template_capabilities:
                errors.append(
                    f"capability override removes '{cap.strip()}' "
                    f"which is not in template capabilities"
                )

    return errors


# ═══ Knowledge Entry Validation ═══

_KNOWLEDGE_REQUIRED = ("id", "title", "tags")


def validate_knowledge_entry(data: Any) -> list[str]:
    """Validate a knowledge entry. Returns list of error strings."""
    if not isinstance(data, dict):
        return ["knowledge entry must be a dict"]

    errors: list[str] = []

    for field in ("id", "title"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty required string field: {field}")

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        # text is strongly recommended but we allow empty for metadata entries
        pass

    tags = data.get("tags")
    if not isinstance(tags, list) or len(tags) == 0:
        errors.append("missing or empty required field: tags (must be a non-empty list)")

    return errors


# ═══ Theme Validation ═══


def validate_theme(data: Any) -> list[str]:
    """Validate a theme object. Returns list of error strings.

    Requires:
    - accent (string)
    - title (string)
    Optional string fields:
    - accentSoft
    - hint
    """
    if not isinstance(data, dict):
        return ["theme must be a dict"]

    errors: list[str] = []

    for field in ("accent", "title"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"theme.{field} is required and must be a non-empty string")

    for field in ("accentSoft", "hint"):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"theme.{field} must be a string if present")

    return errors
