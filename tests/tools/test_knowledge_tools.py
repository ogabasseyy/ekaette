"""Tests for company knowledge tools — TDD for S12.5."""

from types import SimpleNamespace

import pytest


def _tool_context_with_state(state: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        user_id="user-1",
        session=SimpleNamespace(id="session-1"),
    )


class TestGetCompanyProfileFact:
    @pytest.mark.asyncio
    async def test_returns_fact_from_profile_facts_map(self):
        from app.tools.knowledge_tools import get_company_profile_fact

        ctx = _tool_context_with_state(
            {
                "app:company_id": "acme-hotel",
                "app:company_profile": {
                    "name": "Acme Grand Hotel",
                    "facts": {"rooms": 120, "check_in_time": "14:00"},
                },
            }
        )

        result = await get_company_profile_fact("rooms", tool_context=ctx)

        assert result["company_id"] == "acme-hotel"
        assert result["fact_key"] == "rooms"
        assert result["value"] == 120

    @pytest.mark.asyncio
    async def test_returns_top_level_profile_field_when_fact_missing(self):
        from app.tools.knowledge_tools import get_company_profile_fact

        ctx = _tool_context_with_state(
            {
                "app:company_id": "acme-hotel",
                "app:company_profile": {
                    "name": "Acme Grand Hotel",
                    "overview": "Luxury hospitality in downtown Lagos.",
                    "facts": {},
                },
            }
        )

        result = await get_company_profile_fact("overview", tool_context=ctx)
        assert result["value"] == "Luxury hospitality in downtown Lagos."

    @pytest.mark.asyncio
    async def test_returns_error_when_profile_missing(self):
        from app.tools.knowledge_tools import get_company_profile_fact

        ctx = _tool_context_with_state({"app:company_id": "acme-hotel"})
        result = await get_company_profile_fact("rooms", tool_context=ctx)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_supports_dotted_path_lookup(self):
        from app.tools.knowledge_tools import get_company_profile_fact

        ctx = _tool_context_with_state(
            {
                "app:company_id": "acme-hotel",
                "app:company_profile": {
                    "system_connectors": {
                        "crm": {"provider": "mock"}
                    }
                },
            }
        )

        result = await get_company_profile_fact(
            "system_connectors.crm.provider",
            tool_context=ctx,
        )
        assert result["value"] == "mock"


class TestSearchCompanyKnowledge:
    @pytest.mark.asyncio
    async def test_returns_ranked_knowledge_results(self):
        from app.tools.knowledge_tools import search_company_knowledge

        ctx = _tool_context_with_state(
            {
                "app:company_id": "acme-hotel",
                "app:company_knowledge": [
                    {
                        "id": "kb-1",
                        "title": "Late checkout policy",
                        "text": "Late checkout is available until 1 PM for premium guests.",
                        "tags": ["checkout", "policy"],
                    },
                    {
                        "id": "kb-2",
                        "title": "Breakfast schedule",
                        "text": "Breakfast runs from 6:30 AM to 10:30 AM daily.",
                        "tags": ["food"],
                    },
                ],
            }
        )

        result = await search_company_knowledge("late checkout", tool_context=ctx)

        assert result["query"] == "late checkout"
        assert len(result["results"]) >= 1
        assert result["results"][0]["id"] == "kb-1"

    @pytest.mark.asyncio
    async def test_returns_error_when_context_missing(self):
        from app.tools.knowledge_tools import search_company_knowledge

        result = await search_company_knowledge("checkout policy", tool_context=None)
        assert "error" in result
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_top_entries(self):
        from app.tools.knowledge_tools import search_company_knowledge

        ctx = _tool_context_with_state(
            {
                "app:company_id": "acme-hotel",
                "app:company_knowledge": [
                    {"id": "kb-1", "title": "A", "text": "Alpha"},
                    {"id": "kb-2", "title": "B", "text": "Beta"},
                    {"id": "kb-3", "title": "C", "text": "Gamma"},
                ],
            }
        )
        result = await search_company_knowledge("", max_results=2, tool_context=ctx)
        assert len(result["results"]) == 2


class TestQueryCompanySystem:
    @pytest.mark.asyncio
    async def test_returns_mock_connector_response(self):
        from app.tools.knowledge_tools import query_company_system

        ctx = _tool_context_with_state(
            {
                "app:company_id": "acme-hotel",
                "app:company_profile": {
                    "system_connectors": {
                        "crm": {
                            "mock_actions": {
                                "lookup_guest": {"vip": True, "loyalty_tier": "gold"}
                            }
                        }
                    }
                },
            }
        )

        result = await query_company_system(
            "crm",
            "lookup_guest",
            payload={"email": "guest@example.com"},
            tool_context=ctx,
        )

        assert result["system"] == "crm"
        assert result["action"] == "lookup_guest"
        assert result["result"]["vip"] is True

    @pytest.mark.asyncio
    async def test_returns_error_when_connector_not_configured(self):
        from app.tools.knowledge_tools import query_company_system

        ctx = _tool_context_with_state(
            {"app:company_id": "acme-hotel", "app:company_profile": {}}
        )
        result = await query_company_system("crm", "lookup_guest", tool_context=ctx)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_provider_not_implemented_for_non_mock_provider(self):
        from app.tools.knowledge_tools import query_company_system

        ctx = _tool_context_with_state(
            {
                "app:company_id": "acme-hotel",
                "app:company_profile": {
                    "system_connectors": {
                        "crm": {"provider": "salesforce"}
                    }
                },
            }
        )
        result = await query_company_system("crm", "lookup_guest", tool_context=ctx)
        assert "error" in result
        assert result["provider"] == "salesforce"
