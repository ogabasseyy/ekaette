"""Dependency providers for shared app singletons/state."""

from __future__ import annotations

from fastapi import Request


def get_registry_db(request: Request):
    return (
        getattr(request.app.state, "company_config_client", None)
        or getattr(request.app.state, "industry_config_client", None)
    )


def get_session_service(request: Request):
    return getattr(request.app.state, "session_service", None)


def get_runner(request: Request):
    return getattr(request.app.state, "runner", None)


def get_token_client(request: Request):
    return getattr(request.app.state, "token_client", None)
