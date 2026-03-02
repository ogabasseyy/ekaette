"""Tests for AT contacts endpoint — list known recipients from campaign analytics."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_contacts_app() -> FastAPI:
    from app.api.v1.at.sms import router as sms_router
    from app.api.v1.at.analytics_routes import router as analytics_router

    app = FastAPI()
    app.include_router(sms_router, prefix="/api/v1/at")
    app.include_router(analytics_router, prefix="/api/v1/at")
    return app


@pytest.fixture()
def contacts_client():
    from app.api.v1.at import campaign_analytics

    with (
        patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
        patch("app.api.v1.at.sms.AT_SMS_ENABLED", True),
    ):
        import app.api.v1.at.security as sec_mod

        sec_mod._at_rate_buckets.clear()
        sec_mod._at_last_prune = 0.0
        campaign_analytics.reset_state()
        app = _build_contacts_app()
        yield TestClient(app)
        campaign_analytics.reset_state()


class TestContacts:
    def test_empty_contacts_when_no_campaigns(self, contacts_client: TestClient) -> None:
        resp = contacts_client.get(
            "/api/v1/at/analytics/contacts",
            params={"tenantId": "public", "companyId": "ekaette-electronics"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["contacts"] == []
        assert body["count"] == 0

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_contacts_appear_after_sms_campaign(
        self,
        mock_send: AsyncMock,
        contacts_client: TestClient,
    ) -> None:
        mock_send.return_value = {
            "SMSMessageData": {
                "Recipients": [
                    {"number": "+2348011111111", "status": "Success"},
                ]
            }
        }

        contacts_client.post(
            "/api/v1/at/sms/campaign",
            json={
                "to": ["+2348011111111"],
                "message": "Follow-up promo",
                "tenant_id": "public",
                "company_id": "ekaette-electronics",
                "campaign_name": "Follow-up",
            },
        )

        resp = contacts_client.get(
            "/api/v1/at/analytics/contacts",
            params={"tenantId": "public", "companyId": "ekaette-electronics"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        contact = body["contacts"][0]
        assert contact["phone"] == "+2348011111111"
        assert contact["channel"] == "sms"
        assert contact["last_campaign_name"] == "Follow-up"

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_contacts_scoped_by_tenant_company(
        self,
        mock_send: AsyncMock,
        contacts_client: TestClient,
    ) -> None:
        mock_send.return_value = {
            "SMSMessageData": {
                "Recipients": [{"number": "+2348099999999", "status": "Success"}]
            }
        }

        # Send to different company
        contacts_client.post(
            "/api/v1/at/sms/campaign",
            json={
                "to": ["+2348099999999"],
                "message": "Other company",
                "tenant_id": "public",
                "company_id": "other-company",
                "campaign_name": "Other",
            },
        )

        # Query for ekaette-electronics — should be empty
        resp = contacts_client.get(
            "/api/v1/at/analytics/contacts",
            params={"tenantId": "public", "companyId": "ekaette-electronics"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

        # Query for other-company — should have one
        resp2 = contacts_client.get(
            "/api/v1/at/analytics/contacts",
            params={"tenantId": "public", "companyId": "other-company"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["count"] == 1

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_contacts_deduplicated_across_campaigns(
        self,
        mock_send: AsyncMock,
        contacts_client: TestClient,
    ) -> None:
        mock_send.return_value = {
            "SMSMessageData": {
                "Recipients": [{"number": "+2348011111111", "status": "Success"}]
            }
        }

        for name in ("Campaign A", "Campaign B"):
            contacts_client.post(
                "/api/v1/at/sms/campaign",
                json={
                    "to": ["+2348011111111"],
                    "message": f"Msg from {name}",
                    "tenant_id": "public",
                    "company_id": "ekaette-electronics",
                    "campaign_name": name,
                },
            )

        resp = contacts_client.get(
            "/api/v1/at/analytics/contacts",
            params={"tenantId": "public", "companyId": "ekaette-electronics"},
        )
        body = resp.json()
        assert body["count"] == 1
        # Last campaign should be "Campaign B"
        assert body["contacts"][0]["last_campaign_name"] == "Campaign B"

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    def test_contacts_include_channel_info(
        self,
        mock_send: AsyncMock,
        contacts_client: TestClient,
    ) -> None:
        mock_send.return_value = {
            "SMSMessageData": {
                "Recipients": [
                    {"number": "+2348011111111", "status": "Success"},
                    {"number": "+2348022222222", "status": "Success"},
                ]
            }
        }

        contacts_client.post(
            "/api/v1/at/sms/campaign",
            json={
                "to": ["+2348011111111", "+2348022222222"],
                "message": "Multi-contact SMS",
                "tenant_id": "public",
                "company_id": "ekaette-electronics",
                "campaign_name": "Multi",
            },
        )

        resp = contacts_client.get(
            "/api/v1/at/analytics/contacts",
            params={"tenantId": "public", "companyId": "ekaette-electronics"},
        )
        body = resp.json()
        assert body["count"] == 2
        for contact in body["contacts"]:
            assert "phone" in contact
            assert "channel" in contact
            assert "last_campaign_id" in contact
            assert "last_campaign_name" in contact
