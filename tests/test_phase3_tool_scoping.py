"""Phase 3 — Tool scoping + capability guard tests (TDD Red).

Tests for:
1. scoped_queries.py: tenant/company-scoped Firestore collection helper
2. Capability guard in callbacks.py: TOOL_CAPABILITY_MAP + before_tool check
3. Booking tools: company-scoped queries
4. Catalog tools: company-scoped queries
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ═══ Helpers ═══


def _make_tool_context(state: dict[str, Any]) -> SimpleNamespace:
    """Build a minimal ToolContext-like object with state."""
    return SimpleNamespace(
        state=dict(state),
        agent_name="test_agent",
    )


def _make_state(
    *,
    tenant_id: str = "public",
    company_id: str = "ekaette-electronics",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Build session state with canonical keys."""
    state: dict[str, Any] = {
        "app:tenant_id": tenant_id,
        "app:company_id": company_id,
    }
    if capabilities is not None:
        state["app:capabilities"] = capabilities
    return state


# ═══ 1. Scoped Queries Helper ═══


class TestScopedCollection:
    """scoped_collection() builds tenant/company-scoped Firestore paths."""

    def test_returns_scoped_collection_ref(self):
        from app.tools.scoped_queries import scoped_collection

        mock_db = MagicMock()
        mock_subcol = MagicMock()
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.collection.return_value = mock_subcol

        ctx = _make_tool_context(_make_state(
            tenant_id="acme",
            company_id="acme-hotel",
        ))
        result = scoped_collection(mock_db, ctx, "booking_slots")

        # Should build: tenants/acme/companies/acme-hotel/booking_slots
        mock_db.collection.assert_called_with("tenants")
        assert result == mock_subcol

    def test_returns_none_when_tenant_missing(self):
        from app.tools.scoped_queries import scoped_collection

        mock_db = MagicMock()
        ctx = _make_tool_context({"app:company_id": "acme-hotel"})
        result = scoped_collection(mock_db, ctx, "booking_slots")
        assert result is None

    def test_returns_none_when_company_missing(self):
        from app.tools.scoped_queries import scoped_collection

        mock_db = MagicMock()
        ctx = _make_tool_context({"app:tenant_id": "acme"})
        result = scoped_collection(mock_db, ctx, "booking_slots")
        assert result is None

    def test_returns_none_when_db_is_none(self):
        from app.tools.scoped_queries import scoped_collection

        ctx = _make_tool_context(_make_state())
        result = scoped_collection(None, ctx, "booking_slots")
        assert result is None

    def test_falls_back_to_global_when_no_tenant(self):
        """When tenant_id is absent and fallback=True, use global collection."""
        from app.tools.scoped_queries import scoped_collection_or_global

        mock_db = MagicMock()
        mock_global = MagicMock()
        mock_db.collection.return_value = mock_global

        ctx = _make_tool_context({"app:company_id": "acme-hotel"})
        result = scoped_collection_or_global(mock_db, ctx, "booking_slots")
        mock_db.collection.assert_called_with("booking_slots")
        assert result == mock_global


# ═══ 2. Capability Guards ═══


class TestCapabilityGuard:
    """before_tool_capability_guard blocks tools missing required capabilities."""

    @pytest.mark.asyncio
    async def test_allows_tool_with_required_capability(self):
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="create_booking")
        ctx = _make_tool_context(_make_state(
            capabilities=["booking_reservations", "catalog_lookup"],
        ))
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result is None  # No block

    @pytest.mark.asyncio
    async def test_blocks_tool_missing_capability(self):
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="create_booking")
        ctx = _make_tool_context(_make_state(
            capabilities=["catalog_lookup"],  # No booking_reservations
        ))
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert isinstance(result, dict)
        assert result["error"] == "capability_not_enabled"
        assert result["tool"] == "create_booking"
        assert result["required"] == "booking_reservations"

    @pytest.mark.asyncio
    async def test_allows_tool_not_in_capability_map(self):
        """Tools not in TOOL_CAPABILITY_MAP are always allowed."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="PreloadMemoryTool")
        ctx = _make_tool_context(_make_state(capabilities=[]))
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_allows_when_no_capabilities_in_state(self):
        """When app:capabilities not set, all tools allowed (compat mode)."""
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="create_booking")
        ctx = _make_tool_context({"app:company_id": "some-co"})
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_blocks_catalog_without_catalog_lookup(self):
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="search_catalog")
        ctx = _make_tool_context(_make_state(
            capabilities=["booking_reservations"],
        ))
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result["error"] == "capability_not_enabled"
        assert result["required"] == "catalog_lookup"

    @pytest.mark.asyncio
    async def test_blocks_valuation_tool_without_capability(self):
        from app.agents.callbacks import before_tool_capability_guard

        tool = SimpleNamespace(name="grade_and_value_tool")
        ctx = _make_tool_context(_make_state(
            capabilities=["catalog_lookup", "booking_reservations"],
        ))
        result = await before_tool_capability_guard(tool, {}, ctx)
        assert result["error"] == "capability_not_enabled"
        assert result["required"] == "valuation_tradein"

    @pytest.mark.asyncio
    async def test_aviation_blocks_booking_allows_knowledge(self):
        """Aviation template: booking blocked, knowledge allowed."""
        from app.agents.callbacks import before_tool_capability_guard

        aviation_caps = ["policy_qa", "public_search_fallback", "flight_status_lookup"]
        ctx = _make_tool_context(_make_state(capabilities=aviation_caps))

        booking_tool = SimpleNamespace(name="create_booking")
        result = await before_tool_capability_guard(booking_tool, {}, ctx)
        assert result is not None
        assert result["error"] == "capability_not_enabled"

        knowledge_tool = SimpleNamespace(name="search_company_knowledge")
        result = await before_tool_capability_guard(knowledge_tool, {}, ctx)
        assert result is None  # Allowed


class TestToolCapabilityMap:
    """TOOL_CAPABILITY_MAP covers all critical tools."""

    def test_map_covers_booking_tools(self):
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert TOOL_CAPABILITY_MAP["create_booking"] == "booking_reservations"
        assert TOOL_CAPABILITY_MAP["cancel_booking"] == "booking_reservations"
        assert TOOL_CAPABILITY_MAP["check_availability"] == "booking_reservations"

    def test_map_covers_catalog_tools(self):
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert TOOL_CAPABILITY_MAP["search_catalog"] == "catalog_lookup"

    def test_map_covers_valuation_tools(self):
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert TOOL_CAPABILITY_MAP["grade_and_value_tool"] == "valuation_tradein"
        assert TOOL_CAPABILITY_MAP["analyze_device_image_tool"] == "valuation_tradein"

    def test_map_covers_knowledge_tools(self):
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert TOOL_CAPABILITY_MAP["search_company_knowledge"] == "policy_qa"
        assert TOOL_CAPABILITY_MAP["get_company_profile_fact"] == "policy_qa"

    def test_map_covers_connector_dispatch(self):
        from app.agents.callbacks import TOOL_CAPABILITY_MAP

        assert TOOL_CAPABILITY_MAP["query_company_system"] == "connector_dispatch"


# ═══ 3. Booking Tools — Company Scoped ═══


class TestBookingToolsScoped:
    """Booking tools should use tenant/company-scoped Firestore paths."""

    @pytest.mark.asyncio
    async def test_check_availability_uses_scoped_collection(self):
        """check_availability should query scoped booking_slots."""
        from app.tools.booking_tools import check_availability

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.stream.return_value = iter([])

        ctx = _make_tool_context(_make_state(
            tenant_id="public",
            company_id="ekaette-hotel",
        ))

        with patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db), \
             patch("app.tools.booking_tools.scoped_collection_or_global", return_value=mock_query) as mock_scoped:
            result = await check_availability(date="2026-03-01", tool_context=ctx)

        mock_scoped.assert_called_once()
        assert result.get("error") is None or result["slots"] == []

    @pytest.mark.asyncio
    async def test_create_booking_stores_tenant_and_company(self):
        """create_booking should write tenant_id + company_id on the booking doc."""
        from app.tools.booking_tools import create_booking

        mock_db = MagicMock()
        slot_doc = MagicMock()
        slot_doc.exists = True
        slot_doc.to_dict.return_value = {
            "date": "2026-03-01", "time": "10:00",
            "location": "Lagos", "available": True,
        }
        mock_slot_ref = MagicMock()
        mock_slot_ref.get.return_value = slot_doc

        mock_scoped_slots = MagicMock()
        mock_scoped_slots.document.return_value = mock_slot_ref

        mock_booking_ref = MagicMock()
        mock_scoped_bookings = MagicMock()
        mock_scoped_bookings.document.return_value = mock_booking_ref

        mock_batch = MagicMock()
        mock_db.batch.return_value = mock_batch

        ctx = _make_tool_context(_make_state(
            tenant_id="public",
            company_id="ekaette-hotel",
        ))

        def _mock_scoped_or_global(db, tool_ctx, subcollection):
            if subcollection == "booking_slots":
                return mock_scoped_slots
            if subcollection == "bookings":
                return mock_scoped_bookings
            return MagicMock()

        with patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db), \
             patch("app.tools.booking_tools.scoped_collection_or_global", side_effect=_mock_scoped_or_global):
            result = await create_booking(
                slot_id="slot-001",
                user_id="user-1",
                user_name="Test",
                device_name="Phone",
                service_type="trade-in",
                tool_context=ctx,
            )

        assert "confirmation_id" in result
        # Booking data should include tenant_id and company_id
        batch_set_call = mock_batch.set.call_args
        booking_data = batch_set_call[0][1]
        assert booking_data["tenant_id"] == "public"
        assert booking_data["company_id"] == "ekaette-hotel"

    @pytest.mark.asyncio
    async def test_cancel_booking_rejects_cross_company(self):
        """cancel_booking should reject if booking belongs to different company."""
        from app.tools.booking_tools import cancel_booking

        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "confirmation_id": "EKT-ABC123",
            "user_id": "user-1",
            "slot_id": "slot-001",
            "status": "confirmed",
            "company_id": "ekaette-electronics",  # Different company
            "tenant_id": "public",
        }
        mock_doc_ref.get.return_value = mock_doc

        mock_scoped = MagicMock()
        mock_scoped.document.return_value = mock_doc_ref

        ctx = _make_tool_context(_make_state(
            tenant_id="public",
            company_id="ekaette-hotel",  # Caller is hotel, booking is electronics
        ))

        with patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db), \
             patch("app.tools.booking_tools.scoped_collection_or_global", return_value=mock_scoped):
            result = await cancel_booking(
                confirmation_id="EKT-ABC123",
                user_id="user-1",
                tool_context=ctx,
            )

        assert "error" in result


# ═══ 4. Catalog Tools — Company Scoped ═══


class TestCatalogToolsScoped:
    """Catalog tools should use tenant/company-scoped Firestore paths."""

    @pytest.mark.asyncio
    async def test_search_catalog_uses_scoped_collection(self):
        """search_catalog should query scoped catalog_items."""
        from app.tools.catalog_tools import search_catalog

        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter([])

        ctx = _make_tool_context(_make_state(
            tenant_id="public",
            company_id="ekaette-electronics",
        ))

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=MagicMock()), \
             patch("app.tools.catalog_tools.scoped_collection_or_global", return_value=mock_query) as mock_scoped:
            result = await search_catalog(query="iPhone", tool_context=ctx)

        mock_scoped.assert_called_once()
        assert "products" in result

    @pytest.mark.asyncio
    async def test_cross_company_catalog_returns_empty(self):
        """Catalog scoped to company X should not return company Y's products."""
        from app.tools.catalog_tools import search_catalog

        # Scoped collection returns empty (no products for this company)
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.stream.return_value = iter([])

        ctx = _make_tool_context(_make_state(
            tenant_id="other-tenant",
            company_id="other-company",
        ))

        with patch("app.tools.catalog_tools._get_firestore_db", return_value=MagicMock()), \
             patch("app.tools.catalog_tools.scoped_collection_or_global", return_value=mock_query):
            result = await search_catalog(query="iPhone", tool_context=ctx)

        assert result["products"] == []
