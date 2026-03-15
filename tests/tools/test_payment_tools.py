"""Tests for payment ADK tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class TestCreateVirtualAccountPaymentTool:
    @pytest.mark.asyncio
    async def test_defaults_customer_phone_from_session_caller_phone(self, monkeypatch):
        monkeypatch.delenv("AT_SMS_SENDER_ID", raising=False)
        from app.tools.payment_tools import create_virtual_account_payment

        ctx = SimpleNamespace(
            state={
                "user:caller_phone": "+2348012345678",
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
                "app:company_name": "Awgabassey Gadgets",
            }
        )

        with (
            patch("app.tools.payment_tools.service_payments.create_virtual_account", new_callable=AsyncMock) as mock_create,
            patch("app.tools.payment_tools.service_payments.send_va_notification_sms", new_callable=AsyncMock) as mock_sms,
            patch("app.tools.payment_tools.service_payments.send_va_notification_whatsapp", new_callable=AsyncMock) as mock_wa,
        ):
            mock_create.return_value = {
                "reference": "ref-123",
                "account_number": "1234567890",
                "account_name": "Ada Buyer",
                "bank_name": "Wema Bank",
                "bank_slug": "wema-bank",
            }
            mock_sms.return_value = True
            mock_wa.return_value = True

            result = await create_virtual_account_payment(
                customer_email="ada@example.com",
                customer_first_name="Ada",
                customer_last_name="Buyer",
                expected_amount_kobo=52000000,
                tool_context=ctx,
            )

        assert result["status"] == "ok"
        assert result["notification_phone"] == "+2348012345678"
        assert result["sms_sender_id"] == "Awgabassey"
        assert result["notifications_attempted"] is False
        assert result["sms_sent"] is False
        assert result["whatsapp_sent"] is False
        assert mock_create.await_args.kwargs["phone"] == "+2348012345678"


class TestCheckPaymentStatusDemoMode:
    """check_payment_status returns simulated success when Paystack test keys are active."""

    @pytest.mark.asyncio
    async def test_returns_simulated_success_with_test_key(self):
        from app.tools.payment_tools import check_payment_status

        ctx = SimpleNamespace(
            state={
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
            }
        )
        with patch("app.tools.payment_tools._is_paystack_test_mode", return_value=True):
            result = await check_payment_status("ref-demo-001", tool_context=ctx)
        assert result["status"] == "ok"
        assert result["source"] == "demo"
        assert result["payment"]["status"] == "success"
        assert result["reference"] == "ref-demo-001"

    @pytest.mark.asyncio
    async def test_live_key_does_not_trigger_demo_mode(self):
        from app.tools.payment_tools import check_payment_status

        ctx = SimpleNamespace(
            state={
                "app:tenant_id": "public",
                "app:company_id": "ekaette-electronics",
            }
        )
        with (
            patch("app.tools.payment_tools._is_paystack_test_mode", return_value=False),
            patch("app.tools.payment_tools.service_payments.verify_transaction", new_callable=AsyncMock) as mock_verify,
        ):
            mock_verify.return_value = {"payment": {"status": "success", "tenant_id": "public", "company_id": "ekaette-electronics"}, "processed": True}
            result = await check_payment_status("ref-live-001", tool_context=ctx)
        assert result["source"] == "gateway"
        assert result["status"] == "ok"
        assert result["payment"]["status"] == "success"
        assert result["reference"] == "ref-live-001"
