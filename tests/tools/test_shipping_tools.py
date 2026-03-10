"""Tests for Topship shipping quote tool."""

from unittest.mock import AsyncMock, patch

import pytest


class TestTopshipDeliveryQuote:
    @pytest.mark.asyncio
    async def test_returns_not_configured_without_api_key(self):
        from app.tools import shipping_tools

        with patch.object(shipping_tools, "TOPSHIP_API_KEY", ""):
            result = await shipping_tools.get_topship_delivery_quote(
                sender_city="Lagos",
                receiver_city="Abuja",
            )

        assert result["code"] == "TOPSHIP_NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_parses_array_response_quotes(self):
        from app.tools import shipping_tools

        payload = [
            {
                "mode": "Standard",
                "pricingTier": "Budget",
                "cost": 250000,
                "currency": "NGN",
                "duration": "3-5 days",
            },
            {
                "mode": "Express",
                "pricingTier": "Express",
                "cost": 420000,
                "currency": "NGN",
                "duration": "2 days",
            },
        ]

        with (
            patch.object(shipping_tools, "TOPSHIP_API_KEY", "topship_test_key"),
            patch.object(shipping_tools, "_fetch_topship_rates", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = (200, payload)
            result = await shipping_tools.get_topship_delivery_quote(
                sender_city="Lagos",
                receiver_city="Ibadan",
                weight_kg=1.5,
            )

        assert result["status"] == "ok"
        assert result["provider"] == "topship"
        assert len(result["quotes"]) == 2
        assert result["cheapest"]["total_kobo"] == 250000
        assert result["fastest"]["estimated_days"] == 2
        assert result["cheapest"]["currency_name"] == "naira"
        assert result["cheapest"]["total_display"] == "2,500.00 naira"

    @pytest.mark.asyncio
    async def test_parses_wrapped_response_and_prefers_fastest(self):
        from app.tools import shipping_tools

        payload = {
            "status": True,
            "data": [
                {
                    "serviceType": "Economy",
                    "pricingTier": "Budget",
                    "total": 200000,
                    "currency": "NGN",
                    "deliveryEta": "4-6 days",
                },
                {
                    "serviceType": "Priority",
                    "pricingTier": "Premium",
                    "total": 360000,
                    "currency": "NGN",
                    "deliveryEta": "1-2 days",
                },
            ],
        }

        with (
            patch.object(shipping_tools, "TOPSHIP_API_KEY", "topship_test_key"),
            patch.object(shipping_tools, "_fetch_topship_rates", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = (200, payload)
            result = await shipping_tools.get_topship_delivery_quote(
                sender_city="Lagos",
                receiver_city="Port Harcourt",
                prefer="fastest",
            )

        assert result["status"] == "ok"
        assert result["recommended"]["pricing_tier"] == "Premium"
        assert result["recommended"]["estimated_days"] <= result["cheapest"]["estimated_days"]
        assert result["recommended"]["total_display"].endswith("naira")

    @pytest.mark.asyncio
    async def test_returns_api_error_on_non_2xx(self):
        from app.tools import shipping_tools

        with (
            patch.object(shipping_tools, "TOPSHIP_API_KEY", "topship_test_key"),
            patch.object(shipping_tools, "_fetch_topship_rates", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = (500, {"message": "error"})
            result = await shipping_tools.get_topship_delivery_quote(
                sender_city="Lagos",
                receiver_city="Kano",
            )

        assert result["code"] == "TOPSHIP_API_ERROR"

    @pytest.mark.asyncio
    async def test_returns_no_quotes_when_payload_empty(self):
        from app.tools import shipping_tools

        with (
            patch.object(shipping_tools, "TOPSHIP_API_KEY", "topship_test_key"),
            patch.object(shipping_tools, "_fetch_topship_rates", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = (200, {"status": True, "data": []})
            result = await shipping_tools.get_topship_delivery_quote(
                sender_city="Lagos",
                receiver_city="Jos",
            )

        assert result["code"] == "TOPSHIP_NO_QUOTES"
