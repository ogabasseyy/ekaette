"""Observability helper contract tests for registry-aware logs and metrics labels."""

from __future__ import annotations


def test_registry_metric_labels_include_required_fields() -> None:
    from app.observability import registry_metric_labels

    labels = registry_metric_labels(
        tenant_id="public",
        company_id="ekaette-electronics",
        industry_template_id="electronics",
        registry_version="rv-123",
        schema_version=1,
        registry_mode=True,
        source="api_token",
    )

    assert labels["tenant_id"] == "public"
    assert labels["company_id"] == "ekaette-electronics"
    assert labels["industry_template_id"] == "electronics"
    assert labels["registry_version"] == "rv-123"
    assert labels["schema_version"] == "1"
    assert labels["registry_mode"] == "enabled"
    assert labels["source"] == "api_token"


def test_registry_log_context_formats_key_value_pairs() -> None:
    from app.observability import registry_log_context

    text = registry_log_context(
        tenant_id="public",
        company_id="ekaette-hotel",
        industry_template_id="hotel",
        registry_version="rv-abc",
        schema_version=1,
        registry_mode="enabled",
        source="ws_startup",
    )

    assert "tenant_id=public" in text
    assert "company_id=ekaette-hotel" in text
    assert "industry_template_id=hotel" in text
    assert "registry_version=rv-abc" in text
    assert "schema_version=1" in text
    assert "registry_mode=enabled" in text
    assert "source=ws_startup" in text

