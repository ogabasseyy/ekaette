"""Pydantic v2 request/response models for AT voice and SMS channels."""

from __future__ import annotations

from pydantic import BaseModel


class OutboundCallRequest(BaseModel):
    """Initiate an outbound voice call."""

    to: str
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"


class CampaignCallRequest(BaseModel):
    """Outbound voice campaign (Say/Play actions)."""

    to: list[str]
    message: str


class TransferRequest(BaseModel):
    """Transfer an active call to a human agent."""

    session_id: str
    transfer_to: str


class SendSMSRequest(BaseModel):
    """Send a single outbound SMS."""

    to: str
    message: str


class CampaignSMSRequest(BaseModel):
    """Bulk SMS campaign to a recipient list."""

    to: list[str]
    message: str
