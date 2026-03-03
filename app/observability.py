"""Observability helpers for registry-aware logging and metrics labels."""

from __future__ import annotations

from typing import Any

from app.configs import sanitize_log


def registry_metric_labels(
    *,
    tenant_id: str | None = None,
    company_id: str | None = None,
    industry_template_id: str | None = None,
    registry_version: str | None = None,
    schema_version: int | str | None = None,
    registry_mode: str | bool = "enabled",
    source: str = "registry",
) -> dict[str, str]:
    """Return a stable metrics-label dict for registry-sensitive operations."""
    if isinstance(registry_mode, bool):
        registry_mode_value = "enabled" if registry_mode else "disabled"
    else:
        registry_mode_value = str(registry_mode or "disabled")

    schema_value = ""
    if isinstance(schema_version, (int, str)) and str(schema_version).strip():
        schema_value = str(schema_version).strip()

    return {
        "tenant_id": sanitize_log(tenant_id or ""),
        "company_id": sanitize_log(company_id or ""),
        "industry_template_id": sanitize_log(industry_template_id or ""),
        "registry_version": sanitize_log(registry_version or ""),
        "schema_version": sanitize_log(schema_value),
        "registry_mode": sanitize_log(registry_mode_value),
        "source": sanitize_log(source),
    }


def registry_log_context(**kwargs: Any) -> str:
    """Format registry observability fields as key=value pairs for log messages."""
    labels = registry_metric_labels(**kwargs)
    return " ".join(f"{key}={value}" for key, value in labels.items())

