"""Observability contract tests for admin API logging labels."""

from __future__ import annotations

import logging

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _admin_headers() -> dict[str, str]:
    return {
        "x-user-id": "obs-admin",
        "x-tenant-id": "public",
        "x-roles": "tenant_admin",
    }


@pytest.mark.asyncio
async def test_admin_success_logs_required_observability_fields(client, monkeypatch, caplog):
    from app.configs import registry_loader as registry_loader_module

    async def _fake_build_onboarding_config(_db, tenant_id):
        return {
            "tenantId": tenant_id,
            "templates": [{"id": "telecom", "label": "Telecom"}],
            "companies": [{"id": "ekaette-telecom", "templateId": "telecom"}],
            "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
        }

    monkeypatch.setattr(registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config)

    caplog.set_level(logging.INFO, logger="main")
    response = await client.get("/api/v1/admin/companies?tenantId=public", headers=_admin_headers())
    assert response.status_code == 200

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "admin_request" in logs
    for required_key in (
        "tenant_id=",
        "company_id=",
        "route=",
        "method=",
        "auth_mode=",
        "idempotency_scope=",
        "idempotency_state=",
        "result_code=",
        "status_code=",
    ):
        assert required_key in logs


@pytest.mark.asyncio
async def test_admin_auth_rejection_logs_required_observability_fields(client, caplog):
    caplog.set_level(logging.INFO, logger="main")
    response = await client.get("/api/v1/admin/companies?tenantId=public")
    assert response.status_code == 401

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "admin_request" in logs
    assert "result_code=UNAUTHORIZED" in logs
    assert "status_code=401" in logs
