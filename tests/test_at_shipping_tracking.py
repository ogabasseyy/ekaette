"""Tests for order tracking + review follow-up API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_shipping_app() -> FastAPI:
    from app.api.v1.at.shipping import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/at")
    return app


class TestShippingOrderTrackingEndpoints:
    def test_create_order_then_track(self) -> None:
        from app.tools import shipping_tools

        shipping_tools.reset_shipping_state()

        with patch("app.tools.shipping_tools._get_firestore_db", return_value=None):
            client = TestClient(_build_shipping_app())
            create_resp = client.post(
                "/api/v1/at/shipping/orders",
                json={
                    "customerName": "Ada Buyer",
                    "customerPhone": "+2348011111111",
                    "itemsSummary": "PVC pipe set",
                    "amountKobo": 120000,
                    "receiverCity": "Lagos",
                },
            )

            assert create_resp.status_code == 200
            order_id = create_resp.json()["order_id"]

            track_resp = client.get(
                f"/api/v1/at/shipping/orders/{order_id}/tracking",
                params={"refreshProvider": "false"},
            )

        assert track_resp.status_code == 200
        body = track_resp.json()
        assert body["status"] == "ok"
        assert body["order_id"] == order_id
        assert body["tracking"]["status"] == "pending"

    def test_tracking_missing_order_returns_404(self) -> None:
        from app.tools import shipping_tools

        shipping_tools.reset_shipping_state()

        with patch("app.tools.shipping_tools._get_firestore_db", return_value=None):
            client = TestClient(_build_shipping_app())
            resp = client.get(
                "/api/v1/at/shipping/orders/EKT-ORD-NOTFOUND/tracking",
                params={"refreshProvider": "false"},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "ORDER_NOT_FOUND"

    def test_review_followup_endpoint(self) -> None:
        from app.tools import shipping_tools

        shipping_tools.reset_shipping_state()

        with (
            patch("app.tools.shipping_tools._get_firestore_db", return_value=None),
            patch("app.tools.shipping_tools.AT_SMS_ENABLED", True),
            patch("app.tools.shipping_tools.WHATSAPP_ENABLED", False),
            patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock) as mock_send_sms,
        ):
            mock_send_sms.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}

            client = TestClient(_build_shipping_app())
            create_resp = client.post(
                "/api/v1/at/shipping/orders",
                json={
                    "customerName": "Ada Buyer",
                    "customerPhone": "+2348099999999",
                    "itemsSummary": "Tile adhesives",
                    "amountKobo": 89000,
                    "receiverCity": "Lagos",
                },
            )
            order_id = create_resp.json()["order_id"]

            followup_resp = client.post(f"/api/v1/at/shipping/orders/{order_id}/review-followup")

        assert followup_resp.status_code == 200
        body = followup_resp.json()
        assert body["status"] == "ok"
        assert body["review_followup"]["requested"] is True
        mock_send_sms.assert_awaited_once()
