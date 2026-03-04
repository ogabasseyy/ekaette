"""Tests for Topship shipping quote API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_shipping_app() -> FastAPI:
    from app.api.v1.at.shipping import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/at")
    return app


class TestTopshipShippingEndpoint:
    @patch("app.tools.shipping_tools._fetch_topship_rates", new_callable=AsyncMock)
    def test_post_quote_success(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (
            200,
            {
                "status": True,
                "data": [
                    {
                        "serviceType": "Standard",
                        "pricingTier": "Budget",
                        "total": 220000,
                        "currency": "NGN",
                        "deliveryEta": "3-5 days",
                    },
                    {
                        "serviceType": "Express",
                        "pricingTier": "Express",
                        "total": 360000,
                        "currency": "NGN",
                        "deliveryEta": "1-2 days",
                    },
                ],
            },
        )

        with patch("app.tools.shipping_tools.TOPSHIP_API_KEY", "topship_test_key"):
            client = TestClient(_build_shipping_app())
            resp = client.post(
                "/api/v1/at/shipping/topship/quote",
                json={
                    "senderCity": "Lagos",
                    "receiverCity": "Abuja",
                    "weightKg": 1.5,
                    "prefer": "cheapest",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "provider" not in body
        assert body["recommended"]["total_kobo"] == 220000
        assert len(body["quotes"]) == 2

    def test_post_quote_not_configured(self) -> None:
        with patch("app.tools.shipping_tools.TOPSHIP_API_KEY", ""):
            client = TestClient(_build_shipping_app())
            resp = client.post(
                "/api/v1/at/shipping/topship/quote",
                json={
                    "senderCity": "Lagos",
                    "receiverCity": "Abuja",
                    "weightKg": 1.0,
                },
            )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "TOPSHIP_NOT_CONFIGURED"

    @patch("app.tools.shipping_tools._fetch_topship_rates", new_callable=AsyncMock)
    def test_post_quote_no_quotes(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (200, {"status": True, "data": []})

        with patch("app.tools.shipping_tools.TOPSHIP_API_KEY", "topship_test_key"):
            client = TestClient(_build_shipping_app())
            resp = client.post(
                "/api/v1/at/shipping/topship/quote",
                json={
                    "senderCity": "Lagos",
                    "receiverCity": "Jos",
                },
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "TOPSHIP_NO_QUOTES"

    @patch("app.tools.shipping_tools._fetch_topship_rates", new_callable=AsyncMock)
    def test_get_quote_success(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (
            200,
            [
                {
                    "mode": "Economy",
                    "pricingTier": "Budget",
                    "cost": 180000,
                    "currency": "NGN",
                    "duration": "4-6 days",
                }
            ],
        )

        with patch("app.tools.shipping_tools.TOPSHIP_API_KEY", "topship_test_key"):
            client = TestClient(_build_shipping_app())
            resp = client.get(
                "/api/v1/at/shipping/topship/quote",
                params={
                    "senderCity": "Lagos",
                    "receiverCity": "Ibadan",
                    "weightKg": 1.2,
                    "prefer": "fastest",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["recommended"]["pricing_tier"] == "Budget"
        assert body["recommended"]["total_kobo"] == 180000
