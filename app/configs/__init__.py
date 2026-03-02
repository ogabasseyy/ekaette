"""Shared config utilities for the registry migration.

Centralises helpers that were previously duplicated across
industry_loader, company_loader, and registry_loader.
"""

import os
import re
from typing import Any

_LOG_UNSAFE_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")

REGISTRY_SCHEMA_VERSION = 1
MIN_SUPPORTED_SCHEMA_VERSION = 1
MAX_SUPPORTED_SCHEMA_VERSION = 1


def sanitize_log(value: str | None) -> str:
    """Strip newlines/control chars from user-supplied values before logging."""
    if value is None:
        return "<none>"
    return _LOG_UNSAFE_RE.sub("", value)[:200]


def env_flag(name: str, default: str = "false") -> bool:
    """Read a boolean environment variable (truthy: 1/true/yes/on)."""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def registry_enabled() -> bool:
    """Return True when registry is the authoritative config source."""
    return env_flag("REGISTRY_ENABLED", "true")


class RegistryDataMissingError(Exception):
    """Raised when REGISTRY_ENABLED=true but required registry data is absent."""


class RegistrySchemaVersionError(RegistryDataMissingError):
    """Raised when a registry document has an unsupported schema_version."""

    code = "REGISTRY_SCHEMA_VERSION_UNSUPPORTED"


def validate_registry_schema_version(
    data: Any,
    *,
    kind: str,
    identifier: str,
) -> int:
    """Return supported registry schema_version or raise a fail-closed error."""
    if not isinstance(data, dict):
        raise RegistrySchemaVersionError(
            f"{kind} '{sanitize_log(identifier)}' is not a dict"
        )

    version = data.get("schema_version")
    if not isinstance(version, int):
        raise RegistrySchemaVersionError(
            f"{kind} '{sanitize_log(identifier)}' missing integer schema_version"
        )
    if version < MIN_SUPPORTED_SCHEMA_VERSION or version > MAX_SUPPORTED_SCHEMA_VERSION:
        raise RegistrySchemaVersionError(
            f"{kind} '{sanitize_log(identifier)}' has unsupported schema_version={version} "
            f"(supported {MIN_SUPPORTED_SCHEMA_VERSION}-{MAX_SUPPORTED_SCHEMA_VERSION})"
        )
    return version
