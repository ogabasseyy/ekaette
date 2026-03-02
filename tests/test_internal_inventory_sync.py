"""Contract tests for internal inventory sync scheduler route."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def admin_runtime():
    from app.api.v1.admin.runtime import runtime

    return runtime


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _reset_in_memory_state(admin_runtime):
    from app.api.v1.admin import settings as admin_settings

    admin_settings.reset_runtime_state()
    yield
    admin_settings.reset_runtime_state()


class TestInternalInventorySyncRoute:
    @pytest.mark.asyncio
    async def test_internal_route_disabled_by_default(self, client, admin_runtime, monkeypatch):
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_ENABLED", False)

        response = await client.post(
            "/api/v1/internal/inventory/sync/run?tenantId=public",
            json={},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "INTERNAL_ROUTE_DISABLED"

    @pytest.mark.asyncio
    async def test_internal_route_shared_secret_success(self, client, admin_runtime, monkeypatch):
        captured: dict[str, object] = {}

        async def _fake_run_jobs(**kwargs):
            captured.update(kwargs)
            return {
                "tenantId": kwargs["tenant_id"],
                "companyId": kwargs.get("company_id"),
                "force": kwargs.get("force", False),
                "dryRunOverride": kwargs.get("dry_run_override"),
                "processed": 1,
                "triggered": 1,
                "skipped": 0,
                "results": [{"companyId": "ekaette-electronics", "status": "success", "written": 3}],
                "runAt": "2026-02-28T11:30:00+00:00",
            }

        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_ENABLED", True)
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_AUTH_MODE", "shared_secret")
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_SHARED_SECRET", "sync-secret")
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_MAX_COMPANIES", 25)
        monkeypatch.setattr(admin_runtime, "_run_inventory_sync_jobs", _fake_run_jobs)

        response = await client.post(
            "/api/v1/internal/inventory/sync/run?tenantId=public",
            headers={"x-inventory-sync-key": "sync-secret"},
            json={"maxCompanies": 200, "force": True, "dryRunOverride": False},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["authMode"] == "shared_secret"
        assert payload["authSubject"] == "shared-secret"
        assert payload["triggered"] == 1
        assert captured["tenant_id"] == "public"
        assert captured["max_companies"] == 25
        assert captured["force"] is True
        assert captured["dry_run_override"] is False

    @pytest.mark.asyncio
    async def test_internal_route_shared_secret_rejects_missing_key(self, client, admin_runtime, monkeypatch):
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_ENABLED", True)
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_AUTH_MODE", "shared_secret")
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_SHARED_SECRET", "sync-secret")

        response = await client.post(
            "/api/v1/internal/inventory/sync/run?tenantId=public",
            json={},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "INVENTORY_SYNC_INTERNAL_AUTH_REQUIRED"

    @pytest.mark.asyncio
    async def test_internal_route_oidc_enforces_allowlist(self, client, admin_runtime, monkeypatch):
        fake_google_id_token = SimpleNamespace(
            verify_oauth2_token=lambda token, request, audience: {
                "email": "other-sa@example.iam.gserviceaccount.com"
            }
        )

        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_ENABLED", True)
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_AUTH_MODE", "oidc")
        monkeypatch.setattr(
            admin_runtime,
            "INVENTORY_SYNC_INTERNAL_ALLOWED_SERVICE_ACCOUNTS",
            {"sync-sa@example.iam.gserviceaccount.com"},
        )
        monkeypatch.setattr(admin_runtime, "google_id_token", fake_google_id_token)

        response = await client.post(
            "/api/v1/internal/inventory/sync/run?tenantId=public",
            headers={"Authorization": "Bearer token-123"},
            json={},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "INVENTORY_SYNC_INTERNAL_AUTH_FORBIDDEN"

    @pytest.mark.asyncio
    async def test_internal_route_oidc_success(self, client, admin_runtime, monkeypatch):
        async def _fake_run_jobs(**kwargs):
            return {
                "tenantId": kwargs["tenant_id"],
                "companyId": kwargs.get("company_id"),
                "force": kwargs.get("force", False),
                "dryRunOverride": kwargs.get("dry_run_override"),
                "processed": 1,
                "triggered": 1,
                "skipped": 0,
                "results": [{"companyId": "ekaette-electronics", "status": "success", "written": 1}],
                "runAt": "2026-02-28T11:30:00+00:00",
            }

        fake_google_id_token = SimpleNamespace(
            verify_oauth2_token=lambda token, request, audience: {
                "email": "sync-sa@example.iam.gserviceaccount.com"
            }
        )

        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_ENABLED", True)
        monkeypatch.setattr(admin_runtime, "INVENTORY_SYNC_INTERNAL_AUTH_MODE", "oidc")
        monkeypatch.setattr(
            admin_runtime,
            "INVENTORY_SYNC_INTERNAL_ALLOWED_SERVICE_ACCOUNTS",
            {"sync-sa@example.iam.gserviceaccount.com"},
        )
        monkeypatch.setattr(admin_runtime, "google_id_token", fake_google_id_token)
        monkeypatch.setattr(admin_runtime, "_run_inventory_sync_jobs", _fake_run_jobs)

        response = await client.post(
            "/api/v1/internal/inventory/sync/run?tenantId=public",
            headers={"Authorization": "Bearer token-123"},
            json={"companyId": "ekaette-electronics"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["authMode"] == "oidc"
        assert payload["authSubject"] == "sync-sa@example.iam.gserviceaccount.com"
        assert payload["processed"] == 1
        assert payload["triggered"] == 1
