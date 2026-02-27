"""Unit tests for admin runtime proxy resolution behavior."""

from __future__ import annotations

from app.api.v1.admin import settings
from app.api.v1.admin.runtime import runtime


def test_runtime_proxy_caches_symbol_module_resolution():
    settings.reset_runtime_state()
    # Resolve once to populate cache.
    _ = runtime.ADMIN_RATE_LIMIT
    assert runtime._resolved_module_by_symbol.get("ADMIN_RATE_LIMIT") == "app.api.v1.admin.settings"


def test_runtime_proxy_reads_latest_module_value_after_monkeypatch(monkeypatch):
    settings.reset_runtime_state()
    original = runtime.ADMIN_RATE_LIMIT
    monkeypatch.setattr(settings, "ADMIN_RATE_LIMIT", original + 7)
    assert runtime.ADMIN_RATE_LIMIT == original + 7


def test_reset_runtime_state_clears_runtime_resolution_cache():
    settings.reset_runtime_state()
    _ = runtime.MCP_PROVIDER_ALLOWLIST
    assert "MCP_PROVIDER_ALLOWLIST" in runtime._resolved_module_by_symbol
    settings.reset_runtime_state()
    assert runtime._resolved_module_by_symbol == {}
