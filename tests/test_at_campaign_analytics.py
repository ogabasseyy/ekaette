"""Tests for AT campaign analytics endpoints and SMS instrumentation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_analytics_app() -> FastAPI:
    from app.api.v1.at.sms import router as sms_router
    from app.api.v1.at.analytics_routes import router as analytics_router

    app = FastAPI()
    app.include_router(sms_router, prefix="/api/v1/at")
    app.include_router(analytics_router, prefix="/api/v1/at")
    return app


@pytest.fixture()
def analytics_client():
    from app.api.v1.at import campaign_analytics

    with (
        patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
        patch("app.api.v1.at.sms.AT_SMS_ENABLED", True),
    ):
        import app.api.v1.at.security as sec_mod

        sec_mod._at_rate_buckets.clear()
        sec_mod._at_last_prune = 0.0
        campaign_analytics.reset_state()
        app = _build_analytics_app()
        yield TestClient(app)
        campaign_analytics.reset_state()


class TestCampaignAnalytics:
    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_sms_campaign_updates_analytics(
        self,
        mock_send: AsyncMock,
        analytics_client: TestClient,
    ) -> None:
        mock_send.return_value = {
            "SMSMessageData": {
                "Recipients": [
                    {"number": "+2348011111111", "status": "Success"},
                    {"number": "+2348022222222", "status": "Failed: Invalid number"},
                ]
            }
        }

        resp = analytics_client.post(
            "/api/v1/at/sms/campaign",
            json={
                "to": ["+2348011111111", "+2348022222222"],
                "message": "Weekend promo: 5% off",
                "tenant_id": "public",
                "company_id": "ekaette-electronics",
                "campaign_name": "Weekend Promo",
            },
        )
        assert resp.status_code == 200
        campaign_id = resp.json().get("campaign_id")
        assert isinstance(campaign_id, str) and campaign_id

        overview = analytics_client.get(
            "/api/v1/at/analytics/overview",
            params={"tenantId": "public", "companyId": "ekaette-electronics"},
        )
        assert overview.status_code == 200
        summary = overview.json()["summary"]
        assert summary["campaigns_total"] == 1
        assert summary["total_sent"] == 2
        assert summary["total_delivered"] == 1
        assert summary["total_failed"] == 1

        campaign_resp = analytics_client.get(f"/api/v1/at/analytics/campaigns/{campaign_id}")
        campaign = campaign_resp.json()["campaign"]
        assert campaign["campaign_name"] == "Weekend Promo"
        assert campaign["sent_total"] == 2
        assert campaign["delivered_total"] == 1
        assert campaign["failed_total"] == 1

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    @patch("app.api.v1.at.bridge_text.query_text", new_callable=AsyncMock)
    def test_sms_callback_increments_reply_metric(
        self,
        mock_query: AsyncMock,
        mock_send: AsyncMock,
        analytics_client: TestClient,
    ) -> None:
        mock_send.return_value = {
            "SMSMessageData": {"Recipients": [{"number": "+2348011111111", "status": "Success"}]}
        }

        send_resp = analytics_client.post(
            "/api/v1/at/sms/campaign",
            json={
                "to": ["+2348011111111"],
                "message": "Reply YES to confirm stock",
                "campaign_id": "cmp-hackathon-001",
                "campaign_name": "Stock Follow-up",
            },
        )
        assert send_resp.status_code == 200

        mock_query.return_value = "Thanks for confirming."
        callback_resp = analytics_client.post(
            "/api/v1/at/sms/callback",
            data={
                "from": "+2348011111111",
                "to": "+23417006000",
                "text": "YES",
            },
        )
        assert callback_resp.status_code == 200
        assert callback_resp.json().get("campaign_id") == "cmp-hackathon-001"

        campaign_resp = analytics_client.get("/api/v1/at/analytics/campaigns/cmp-hackathon-001")
        assert campaign_resp.status_code == 200
        campaign = campaign_resp.json()["campaign"]
        assert campaign["replies_total"] == 1

    def test_manual_conversion_event_tracks_revenue(self, analytics_client: TestClient) -> None:
        event_resp = analytics_client.post(
            "/api/v1/at/analytics/events",
            json={
                "event_type": "conversion",
                "channel": "omni",
                "tenant_id": "public",
                "company_id": "ekaette-electronics",
                "campaign_id": "cmp-hackathon-payments",
                "amount_kobo": 250000,
                "reference": "ref-conv-001",
            },
        )
        assert event_resp.status_code == 200

        campaign_resp = analytics_client.get("/api/v1/at/analytics/campaigns/cmp-hackathon-payments")
        campaign = campaign_resp.json()["campaign"]
        assert campaign["conversions_total"] == 1
        assert campaign["revenue_kobo"] == 250000
