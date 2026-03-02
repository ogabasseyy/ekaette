"""Tests for order persistence, tracking, and review follow-up tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _tool_context() -> SimpleNamespace:
    return SimpleNamespace(
        state={
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        }
    )


class TestOrderTrackingTools:
    @pytest.mark.asyncio
    async def test_order_is_saved_before_tracking_lookup(self) -> None:
        from app.tools import shipping_tools

        shipping_tools.reset_shipping_state()

        with patch.object(shipping_tools, "_get_firestore_db", return_value=None):
            created = await shipping_tools.create_order_record(
                customer_name="Ada Buyer",
                customer_phone="+2348011111111",
                items_summary="10 bags of cement",
                amount_kobo=950000,
                receiver_city="Lagos",
                tool_context=_tool_context(),
            )
            tracked = await shipping_tools.track_order_delivery(
                order_id=created["order_id"],
                refresh_from_provider=False,
                tool_context=_tool_context(),
            )

        assert created["status"] == "ok"
        assert created["order"]["order_id"] == created["order_id"]
        assert tracked["status"] == "ok"
        assert tracked["order"]["order_id"] == created["order_id"]
        assert tracked["tracking"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_track_missing_order_returns_not_found(self) -> None:
        from app.tools import shipping_tools

        shipping_tools.reset_shipping_state()

        with patch.object(shipping_tools, "_get_firestore_db", return_value=None):
            result = await shipping_tools.track_order_delivery(
                order_id="EKT-ORD-DOES-NOT-EXIST",
                refresh_from_provider=False,
                tool_context=_tool_context(),
            )

        assert result["status"] == "error"
        assert result["code"] == "ORDER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_delivered_status_triggers_review_followup(self) -> None:
        from app.tools import shipping_tools

        shipping_tools.reset_shipping_state()

        with (
            patch.object(shipping_tools, "_get_firestore_db", return_value=None),
            patch.object(shipping_tools, "AT_SMS_ENABLED", True),
            patch.object(shipping_tools, "WHATSAPP_ENABLED", False),
            patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock) as mock_send_sms,
        ):
            mock_send_sms.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}

            created = await shipping_tools.create_order_record(
                customer_name="Ada Buyer",
                customer_phone="+2348099999999",
                items_summary="Wire and conduit",
                amount_kobo=320000,
                receiver_city="Lagos",
                tool_context=_tool_context(),
            )
            updated = await shipping_tools.update_order_tracking_status(
                order_id=created["order_id"],
                tracking_status="Delivered",
                event_description="Package delivered to front desk",
                tool_context=_tool_context(),
            )

        assert updated["status"] == "ok"
        assert updated["tracking"]["status"] == "delivered"
        assert updated["review_followup"]["requested"] is True
        mock_send_sms.assert_awaited_once()
