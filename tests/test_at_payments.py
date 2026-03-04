"""Tests for Paystack payment routes and analytics linkage."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_payments_app() -> FastAPI:
    from app.api.v1.at.payments import router as payments_router
    from app.api.v1.at.analytics_routes import router as analytics_router

    app = FastAPI()
    app.include_router(payments_router, prefix="/api/v1/at")
    app.include_router(analytics_router, prefix="/api/v1/at")
    return app


@pytest.fixture()
def payments_client():
    from app.api.v1.at import campaign_analytics
    from app.api.v1.at import service_payments

    campaign_analytics.reset_state()
    service_payments.reset_state()
    app = _build_payments_app()
    yield TestClient(app)
    campaign_analytics.reset_state()
    service_payments.reset_state()


class TestPaystackPayments:
    @patch("app.api.v1.at.providers.paystack_initialize_transaction", new_callable=AsyncMock)
    def test_initialize_transaction_success(
        self,
        mock_initialize: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        mock_initialize.return_value = (
            200,
            {
                "status": True,
                "message": "Authorization URL created",
                "data": {
                    "authorization_url": "https://checkout.paystack.com/test",
                    "access_code": "access-123",
                    "reference": "ref-paystack-001",
                },
            },
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/initialize",
                headers={"Idempotency-Key": "paystack-init-001"},
                json={
                    "email": "buyer@example.com",
                    "amountKobo": 150000,
                    "currency": "NGN",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                    "campaignId": "cmp-hardware-01",
                    "customerPhone": "+2348011111111",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["reference"] == "ref-paystack-001"
        assert body["authorization_url"].startswith("https://")
        mock_initialize.assert_awaited_once()

        campaign_resp = payments_client.get("/api/v1/at/analytics/campaigns/cmp-hardware-01")
        campaign = campaign_resp.json()["campaign"]
        assert campaign["payments_initialized_total"] == 1

    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_create_virtual_account_success(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        mock_create_customer.return_value = (
            200,
            {
                "status": True,
                "message": "Customer created",
                "data": {"customer_code": "CUS_123"},
            },
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "message": "Dedicated account created",
                "data": {
                    "id": 999,
                    "account_name": "Ekaette Buyer",
                    "account_number": "1234567890",
                    "bank_name": "Wema Bank",
                    "bank_slug": "wema-bank",
                    "customer": 123,
                },
            },
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "wema-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-001"},
                json={
                    "email": "buyer@example.com",
                    "firstName": "Ada",
                    "lastName": "Ekaette",
                    "phone": "+2348011111111",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                    "campaignId": "cmp-hardware-va-01",
                    "expectedAmountKobo": 120000,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["payment_method"] == "virtual_account"
        assert body["account_number"] == "1234567890"
        assert body["bank_name"] == "Wema Bank"
        assert "instructions" in body

        snapshot = payments_client.get(f"/api/v1/at/payments/paystack/virtual-accounts/{body['reference']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["virtual_account"]["account_number"] == "1234567890"

        campaign_resp = payments_client.get("/api/v1/at/analytics/campaigns/cmp-hardware-va-01")
        campaign = campaign_resp.json()["campaign"]
        assert campaign["payments_initialized_total"] == 1

    @patch("app.api.v1.at.providers.paystack_fetch_dedicated_account_providers", new_callable=AsyncMock)
    def test_virtual_account_providers(
        self,
        mock_fetch_providers: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        mock_fetch_providers.return_value = (
            200,
            {
                "status": True,
                "data": [
                    {"id": 1, "slug": "wema-bank", "bank_name": "Wema Bank"},
                    {"id": 2, "slug": "titan-paystack", "bank_name": "Titan Paystack"},
                ],
            },
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
        ):
            resp = payments_client.get("/api/v1/at/payments/paystack/virtual-accounts/providers")

        assert resp.status_code == 200
        providers = resp.json()["providers"]
        assert len(providers) == 2
        assert providers[0]["slug"] == "wema-bank"

    @patch("app.api.v1.at.providers.paystack_verify_transaction", new_callable=AsyncMock)
    def test_verify_transaction_records_conversion(
        self,
        mock_verify: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        mock_verify.return_value = (
            200,
            {
                "status": True,
                "message": "Verification successful",
                "data": {
                    "reference": "ref-paystack-002",
                    "status": "success",
                    "amount": 250000,
                    "currency": "NGN",
                    "metadata": {
                        "tenant_id": "public",
                        "company_id": "ekaette-electronics",
                        "campaign_id": "cmp-hardware-02",
                    },
                },
            },
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
        ):
            resp = payments_client.get("/api/v1/at/payments/paystack/verify/ref-paystack-002")

        assert resp.status_code == 200
        processed = resp.json()["processed"]
        assert processed["event"] == "charge.success"
        assert processed["campaign_id"] == "cmp-hardware-02"

        campaign_resp = payments_client.get("/api/v1/at/analytics/campaigns/cmp-hardware-02")
        campaign = campaign_resp.json()["campaign"]
        assert campaign["conversions_total"] == 1
        assert campaign["revenue_kobo"] == 250000

    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_webhook_maps_virtual_account_to_campaign(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        mock_create_customer.return_value = (
            200,
            {
                "status": True,
                "data": {"customer_code": "CUS_123"},
            },
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "account_name": "Ekaette Buyer",
                    "account_number": "1234567890",
                    "bank_name": "Wema Bank",
                    "bank_slug": "wema-bank",
                },
            },
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_WEBHOOK_SECRET", "paystack-webhook-secret"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "wema-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
        ):
            create_resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-map-001"},
                json={
                    "email": "buyer@example.com",
                    "firstName": "Ada",
                    "lastName": "Ekaette",
                    "campaignId": "cmp-hardware-va-map-01",
                },
            )
            assert create_resp.status_code == 200
            va_reference = create_resp.json()["reference"]

            payload = {
                "event": "charge.success",
                "data": {
                    "reference": "ref-paystack-va-charge-01",
                    "status": "success",
                    "amount": 99000,
                    "currency": "NGN",
                    "authorization": {
                        "channel": "dedicated_nuban",
                        "receiver_bank_account_number": "1234567890",
                    },
                },
            }
            raw = json.dumps(payload).encode("utf-8")
            signature = hmac.new(b"paystack-webhook-secret", raw, hashlib.sha512).hexdigest()
            webhook_resp = payments_client.post(
                "/api/v1/at/payments/paystack/webhook",
                data=raw,
                headers={
                    "x-paystack-signature": signature,
                    "content-type": "application/json",
                },
            )

        assert webhook_resp.status_code == 200
        processed = webhook_resp.json()["processed"]
        assert processed["payment_method"] == "virtual_account"
        assert processed["virtual_account_reference"] == va_reference

        # Critical for voice flow: checking by original VA reference must reflect success.
        from app.api.v1.at import service_payments

        va_payment = service_payments.payment_snapshot(va_reference)
        assert isinstance(va_payment, dict)
        assert va_payment.get("status") == "success"
        assert va_payment.get("last_gateway_reference") == "ref-paystack-va-charge-01"

        campaign_resp = payments_client.get("/api/v1/at/analytics/campaigns/cmp-hardware-va-map-01")
        campaign = campaign_resp.json()["campaign"]
        assert campaign["conversions_total"] == 1
        assert campaign["revenue_kobo"] == 99000

    def test_webhook_signature_and_processing(self, payments_client: TestClient) -> None:
        payload = {
            "event": "charge.success",
            "data": {
                "reference": "ref-paystack-003",
                "status": "success",
                "amount": 99000,
                "currency": "NGN",
                "metadata": {
                    "tenant_id": "public",
                    "company_id": "ekaette-electronics",
                    "campaign_id": "cmp-hardware-03",
                },
            },
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = hmac.new(b"paystack-webhook-secret", raw, hashlib.sha512).hexdigest()

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_WEBHOOK_SECRET", "paystack-webhook-secret"),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/webhook",
                data=raw,
                headers={
                    "x-paystack-signature": signature,
                    "content-type": "application/json",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["processed"]["event"] == "charge.success"

        campaign_resp = payments_client.get("/api/v1/at/analytics/campaigns/cmp-hardware-03")
        campaign = campaign_resp.json()["campaign"]
        assert campaign["conversions_total"] == 1
        assert campaign["revenue_kobo"] == 99000

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_virtual_account_sends_sms_notification(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        mock_send_sms: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        """Creating a virtual account with a phone number fires an SMS with account details."""
        mock_create_customer.return_value = (
            200,
            {"status": True, "data": {"customer_code": "CUS_sms_test"}},
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "account_name": "Ada Ekaette",
                    "account_number": "9876543210",
                    "bank_name": "Wema Bank",
                    "bank_slug": "wema-bank",
                },
            },
        )
        mock_send_sms.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "wema-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
            patch("app.api.v1.at.settings.AT_SMS_ENABLED", True),
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-sms-001"},
                json={
                    "email": "ada@example.com",
                    "firstName": "Ada",
                    "lastName": "Ekaette",
                    "phone": "+2348099999999",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                    "expectedAmountKobo": 50000,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["sms_sent"] is True

        # Verify SMS was sent with account details in the message
        mock_send_sms.assert_awaited_once()
        sms_call = mock_send_sms.call_args
        sms_message = sms_call.kwargs.get("message") or sms_call[1].get("message", "")
        assert "9876543210" in sms_message
        assert "Wema Bank" in sms_message
        sms_recipients = sms_call.kwargs.get("recipients") or sms_call[1].get("recipients", [])
        assert "+2348099999999" in sms_recipients

    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_virtual_account_sms_failure_does_not_break_creation(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        mock_send_sms: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        """SMS failure is non-blocking — account creation still succeeds."""
        mock_create_customer.return_value = (
            200,
            {"status": True, "data": {"customer_code": "CUS_sms_fail"}},
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "account_name": "Ada Ekaette",
                    "account_number": "1111111111",
                    "bank_name": "Test Bank",
                    "bank_slug": "test-bank",
                },
            },
        )
        mock_send_sms.side_effect = Exception("AT SDK down")

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "test-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
            patch("app.api.v1.at.settings.AT_SMS_ENABLED", True),
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-sms-fail-001"},
                json={
                    "email": "ada@example.com",
                    "firstName": "Ada",
                    "lastName": "Ekaette",
                    "phone": "+2348099999999",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["account_number"] == "1111111111"
        assert body["sms_sent"] is False

    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_virtual_account_no_sms_when_no_phone(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        """No SMS sent when phone number is missing."""
        mock_create_customer.return_value = (
            200,
            {"status": True, "data": {"customer_code": "CUS_no_phone"}},
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "account_name": "No Phone",
                    "account_number": "2222222222",
                    "bank_name": "Test Bank",
                    "bank_slug": "test-bank",
                },
            },
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "test-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
            patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock) as mock_send_sms,
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-no-phone-001"},
                json={
                    "email": "nophone@example.com",
                    "firstName": "No",
                    "lastName": "Phone",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body.get("sms_sent") is False
        mock_send_sms.assert_not_awaited()

    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.send_sms", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_virtual_account_sends_whatsapp_notification(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        mock_send_sms: AsyncMock,
        mock_whatsapp_send: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        """Creating a virtual account with WhatsApp enabled sends both SMS and WhatsApp."""
        mock_create_customer.return_value = (
            200,
            {"status": True, "data": {"customer_code": "CUS_wa_test"}},
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "account_name": "Ada Ekaette",
                    "account_number": "5555555555",
                    "bank_name": "Wema Bank",
                    "bank_slug": "wema-bank",
                },
            },
        )
        mock_send_sms.return_value = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}
        mock_whatsapp_send.return_value = (
            200,
            {"messages": [{"id": "wamid.abc123"}]},
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "wema-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
            patch("app.api.v1.at.settings.AT_SMS_ENABLED", True),
            patch("app.api.v1.at.service_payments.WHATSAPP_ENABLED", True),
            patch("app.api.v1.at.service_payments.WHATSAPP_ACCESS_TOKEN", "wa_test_token"),
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-wa-001"},
                json={
                    "email": "ada@example.com",
                    "firstName": "Ada",
                    "lastName": "Ekaette",
                    "phone": "+2348099999999",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                    "expectedAmountKobo": 75000,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["sms_sent"] is True
        assert body["whatsapp_sent"] is True

        # Verify WhatsApp was called with correct params
        mock_whatsapp_send.assert_awaited_once()
        wa_call = mock_whatsapp_send.call_args
        assert wa_call.kwargs["to"] == "+2348099999999"
        assert "5555555555" in wa_call.kwargs["body"]
        assert "Wema Bank" in wa_call.kwargs["body"]

    @patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_whatsapp_failure_does_not_break_creation(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        mock_whatsapp_send: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        """WhatsApp API error is non-blocking — account creation still succeeds."""
        mock_create_customer.return_value = (
            200,
            {"status": True, "data": {"customer_code": "CUS_wa_fail"}},
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "account_name": "Ada Ekaette",
                    "account_number": "6666666666",
                    "bank_name": "Test Bank",
                    "bank_slug": "test-bank",
                },
            },
        )
        mock_whatsapp_send.return_value = (
            401,
            {"error": {"message": "Invalid OAuth access token", "type": "OAuthException"}},
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "test-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
            patch("app.api.v1.at.service_payments.WHATSAPP_ENABLED", True),
            patch("app.api.v1.at.service_payments.WHATSAPP_ACCESS_TOKEN", "bad_token"),
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-wa-fail-001"},
                json={
                    "email": "ada@example.com",
                    "firstName": "Ada",
                    "lastName": "Ekaette",
                    "phone": "+2348099999999",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["account_number"] == "6666666666"
        assert body["whatsapp_sent"] is False

    @patch("app.api.v1.at.providers.paystack_create_dedicated_account", new_callable=AsyncMock)
    @patch("app.api.v1.at.providers.paystack_create_customer", new_callable=AsyncMock)
    def test_whatsapp_skipped_when_disabled(
        self,
        mock_create_customer: AsyncMock,
        mock_create_dedicated_account: AsyncMock,
        payments_client: TestClient,
    ) -> None:
        """WhatsApp notification skipped when WHATSAPP_ENABLED=false."""
        mock_create_customer.return_value = (
            200,
            {"status": True, "data": {"customer_code": "CUS_wa_off"}},
        )
        mock_create_dedicated_account.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "account_name": "Test User",
                    "account_number": "7777777777",
                    "bank_name": "Test Bank",
                    "bank_slug": "test-bank",
                },
            },
        )

        with (
            patch("app.api.v1.at.service_payments.PAYSTACK_ENABLED", True),
            patch("app.api.v1.at.service_payments.PAYSTACK_SECRET_KEY", "sk_test_abc"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_BANK_SLUG", "test-bank"),
            patch("app.api.v1.at.service_payments.PAYSTACK_DEFAULT_DVA_COUNTRY", "NG"),
            patch("app.api.v1.at.service_payments.WHATSAPP_ENABLED", False),
            patch("app.api.v1.at.providers.whatsapp_send_text", new_callable=AsyncMock) as mock_wa,
        ):
            resp = payments_client.post(
                "/api/v1/at/payments/paystack/virtual-accounts",
                headers={"Idempotency-Key": "paystack-va-wa-off-001"},
                json={
                    "email": "test@example.com",
                    "firstName": "Test",
                    "lastName": "User",
                    "phone": "+2348099999999",
                    "tenantId": "public",
                    "companyId": "ekaette-electronics",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["whatsapp_sent"] is False
        mock_wa.assert_not_awaited()

    def test_webhook_rejects_invalid_signature(self, payments_client: TestClient) -> None:
        payload = {"event": "charge.success", "data": {"reference": "ref-paystack-004"}}
        resp = payments_client.post(
            "/api/v1/at/payments/paystack/webhook",
            data=json.dumps(payload).encode("utf-8"),
            headers={"x-paystack-signature": "invalid", "content-type": "application/json"},
        )
        assert resp.status_code == 401
