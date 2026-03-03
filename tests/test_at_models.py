"""TDD tests for AT Pydantic v2 request/response models.

Red phase — write tests before implementation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestOutboundCallRequest:
    """Outbound call request model validation."""

    def test_valid_call_request(self) -> None:
        from app.api.v1.at.models import OutboundCallRequest

        req = OutboundCallRequest(to="+2348012345678")
        assert req.to == "+2348012345678"
        assert req.tenant_id == "public"
        assert req.company_id == "ekaette-electronics"

    def test_call_request_custom_tenant(self) -> None:
        from app.api.v1.at.models import OutboundCallRequest

        req = OutboundCallRequest(
            to="+2348012345678",
            tenant_id="acme",
            company_id="acme-hotel",
        )
        assert req.tenant_id == "acme"
        assert req.company_id == "acme-hotel"

    def test_call_request_requires_to(self) -> None:
        from app.api.v1.at.models import OutboundCallRequest

        with pytest.raises(ValidationError):
            OutboundCallRequest()  # type: ignore[call-arg]


class TestTransferRequest:
    """Transfer request model validation."""

    def test_valid_transfer(self) -> None:
        from app.api.v1.at.models import TransferRequest

        req = TransferRequest(session_id="AT-123", transfer_to="+2348012345678")
        assert req.session_id == "AT-123"
        assert req.transfer_to == "+2348012345678"

    def test_transfer_requires_session_id(self) -> None:
        from app.api.v1.at.models import TransferRequest

        with pytest.raises(ValidationError):
            TransferRequest(transfer_to="+2348012345678")  # type: ignore[call-arg]

    def test_transfer_requires_transfer_to(self) -> None:
        from app.api.v1.at.models import TransferRequest

        with pytest.raises(ValidationError):
            TransferRequest(session_id="AT-123")  # type: ignore[call-arg]


class TestCampaignCallRequest:
    """Campaign call request model validation."""

    def test_valid_campaign(self) -> None:
        from app.api.v1.at.models import CampaignCallRequest

        req = CampaignCallRequest(
            to=["+2348012345678", "+2348098765432"],
            message="Your order is ready.",
        )
        assert len(req.to) == 2
        assert req.message == "Your order is ready."

    def test_campaign_requires_recipients(self) -> None:
        from app.api.v1.at.models import CampaignCallRequest

        with pytest.raises(ValidationError):
            CampaignCallRequest(message="Hello")  # type: ignore[call-arg]


class TestSendSMSRequest:
    """SMS send request model validation."""

    def test_valid_sms(self) -> None:
        from app.api.v1.at.models import SendSMSRequest

        req = SendSMSRequest(to="+2348012345678", message="Hello from Ekaette")
        assert req.to == "+2348012345678"
        assert req.message == "Hello from Ekaette"

    def test_sms_requires_message(self) -> None:
        from app.api.v1.at.models import SendSMSRequest

        with pytest.raises(ValidationError):
            SendSMSRequest(to="+2348012345678")  # type: ignore[call-arg]


class TestCampaignSMSRequest:
    """Bulk SMS campaign request model validation."""

    def test_valid_campaign_sms(self) -> None:
        from app.api.v1.at.models import CampaignSMSRequest

        req = CampaignSMSRequest(
            to=["+2348012345678", "+2348098765432"],
            message="Flash sale!",
        )
        assert len(req.to) == 2
        assert req.message == "Flash sale!"

    def test_campaign_sms_requires_both_fields(self) -> None:
        from app.api.v1.at.models import CampaignSMSRequest

        with pytest.raises(ValidationError):
            CampaignSMSRequest(to=["+2348012345678"])  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            CampaignSMSRequest(message="Hello")  # type: ignore[call-arg]
