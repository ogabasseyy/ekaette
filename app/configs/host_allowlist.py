"""Shared host extraction and allowlist matching helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse


def _normalize_hostname(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    return normalized


def extract_connector_endpoint_host(connector: Mapping[str, Any]) -> str | None:
    """Extract normalized hostname from connector config endpoint fields.

    Supports standard URL forms with optional credentials, ports, and IPv6
    bracket notation. Returns `None` when hostname cannot be resolved.
    """
    config = connector.get("config")
    if not isinstance(config, Mapping):
        return None

    for key in ("endpoint", "base_url", "url"):
        raw_value = config.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        parsed = urlparse(raw_value.strip())
        if parsed.hostname:
            host = _normalize_hostname(parsed.hostname)
            if host:
                return host
    return None


def host_matches_allowlist(hostname: str, allowed_hosts: Sequence[str]) -> bool:
    """Return True when hostname is explicitly allowed.

    Wildcard entries must be of form `*.example.com` and only match subdomains
    (`api.example.com`), not the parent apex (`example.com`).
    """
    normalized_hostname = _normalize_hostname(hostname)
    if not normalized_hostname:
        return False

    for candidate in allowed_hosts:
        normalized = _normalize_hostname(str(candidate))
        if not normalized:
            continue
        if normalized.startswith("*."):
            base = normalized[2:]
            if not base:
                continue
            if normalized_hostname == base:
                continue
            if normalized_hostname.endswith(f".{base}"):
                return True
            continue
        if normalized_hostname == normalized:
            return True
    return False

