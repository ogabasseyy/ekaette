"""Tests for company profile/knowledge loading — TDD for S12.5."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLoadCompanyProfile:
    """Company profile should load from Firestore with safe fallback."""

    @pytest.mark.asyncio
    async def test_returns_local_fallback_when_firestore_unavailable(self):
        from app.configs.company_loader import load_company_profile

        profile = await load_company_profile(None, "acme-hotel")

        assert profile["company_id"] == "acme-hotel"
        assert "name" in profile
        assert "overview" in profile

    @pytest.mark.asyncio
    async def test_returns_industry_specific_local_fallback_profile(self):
        from app.configs.company_loader import load_company_profile

        profile = await load_company_profile(None, "ekaette-electronics")
        assert profile["company_id"] == "ekaette-electronics"
        assert profile["name"] == "Ekaette Devices Hub"
        assert profile["facts"]["support_hours"] == "09:00-19:00"

    @pytest.mark.asyncio
    async def test_loads_profile_from_firestore_document(self):
        from app.configs.company_loader import load_company_profile

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "name": "Acme Grand Hotel",
            "overview": "Luxury hospitality in downtown Lagos.",
            "facts": {"rooms": 120, "check_in_time": "14:00"},
            "system_connectors": {"crm": {"provider": "mock"}},
        }

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        profile = await load_company_profile(mock_db, "acme-hotel")

        mock_db.collection.assert_called_once_with("company_profiles")
        mock_collection.document.assert_called_once_with("acme-hotel")
        assert profile["company_id"] == "acme-hotel"
        assert profile["name"] == "Acme Grand Hotel"
        assert profile["facts"]["rooms"] == 120

    @pytest.mark.asyncio
    async def test_registry_enabled_loads_tenant_scoped_profile_and_projects_legacy_shape(self, monkeypatch):
        """Phase 1 adapter: tenant/company registry docs map into legacy profile shape."""
        from app.configs.company_loader import load_company_profile

        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        monkeypatch.setenv("REGISTRY_DEFAULT_TENANT_ID", "public")
        mock_db = MagicMock()

        with patch(
            "app.configs.registry_loader.load_tenant_company",
            AsyncMock(
                return_value={
                    "company_id": "ekaette-telecom",
                    "tenant_id": "public",
                    "industry_template_id": "telecom",
                    "display_name": "Ekaette Telecom",
                    "overview": "Customer support and plan sales.",
                    "facts": {"support_hours": "24/7"},
                    "links": [{"label": "Home", "url": "https://example.com"}],
                    "connectors": {"crm": {"provider": "mock-crm"}},
                }
            ),
        ) as mock_registry_company:
            profile = await load_company_profile(mock_db, "ekaette-telecom", tenant_id="public")

        mock_registry_company.assert_awaited_once_with(mock_db, "public", "ekaette-telecom")
        mock_db.collection.assert_not_called()
        assert profile["company_id"] == "ekaette-telecom"
        assert profile["name"] == "Ekaette Telecom"
        assert profile["overview"] == "Customer support and plan sales."
        assert profile["facts"]["support_hours"] == "24/7"
        assert profile["system_connectors"]["crm"]["provider"] == "mock-crm"

    @pytest.mark.asyncio
    async def test_registry_enabled_profile_falls_back_to_legacy_collection_on_registry_miss(self, monkeypatch):
        """Phase 1 adapter: registry miss should preserve current Firestore path behavior."""
        from app.configs.company_loader import load_company_profile

        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        monkeypatch.setenv("REGISTRY_DEFAULT_TENANT_ID", "public")

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "name": "Legacy Profile",
            "overview": "Legacy fallback path.",
            "facts": {"rooms": 10},
            "system_connectors": {"crm": {"provider": "legacy"}},
        }

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        with patch(
            "app.configs.registry_loader.load_tenant_company",
            AsyncMock(return_value=None),
        ) as mock_registry_company:
            profile = await load_company_profile(mock_db, "acme-hotel", tenant_id="public")

        mock_registry_company.assert_awaited_once_with(mock_db, "public", "acme-hotel")
        mock_db.collection.assert_called_once_with("company_profiles")
        assert profile["name"] == "Legacy Profile"
        assert profile["facts"]["rooms"] == 10


class TestLoadCompanyKnowledge:
    """Company knowledge entries should load and normalize safely."""

    @pytest.mark.asyncio
    async def test_loads_knowledge_entries_from_firestore(self):
        from app.configs.company_loader import load_company_knowledge

        doc_1 = MagicMock()
        doc_1.id = "kb-1"
        doc_1.to_dict.return_value = {
            "company_id": "acme-hotel",
            "title": "Late checkout policy",
            "text": "Late checkout is available until 1 PM for premium guests.",
            "tags": ["policy", "checkout"],
        }

        doc_2 = MagicMock()
        doc_2.id = "kb-2"
        doc_2.to_dict.return_value = {
            "company_id": "acme-hotel",
            "title": "Suite inventory",
            "text": "We have 20 executive suites and 100 standard rooms.",
            "url": "https://example.com/rooms",
            "tags": ["inventory"],
        }

        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter([doc_1, doc_2])

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_query

        entries = await load_company_knowledge(mock_db, "acme-hotel", limit=5)

        mock_db.collection.assert_called_once_with("company_knowledge")
        mock_query.where.assert_called_once_with("company_id", "==", "acme-hotel")
        assert len(entries) == 2
        assert {entry["id"] for entry in entries} == {"kb-1", "kb-2"}
        assert all(entry["company_id"] == "acme-hotel" for entry in entries)

    @pytest.mark.asyncio
    async def test_returns_fallback_knowledge_when_firestore_unavailable(self):
        from app.configs.company_loader import load_company_knowledge

        entries = await load_company_knowledge(None, "unknown-company")

        assert isinstance(entries, list)
        assert len(entries) >= 1
        assert all("text" in entry for entry in entries)

    @pytest.mark.asyncio
    async def test_returns_company_specific_fallback_knowledge_when_available(self):
        from app.configs.company_loader import load_company_knowledge

        entries = await load_company_knowledge(None, "ekaette-fashion")
        titles = {entry["title"] for entry in entries}
        assert "Return policy" in titles
        assert "Sizing assistance" in titles

    @pytest.mark.asyncio
    async def test_registry_enabled_loads_tenant_scoped_knowledge_subcollection(self, monkeypatch):
        """Phase 1 adapter: tenant/company knowledge path is used when registry mode is enabled."""
        from app.configs.company_loader import load_company_knowledge

        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        monkeypatch.setenv("REGISTRY_DEFAULT_TENANT_ID", "public")

        doc = MagicMock()
        doc.id = "kb-telecom-1"
        doc.to_dict.return_value = {
            "title": "SIM replacement policy",
            "text": "Bring valid ID to replace a SIM card.",
            "tags": ["policy", "sim"],
        }

        knowledge_query = MagicMock()
        knowledge_query.limit.return_value = knowledge_query
        knowledge_query.stream.return_value = iter([doc])

        company_doc_ref = MagicMock()
        company_doc_ref.collection.return_value = knowledge_query

        companies_collection = MagicMock()
        companies_collection.document.return_value = company_doc_ref

        tenant_doc_ref = MagicMock()
        tenant_doc_ref.collection.return_value = companies_collection

        tenants_collection = MagicMock()
        tenants_collection.document.return_value = tenant_doc_ref

        mock_db = MagicMock()
        mock_db.collection.return_value = tenants_collection

        entries = await load_company_knowledge(
            mock_db,
            "ekaette-telecom",
            tenant_id="public",
            limit=5,
        )

        # Registry path should start at tenants/{tenant}/companies/{company}/knowledge
        mock_db.collection.assert_called_once_with("tenants")
        tenants_collection.document.assert_called_once_with("public")
        tenant_doc_ref.collection.assert_called_once_with("companies")
        companies_collection.document.assert_called_once_with("ekaette-telecom")
        company_doc_ref.collection.assert_called_once_with("knowledge")
        knowledge_query.limit.assert_called_once_with(5)
        assert len(entries) == 1
        assert entries[0]["company_id"] == "ekaette-telecom"
        assert entries[0]["id"] == "kb-telecom-1"
        assert entries[0]["title"] == "SIM replacement policy"

    @pytest.mark.asyncio
    async def test_registry_enabled_knowledge_falls_back_to_legacy_collection_when_registry_empty(self, monkeypatch):
        """Phase 1 adapter: preserve legacy company_knowledge path if registry path returns no entries."""
        from app.configs.company_loader import load_company_knowledge

        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        monkeypatch.setenv("REGISTRY_DEFAULT_TENANT_ID", "public")

        registry_query = MagicMock()
        registry_query.limit.return_value = registry_query
        registry_query.stream.return_value = iter([])

        company_doc_ref = MagicMock()
        company_doc_ref.collection.return_value = registry_query
        companies_collection = MagicMock()
        companies_collection.document.return_value = company_doc_ref
        tenant_doc_ref = MagicMock()
        tenant_doc_ref.collection.return_value = companies_collection
        tenants_collection = MagicMock()
        tenants_collection.document.return_value = tenant_doc_ref

        legacy_doc = MagicMock()
        legacy_doc.id = "kb-legacy-1"
        legacy_doc.to_dict.return_value = {
            "company_id": "acme-hotel",
            "title": "Late checkout policy",
            "text": "Late checkout until 1 PM.",
            "tags": ["policy"],
        }
        legacy_query = MagicMock()
        legacy_query.where.return_value = legacy_query
        legacy_query.limit.return_value = legacy_query
        legacy_query.stream.return_value = iter([legacy_doc])

        mock_db = MagicMock()

        def _collection(name: str):
            if name == "tenants":
                return tenants_collection
            if name == "company_knowledge":
                return legacy_query
            raise AssertionError(f"unexpected collection {name}")

        mock_db.collection.side_effect = _collection

        entries = await load_company_knowledge(mock_db, "acme-hotel", tenant_id="public", limit=3)

        assert len(entries) == 1
        assert entries[0]["id"] == "kb-legacy-1"
        assert entries[0]["company_id"] == "acme-hotel"
        legacy_query.where.assert_called_once_with("company_id", "==", "acme-hotel")
        legacy_query.limit.assert_called_once_with(3)


class TestBuildCompanySessionState:
    def test_builds_prefixed_session_state(self):
        from app.configs.company_loader import build_company_session_state

        state = build_company_session_state(
            company_id="acme-hotel",
            profile={
                "company_id": "acme-hotel",
                "name": "Acme Grand Hotel",
                "facts": {"rooms": 120},
            },
            knowledge=[
                {
                    "id": "kb-1",
                    "company_id": "acme-hotel",
                    "title": "Check-in",
                    "text": "Check-in starts at 2 PM.",
                }
            ],
        )

        assert state["app:company_id"] == "acme-hotel"
        assert state["app:company_profile"]["name"] == "Acme Grand Hotel"
        assert state["app:company_knowledge"][0]["id"] == "kb-1"
        for key in state:
            assert key.startswith("app:")

    def test_tolerates_invalid_profile_and_knowledge_inputs(self):
        from app.configs.company_loader import build_company_session_state

        state = build_company_session_state(
            company_id="acme-hotel",
            profile="not-a-dict",  # type: ignore[arg-type]
            knowledge=[None, "plain text", {"title": "Valid", "text": "Entry"}],  # type: ignore[list-item]
        )

        assert state["app:company_id"] == "acme-hotel"
        assert isinstance(state["app:company_profile"], dict)
        assert isinstance(state["app:company_knowledge"], list)
        assert len(state["app:company_knowledge"]) >= 1
