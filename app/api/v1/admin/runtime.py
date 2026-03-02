"""Runtime proxy exposing legacy `_m.<symbol>` lookups without importing main.

This preserves behavior in extracted modules while eliminating direct coupling to
`main.py` from within `app/api/v1/admin/**`.
"""

from __future__ import annotations

from importlib import import_module
import threading


_MODULE_SEARCH_ORDER = [
    "app.api.v1.admin.shared",
    "app.api.v1.admin.settings",
    "app.api.v1.admin.auth",
    "app.api.v1.admin.idempotency",
    "app.api.v1.admin.policy",
    "app.api.v1.admin.firestore_helpers",
    "app.api.v1.admin.service_companies",
    "app.api.v1.admin.service_knowledge",
    "app.api.v1.admin.service_connectors",
    "app.api.v1.admin.service_data",
    "app.configs.company_loader",
    "app.configs.host_allowlist",
    "app.configs",
    "app.observability",
]


class RuntimeProxy:
    """Resolve attributes lazily from canonical admin modules.

    Tests can monkeypatch attributes directly on this instance, and those
    overrides take precedence over module-based resolution.
    """

    def __init__(self) -> None:
        # Cache module resolution only (not resolved values), so module-level
        # monkeypatches still take effect on subsequent lookups.
        self._resolved_module_by_symbol: dict[str, str] = {}
        self._cache_lock = threading.RLock()

    def clear_resolution_cache(self) -> None:
        """Clear cached symbol->module resolution metadata."""
        with self._cache_lock:
            self._resolved_module_by_symbol.clear()

    def __getattr__(self, name: str):
        with self._cache_lock:
            cached_module_name = self._resolved_module_by_symbol.get(name)
        if cached_module_name:
            cached_module = import_module(cached_module_name)
            if hasattr(cached_module, name):
                return getattr(cached_module, name)
            # Cache entry became stale (symbol moved/removed); re-resolve.
            with self._cache_lock:
                self._resolved_module_by_symbol.pop(name, None)

        for module_name in _MODULE_SEARCH_ORDER:
            module = import_module(module_name)
            if hasattr(module, name):
                with self._cache_lock:
                    self._resolved_module_by_symbol[name] = module_name
                return getattr(module, name)
        raise AttributeError(name)


runtime = RuntimeProxy()
