"""Versioned response contract tests for /api/v1/admin endpoints."""

from unittest.mock import AsyncMock

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

    # Force in-memory idempotency backend for tests — contract tests must
    # never hit real Firestore.  The production .env may set "firestore"
    # which would read stale documents that reset_runtime_state() cannot clear.
    original_backend = admin_settings.IDEMPOTENCY_STORE_BACKEND
    admin_settings.IDEMPOTENCY_STORE_BACKEND = "memory"
    admin_settings.reset_runtime_state()
    yield
    admin_settings.reset_runtime_state()
    admin_settings.IDEMPOTENCY_STORE_BACKEND = original_backend


def _admin_headers(*, tenant_id: str = "public") -> dict[str, str]:
    return {
        "x-user-id": "contract-admin",
        "x-tenant-id": tenant_id,
        "x-roles": "tenant_admin",
    }


def _company_doc(company_id: str = "ekaette-telecom", template_id: str = "telecom") -> dict[str, object]:
    return {
        "schema_version": 1,
        "tenant_id": "public",
        "company_id": company_id,
        "industry_template_id": template_id,
        "display_name": "Contract Company",
        "status": "active",
        "connectors": {},
        "overview": "Contract overview",
        "facts": {"sla": "24/7"},
        "links": ["https://example.com"],
    }


class TestAdminV1Contracts:
    @pytest.mark.asyncio
    async def test_get_admin_companies_contract(self, client, monkeypatch):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(_db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [{"id": "ekaette-telecom", "templateId": "telecom", "displayName": "Telecom"}],
                "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
            }

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )

        response = await client.get(
            "/api/v1/admin/companies?tenantId=public",
            headers=_admin_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["tenantId"] == "public"
        assert isinstance(payload["companies"], list)
        assert isinstance(payload["count"], int)

    @pytest.mark.asyncio
    async def test_post_admin_company_contract(self, client, admin_runtime, monkeypatch):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(_db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [],
                "defaults": {"templateId": "telecom", "companyId": ""},
            }

        async def _fake_upsert(_db, **kwargs):
            return True, _company_doc(company_id=kwargs["company_id"], template_id=kwargs["industry_template_id"])

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(admin_runtime, "_upsert_registry_company_doc", _fake_upsert)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", AsyncMock())

        response = await client.post(
            "/api/v1/admin/companies?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-company-create-1"},
            json={
                "companyId": "ekaette-telecom",
                "displayName": "Ekaette Telecom",
                "industryTemplateId": "telecom",
                "status": "active",
            },
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["tenantId"] == "public"
        assert payload["companyId"] == "ekaette-telecom"
        assert isinstance(payload["company"], dict)

    @pytest.mark.asyncio
    async def test_get_admin_company_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"]), None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)

        response = await client.get(
            "/api/v1/admin/companies/ekaette-telecom?tenantId=public",
            headers=_admin_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-telecom"
        assert isinstance(payload["company"], dict)
        assert payload["company"]["schemaVersion"] == 1

    @pytest.mark.asyncio
    async def test_put_admin_company_contract(self, client, admin_runtime, monkeypatch):
        from app.configs import registry_loader as registry_loader_module

        async def _fake_build_onboarding_config(_db, tenant_id):
            return {
                "tenantId": tenant_id,
                "templates": [{"id": "telecom", "label": "Telecom"}],
                "companies": [{"id": "ekaette-telecom", "templateId": "telecom"}],
                "defaults": {"templateId": "telecom", "companyId": "ekaette-telecom"},
            }

        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"]), None

        monkeypatch.setattr(
            registry_loader_module, "build_onboarding_config", _fake_build_onboarding_config
        )
        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", AsyncMock())

        response = await client.put(
            "/api/v1/admin/companies/ekaette-telecom?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-company-update-1"},
            json={
                "displayName": "Updated Name",
                "industryTemplateId": "telecom",
                "status": "active",
                "connectors": {},
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-telecom"
        assert payload["updated"] is True
        assert isinstance(payload["company"], dict)

    @pytest.mark.asyncio
    async def test_get_admin_mcp_providers_contract(self, client, admin_runtime, monkeypatch):
        monkeypatch.setattr(
            admin_runtime,
            "_effective_mcp_provider_catalog",
            lambda: {
                "mock": {
                    "id": "mock",
                    "label": "Mock Provider",
                    "status": "active",
                    "requiresSecretRef": False,
                    "capabilities": ["read"],
                    "testPolicy": {
                        "timeoutSeconds": 1.0,
                        "maxRetries": 0,
                        "circuitOpenAfterFailures": 2,
                        "circuitOpenSeconds": 10,
                        "allowedHosts": [],
                    },
                }
            },
        )

        response = await client.get(
            "/api/v1/admin/mcp/providers?tenantId=public",
            headers=_admin_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["tenantId"] == "public"
        assert isinstance(payload["providers"], list)
        assert payload["providers"][0]["id"] == "mock"

    @pytest.mark.asyncio
    async def test_get_admin_company_knowledge_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"]), None

        async def _fake_load_knowledge(_db, _company_id, limit=12, *, tenant_id=None):
            return [
                {
                    "id": "kb-1",
                    "title": "FAQ",
                    "text": "Open daily",
                    "tags": ["faq"],
                    "source": "text",
                }
            ]

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "load_company_knowledge", _fake_load_knowledge)

        response = await client.get(
            "/api/v1/admin/companies/ekaette-telecom/knowledge?tenantId=public",
            headers=_admin_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-telecom"
        assert isinstance(payload["entries"], list)

    @pytest.mark.asyncio
    async def test_post_knowledge_import_text_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"]), None

        async def _fake_write_knowledge(**kwargs):
            return None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_write_company_knowledge_entry", _fake_write_knowledge)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/knowledge/import-text?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-kb-text-1"},
            json={"title": "Policy", "text": "Open 24/7", "tags": ["policy"]},
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-telecom"
        assert isinstance(payload["entry"], dict)

    @pytest.mark.asyncio
    async def test_post_knowledge_import_url_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"]), None

        async def _fake_write_knowledge(**kwargs):
            return None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_write_company_knowledge_entry", _fake_write_knowledge)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/knowledge/import-url?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-kb-url-1"},
            json={"url": "https://example.com/policy", "title": "Policy URL"},
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-telecom"
        assert payload["entry"]["source"] == "url"

    @pytest.mark.asyncio
    async def test_post_knowledge_import_file_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"]), None

        async def _fake_write_knowledge(**kwargs):
            return None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_write_company_knowledge_entry", _fake_write_knowledge)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/knowledge/import-file?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-kb-file-1"},
            data={"title": "File KB", "tags": "file,policy", "source": "file"},
            files={"file": ("kb.txt", b"knowledge file text", "text/plain")},
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-telecom"
        assert isinstance(payload["knowledgeId"], str)

    @pytest.mark.asyncio
    async def test_delete_knowledge_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"]), None

        async def _fake_delete_knowledge(**kwargs):
            return True

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_delete_company_knowledge_entry", _fake_delete_knowledge)

        response = await client.delete(
            "/api/v1/admin/companies/ekaette-telecom/knowledge/kb-1?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-kb-delete-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["deleted"] is True

    @pytest.mark.asyncio
    async def test_post_connector_create_contract(self, client, admin_runtime, monkeypatch):
        company_state = _company_doc()

        async def _fake_load_company(**kwargs):
            return dict(company_state), None

        async def _fake_save_company(**kwargs):
            company_state.update(kwargs["payload"])

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", _fake_save_company)
        monkeypatch.setattr(admin_runtime, "_registry_db_client", lambda: None)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-connector-create-1"},
            json={
                "connectorId": "crm",
                "provider": "mock",
                "enabled": True,
                "capabilities": ["read"],
                "config": {},
            },
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["connectorId"] == "crm"
        assert isinstance(payload["connector"], dict)

    @pytest.mark.asyncio
    async def test_put_connector_update_contract(self, client, admin_runtime, monkeypatch):
        company_state = _company_doc()
        company_state["connectors"] = {
            "crm": {
                "id": "crm",
                "provider": "mock",
                "enabled": True,
                "capabilities": ["read"],
                "config": {},
                "runtime_policy": {
                    "timeoutSeconds": 1.0,
                    "maxRetries": 0,
                    "circuitOpenAfterFailures": 2,
                    "circuitOpenSeconds": 10,
                    "allowedHosts": [],
                },
            }
        }

        async def _fake_load_company(**kwargs):
            return dict(company_state), None

        async def _fake_save_company(**kwargs):
            company_state.update(kwargs["payload"])

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", _fake_save_company)
        monkeypatch.setattr(admin_runtime, "_registry_db_client", lambda: None)

        # Mock provider catalog to allow all capabilities for this contract test
        monkeypatch.setattr(
            admin_runtime,
            "_effective_mcp_provider_catalog",
            lambda: {"mock": {"capabilities": ["read", "write"], "requiresSecretRef": False}},
        )

        response = await client.put(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-connector-update-1"},
            json={
                "provider": "mock",
                "enabled": True,
                "capabilities": ["read", "write"],
                "config": {},
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["updated"] is True
        assert payload["connectorId"] == "crm"

    @pytest.mark.asyncio
    async def test_post_connector_test_contract(self, client, admin_runtime, monkeypatch):
        company_state = _company_doc()
        company_state["connectors"] = {
            "crm": {
                "id": "crm",
                "provider": "mock",
                "enabled": True,
                "capabilities": ["read"],
                "config": {},
                "runtime_policy": {
                    "timeoutSeconds": 1.0,
                    "maxRetries": 0,
                    "circuitOpenAfterFailures": 2,
                    "circuitOpenSeconds": 10,
                    "allowedHosts": [],
                },
            }
        }

        async def _fake_load_company(**kwargs):
            return dict(company_state), None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm/test?tenantId=public",
            headers=_admin_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["ok"] is True
        assert payload["connectorId"] == "crm"

    @pytest.mark.asyncio
    async def test_delete_connector_contract(self, client, admin_runtime, monkeypatch):
        company_state = _company_doc()
        company_state["connectors"] = {"crm": {"id": "crm", "provider": "mock"}}

        async def _fake_load_company(**kwargs):
            return dict(company_state), None

        async def _fake_save_company(**kwargs):
            company_state.update(kwargs["payload"])

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", _fake_save_company)
        monkeypatch.setattr(admin_runtime, "_registry_db_client", lambda: None)

        response = await client.delete(
            "/api/v1/admin/companies/ekaette-telecom/connectors/crm?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-connector-delete-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["deleted"] is True

    @pytest.mark.asyncio
    async def test_post_demo_seed_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="electronics"), None

        async def _fake_seed_demo(**kwargs):
            return {
                "seedVersion": "electronics-v1",
                "dataTier": "demo",
                "ok": True,
                "sections": {
                    "knowledge": {"ok": True, "written": 4},
                    "connectors": {"ok": True, "connectorId": "crm", "created": True},
                    "products": {"ok": True, "written": 12},
                    "booking_slots": {"ok": True, "written": 8},
                },
                "errors": [],
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_seed_company_demo_data", _fake_seed_demo)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-electronics/seed/demo?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-seed-demo-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-electronics"
        assert payload["seedVersion"] == "electronics-v1"
        assert payload["ok"] is True
        assert isinstance(payload["sections"], dict)
        assert isinstance(payload["errors"], list)

    @pytest.mark.asyncio
    async def test_post_products_import_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="electronics"), None

        async def _fake_import_products(**kwargs):
            return {
                "written": len(kwargs["products"]),
                "operations": {"created": 1, "updated": 0, "unchanged": 0, "failed": 0},
                "errors": [],
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_import_company_products", _fake_import_products)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-electronics/products/import?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-products-1"},
            json={
                "products": [
                    {
                        "id": "iphone-13",
                        "name": "iPhone 13",
                        "price": 500,
                        "currency": "USD",
                        "category": "phones",
                        "in_stock": True,
                    }
                ],
                "data_tier": "admin",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["collection"] == "products"
        assert isinstance(payload["operations"], dict)

    @pytest.mark.asyncio
    async def test_post_inventory_sync_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="electronics"), None

        async def _fake_sync_google_sheet(**kwargs):
            return {
                "parsedRows": 2,
                "normalizedRows": 2,
                "written": 2,
                "operations": {"created": 2, "updated": 0, "unchanged": 0, "failed": 0},
                "errors": [],
                "dryRun": False,
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_sync_company_inventory_from_google_sheet", _fake_sync_google_sheet)
        monkeypatch.setattr(admin_runtime, "_inventory_sync_metadata", lambda **kwargs: {"status": "success"})
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", AsyncMock())

        response = await client.post(
            "/api/v1/admin/companies/ekaette-electronics/inventory/sync?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-inventory-sync-1"},
            json={
                "sourceType": "google_sheets",
                "sourceUrl": "https://docs.google.com/spreadsheets/d/test-sheet-id/edit#gid=0",
                "dataTier": "admin",
                "dryRun": False,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["sourceType"] == "google_sheets"
        assert payload["companyId"] == "ekaette-electronics"
        assert isinstance(payload["operations"], dict)

    @pytest.mark.asyncio
    async def test_post_inventory_upload_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="electronics"), None

        async def _fake_sync_upload(**kwargs):
            return {
                "parsedRows": 1,
                "normalizedRows": 1,
                "written": 1,
                "operations": {"created": 1, "updated": 0, "unchanged": 0, "failed": 0},
                "errors": [],
                "dryRun": False,
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_sync_company_inventory_from_upload", _fake_sync_upload)
        monkeypatch.setattr(admin_runtime, "_inventory_sync_metadata", lambda **kwargs: {"status": "success"})
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", AsyncMock())

        response = await client.post(
            "/api/v1/admin/companies/ekaette-electronics/inventory/upload?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-inventory-upload-1"},
            data={"data_tier": "admin", "dry_run": "false"},
            files={"file": ("inventory.csv", b"id,name,category,price,currency,in_stock\nsku-1,iPhone,phones,500,USD,true\n", "text/csv")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-electronics"
        assert payload["fileName"] == "inventory.csv"
        assert isinstance(payload["operations"], dict)

    @pytest.mark.asyncio
    async def test_put_inventory_sync_config_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="electronics"), None

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_inventory_sync_metadata", lambda **kwargs: {"status": "configured"})
        monkeypatch.setattr(admin_runtime, "_save_registry_company_doc", AsyncMock())

        response = await client.put(
            "/api/v1/admin/companies/ekaette-electronics/inventory/sync/config?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-inventory-config-1"},
            json={
                "sourceType": "google_sheets",
                "sourceUrl": "https://docs.google.com/spreadsheets/d/test-sheet-id/edit#gid=0",
                "dataTier": "admin",
                "dryRun": False,
                "autoEnabled": True,
                "intervalMinutes": 30,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["configured"] is True
        assert isinstance(payload["inventorySync"], dict)

    @pytest.mark.asyncio
    async def test_post_inventory_sync_run_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_run_jobs(**kwargs):
            return {
                "tenantId": "public",
                "companyId": kwargs.get("company_id"),
                "force": kwargs.get("force", False),
                "dryRunOverride": kwargs.get("dry_run_override"),
                "processed": 1,
                "triggered": 1,
                "skipped": 0,
                "results": [{"companyId": "ekaette-electronics", "status": "success", "written": 2}],
                "runAt": "2026-02-28T10:00:00+00:00",
            }

        monkeypatch.setattr(admin_runtime, "_run_inventory_sync_jobs", _fake_run_jobs)

        response = await client.post(
            "/api/v1/admin/inventory/sync/run?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-inventory-run-1"},
            json={
                "companyId": "ekaette-electronics",
                "maxCompanies": 10,
                "force": True,
                "dryRunOverride": False,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["processed"] == 1
        assert payload["triggered"] == 1
        assert isinstance(payload["results"], list)

    @pytest.mark.asyncio
    async def test_post_booking_slots_import_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="hotel"), None

        async def _fake_import_slots(**kwargs):
            return {
                "written": len(kwargs["slots"]),
                "operations": {"created": 1, "updated": 0, "unchanged": 0, "failed": 0},
                "errors": [],
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_import_company_booking_slots", _fake_import_slots)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/booking-slots/import?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-slots-1"},
            json={
                "slots": [{"id": "slot-1", "date": "2026-03-01", "time": "10:00", "available": True}],
                "data_tier": "admin",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["collection"] == "booking_slots"
        assert isinstance(payload["operations"], dict)

    @pytest.mark.asyncio
    async def test_post_runtime_purge_demo_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="hotel"), None

        async def _fake_purge(**kwargs):
            return {"products": 1, "booking_slots": 2, "knowledge": 3}

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_purge_company_demo_runtime_data", _fake_purge)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/runtime/purge-demo?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-purge-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-hotel"
        assert isinstance(payload["deleted"], dict)

    @pytest.mark.asyncio
    async def test_post_company_export_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="hotel"), None

        async def _fake_export_bundle(**kwargs):
            return {
                "company": {"id": kwargs["company_id"], "schemaVersion": 1},
                "collections": {"knowledge": [], "products": [], "booking_slots": []},
                "counts": {"knowledge": 0, "products": 0, "booking_slots": 0},
            }

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_export_company_bundle", _fake_export_bundle)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/export?tenantId=public",
            headers=_admin_headers(),
            json={"includeRuntimeData": True},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-hotel"
        assert isinstance(payload["collections"], dict)
        assert isinstance(payload["counts"], dict)

    @pytest.mark.asyncio
    async def test_delete_company_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="hotel"), None

        async def _fake_delete_bundle(**kwargs):
            return {"knowledge": 1, "products": 2, "booking_slots": 3, "company": 1}

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_delete_company_bundle", _fake_delete_bundle)

        response = await client.delete(
            "/api/v1/admin/companies/ekaette-hotel?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-company-delete-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-hotel"
        assert isinstance(payload["deleted"], dict)

    @pytest.mark.asyncio
    async def test_post_retention_purge_contract(self, client, admin_runtime, monkeypatch):
        async def _fake_load_company(**kwargs):
            return _company_doc(company_id=kwargs["company_id"], template_id="hotel"), None

        async def _fake_retention_purge(**kwargs):
            return {"knowledge": {"scanned": 4, "deleted": 2, "skipped": 2, "missing_timestamp": 0}}

        monkeypatch.setattr(admin_runtime, "_load_registry_company_doc", _fake_load_company)
        monkeypatch.setattr(admin_runtime, "_purge_company_retention_data", _fake_retention_purge)

        response = await client.post(
            "/api/v1/admin/companies/ekaette-hotel/retention/purge?tenantId=public",
            headers={**_admin_headers(), "Idempotency-Key": "contract-retention-1"},
            json={"olderThanDays": 30, "collections": ["knowledge"], "dataTier": "demo"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["apiVersion"] == "v1"
        assert payload["companyId"] == "ekaette-hotel"
        assert isinstance(payload["report"], dict)
