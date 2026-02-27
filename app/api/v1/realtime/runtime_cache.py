"""Runtime dependency cache for realtime websocket modules.

This keeps runtime injection compatibility while avoiding repeated dynamic
resolution in hot streaming paths.
"""

from __future__ import annotations

from typing import Any

_RUNTIME_VALUES: dict[str, Any] = {}
_RUNTIME_CACHE: dict[str, Any] = {}
_MISSING = object()


def configure_runtime(**kwargs: Any) -> None:
    """Update runtime symbols and invalidate memoized lookups."""
    if kwargs:
        _RUNTIME_VALUES.update(kwargs)
        _RUNTIME_CACHE.clear()


def get_runtime_value(name: str) -> Any:
    """Resolve and memoize a runtime symbol by name."""
    cached = _RUNTIME_CACHE.get(name, _MISSING)
    if cached is not _MISSING:
        return cached
    if name not in _RUNTIME_VALUES:
        raise KeyError(f"Realtime runtime dependency not configured: {name}")
    value = _RUNTIME_VALUES[name]
    _RUNTIME_CACHE[name] = value
    return value


def bind_runtime_values(*names: str) -> tuple[Any, ...]:
    """Return a tuple of resolved runtime symbols in the same order as names."""
    return tuple(get_runtime_value(name) for name in names)
