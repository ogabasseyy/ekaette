"""Tests for company profile/knowledge loading — TDD for S12.5."""

from unittest.mock import AsyncMock, MagicMock

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
