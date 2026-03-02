"""Dependency providers for admin routes/services."""

from __future__ import annotations

from fastapi import Request

from . import settings, shared


def get_admin_settings():
    return settings


def get_admin_registry_db(_request: Request):
    return shared._registry_db_client()


def get_admin_runtime_clients(_request: Request) -> tuple[object | None, object | None]:
    return shared.industry_config_client, shared.company_config_client
