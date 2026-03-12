"""Pydantic v2 request/response models for AT voice and SMS channels."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OutboundCallRequest(BaseModel):
    """Initiate an outbound voice call."""

    to: str = Field(min_length=1)
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"
    campaign_id: str | None = None
    campaign_name: str | None = None


class CampaignCallRequest(BaseModel):
    """Outbound voice campaign (Say/Play actions)."""

    to: list[str] = Field(min_length=1)
    message: str = Field(min_length=1)
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"
    campaign_id: str | None = None
    campaign_name: str | None = None


class TransferRequest(BaseModel):
    """Transfer an active call to a human agent."""

    session_id: str
    transfer_to: str


class CallbackRequest(BaseModel):
    """Request an outbound callback to a customer."""

    phone: str = Field(min_length=1)
    reason: str | None = None
    mode: Literal["after_hangup", "immediate"] = "after_hangup"
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"


class SendSMSRequest(BaseModel):
    """Send a single outbound SMS."""

    to: str = Field(min_length=1)
    message: str = Field(min_length=1)
    sender_id: str | None = None
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"
    campaign_id: str | None = None
    campaign_name: str | None = None


class CampaignSMSRequest(BaseModel):
    """Bulk SMS campaign to a recipient list."""

    to: list[str] = Field(min_length=1)
    message: str = Field(min_length=1)
    sender_id: str | None = None
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"
    campaign_id: str | None = None
    campaign_name: str | None = None


class CampaignAnalyticsEventRequest(BaseModel):
    """Manual analytics event ingestion."""

    event_type: Literal[
        "sent",
        "delivered",
        "failed",
        "reply",
        "conversion",
        "payment_initialized",
        "payment_success",
    ]
    channel: Literal["sms", "voice", "omni"] = "sms"
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"
    campaign_id: str | None = None
    campaign_name: str | None = None
    recipient: str | None = None
    amount_kobo: int | None = Field(default=None, ge=0)
    reference: str | None = None
    event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaystackInitializeRequest(BaseModel):
    """Initialize a Paystack transaction."""

    model_config = ConfigDict(populate_by_name=True)

    email: str
    amount_kobo: int = Field(alias="amountKobo", ge=100)
    currency: str = "NGN"
    callback_url: str | None = Field(default=None, alias="callbackUrl")
    reference: str | None = None
    tenant_id: str = Field(default="public", alias="tenantId")
    company_id: str = Field(default="ekaette-electronics", alias="companyId")
    campaign_id: str | None = Field(default=None, alias="campaignId")
    customer_phone: str | None = Field(default=None, alias="customerPhone")
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaystackVirtualAccountCreateRequest(BaseModel):
    """Create a Paystack dedicated virtual account for transfer payments."""

    model_config = ConfigDict(populate_by_name=True)

    email: str
    first_name: str = Field(alias="firstName", min_length=1, max_length=80)
    last_name: str = Field(alias="lastName", min_length=1, max_length=80)
    phone: str | None = None
    preferred_bank_slug: str | None = Field(default=None, alias="preferredBankSlug")
    country: str | None = None
    tenant_id: str = Field(default="public", alias="tenantId")
    company_id: str = Field(default="ekaette-electronics", alias="companyId")
    campaign_id: str | None = Field(default=None, alias="campaignId")
    expected_amount_kobo: int | None = Field(default=None, alias="expectedAmountKobo", ge=100)
    reference: str | None = None
    customer_phone: str | None = Field(default=None, alias="customerPhone")
    metadata: dict[str, Any] = Field(default_factory=dict)


class TopshipQuoteRequest(BaseModel):
    """Request model for Topship delivery quote endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    sender_city: str = Field(alias="senderCity", min_length=2, max_length=120)
    receiver_city: str = Field(alias="receiverCity", min_length=2, max_length=120)
    weight_kg: float = Field(default=1.0, alias="weightKg", gt=0, le=1000)
    sender_country_code: str = Field(default="NG", alias="senderCountryCode", min_length=2, max_length=3)
    receiver_country_code: str = Field(default="NG", alias="receiverCountryCode", min_length=2, max_length=3)
    prefer: Literal["cheapest", "fastest"] = "cheapest"


class ShippingOrderCreateRequest(BaseModel):
    """Create/persist an order record before tracking updates."""

    model_config = ConfigDict(populate_by_name=True)

    customer_name: str = Field(alias="customerName", min_length=1, max_length=120)
    customer_phone: str | None = Field(default=None, alias="customerPhone")
    items_summary: str | None = Field(default=None, alias="itemsSummary", max_length=1200)
    amount_kobo: int | None = Field(default=None, alias="amountKobo", ge=0)
    payment_reference: str | None = Field(default=None, alias="paymentReference", max_length=128)
    sender_city: str | None = Field(default=None, alias="senderCity", max_length=120)
    receiver_city: str | None = Field(default=None, alias="receiverCity", max_length=120)
    delivery_address: str | None = Field(default=None, alias="deliveryAddress", max_length=400)
    shipping_provider: str = Field(default="topship", alias="shippingProvider", max_length=64)
    provider_tracking_id: str | None = Field(default=None, alias="providerTrackingId", max_length=120)
    provider_shipment_id: str | None = Field(default=None, alias="providerShipmentId", max_length=120)
    order_id: str | None = Field(default=None, alias="orderId", max_length=80)
    tenant_id: str = Field(default="public", alias="tenantId")
    company_id: str = Field(default="ekaette-electronics", alias="companyId")


class ShippingTrackingStatusUpdateRequest(BaseModel):
    """Manual tracking status update request."""

    model_config = ConfigDict(populate_by_name=True)

    tracking_status: str = Field(alias="trackingStatus", min_length=1, max_length=80)
    provider_status: str | None = Field(default=None, alias="providerStatus", max_length=120)
    provider_tracking_id: str | None = Field(default=None, alias="providerTrackingId", max_length=120)
    provider_shipment_id: str | None = Field(default=None, alias="providerShipmentId", max_length=120)
    provider: str | None = Field(default=None, max_length=64)
    event_description: str | None = Field(default=None, alias="eventDescription", max_length=300)
    location: str | None = Field(default=None, max_length=120)
    event_timestamp: str | None = Field(default=None, alias="eventTimestamp", max_length=80)
    trigger_review_followup: bool = Field(default=True, alias="triggerReviewFollowup")


class ShippingReviewFollowupRequest(BaseModel):
    """Manual order review follow-up trigger request."""

    model_config = ConfigDict(populate_by_name=True)

    force: bool = False
    message: str | None = Field(default=None, max_length=320)
