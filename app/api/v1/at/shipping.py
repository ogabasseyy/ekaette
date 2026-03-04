"""AT shipping quote endpoints (Topship)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.configs import sanitize_log

from app.tools.shipping_tools import (
    create_order_record,
    get_topship_delivery_quote,
    send_order_review_followup,
    track_order_delivery,
    update_order_tracking_status,
)

from .models import (
    ShippingOrderCreateRequest,
    ShippingReviewFollowupRequest,
    ShippingTrackingStatusUpdateRequest,
    TopshipQuoteRequest,
)

router = APIRouter()


_TOPSHIP_ERROR_STATUS_MAP: dict[str, int] = {
    "TOPSHIP_NOT_CONFIGURED": 503,
    "TOPSHIP_INVALID_ROUTE": 400,
    "TOPSHIP_API_ERROR": 502,
    "TOPSHIP_REQUEST_FAILED": 502,
    "TOPSHIP_NO_QUOTES": 404,
}

_TOPSHIP_ERROR_MESSAGE_MAP: dict[str, str] = {
    "TOPSHIP_NOT_CONFIGURED": "Topship integration is not configured",
    "TOPSHIP_INVALID_ROUTE": "Invalid shipping route",
    "TOPSHIP_API_ERROR": "Topship API error",
    "TOPSHIP_REQUEST_FAILED": "Topship request failed",
    "TOPSHIP_NO_QUOTES": "No delivery quotes available",
}

_SHIPPING_ORDER_ERROR_STATUS_MAP: dict[str, int] = {
    "ORDER_INVALID": 400,
    "ORDER_NOT_FOUND": 404,
    "ORDER_SCOPE_UNAVAILABLE": 503,
    "ORDER_REVIEW_CONTACT_MISSING": 400,
    "ORDER_REVIEW_NOTIFICATION_FAILED": 502,
    "TOPSHIP_NOT_CONFIGURED": 503,
    "TOPSHIP_TRACKING_ID_REQUIRED": 400,
    "TOPSHIP_TRACKING_NOT_FOUND": 404,
    "TOPSHIP_TRACKING_INVALID_RESPONSE": 502,
    "TOPSHIP_TRACKING_API_ERROR": 502,
    "TOPSHIP_TRACKING_REQUEST_FAILED": 502,
}

_SHIPPING_ORDER_ERROR_MESSAGE_MAP: dict[str, str] = {
    "ORDER_INVALID": "Invalid shipping order request",
    "ORDER_NOT_FOUND": "Shipping order not found",
    "ORDER_SCOPE_UNAVAILABLE": "Shipping order scope unavailable",
    "ORDER_REVIEW_CONTACT_MISSING": "Customer contact is required for review follow-up",
    "ORDER_REVIEW_NOTIFICATION_FAILED": "Order review follow-up notification failed",
    "TOPSHIP_NOT_CONFIGURED": "Topship integration is not configured",
    "TOPSHIP_TRACKING_ID_REQUIRED": "Tracking ID is required",
    "TOPSHIP_TRACKING_NOT_FOUND": "Tracking record not found",
    "TOPSHIP_TRACKING_INVALID_RESPONSE": "Topship tracking returned an invalid response",
    "TOPSHIP_TRACKING_API_ERROR": "Topship tracking API error",
    "TOPSHIP_TRACKING_REQUEST_FAILED": "Topship tracking request failed",
}


class ShippingQuoteOptionResponse(BaseModel):
    service_type: str = ""
    pricing_tier: str = ""
    display_name: str = ""
    total_kobo: int = 0
    total_naira: float = 0.0
    currency: str = "NGN"
    delivery_eta: str = ""
    estimated_days: int = 0
    min_days: int | None = None
    max_days: int | None = None


class TopshipQuoteResponse(BaseModel):
    status: Literal["ok"] = "ok"
    route: str = ""
    sender_city: str = ""
    receiver_city: str = ""
    weight_kg: float = 0.0
    recommended: ShippingQuoteOptionResponse
    cheapest: ShippingQuoteOptionResponse
    fastest: ShippingQuoteOptionResponse
    quotes: list[ShippingQuoteOptionResponse] = Field(default_factory=list)


class ShippingTrackingEventResponse(BaseModel):
    status: str = ""
    description: str = ""
    location: str = ""
    timestamp: str = ""


class ShippingTrackingResponse(BaseModel):
    status: str = ""
    provider_status: str = ""
    estimated_delivery_at: str = ""
    delivered_at: str = ""
    updated_at: str = ""
    last_checked_at: str = ""
    events: list[ShippingTrackingEventResponse] = Field(default_factory=list)


class ShippingReviewFollowupResponse(BaseModel):
    requested: bool = False
    requested_at: str = ""
    sms_sent: bool = False
    whatsapp_sent: bool = False


class ShippingOrderResponse(BaseModel):
    status: Literal["ok"] = "ok"
    order_id: str = ""
    order_status: str = ""
    tracking: ShippingTrackingResponse = Field(default_factory=ShippingTrackingResponse)
    review_followup: ShippingReviewFollowupResponse = Field(default_factory=ShippingReviewFollowupResponse)
    note: str = ""


def _sanitize_quote_option(raw: object) -> ShippingQuoteOptionResponse:
    payload = raw if isinstance(raw, dict) else {}

    def _to_int(value: object, default: int = 0) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def _to_float(value: object, default: float = 0.0) -> float:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    return ShippingQuoteOptionResponse(
        service_type=str(payload.get("service_type") or ""),
        pricing_tier=str(payload.get("pricing_tier") or ""),
        display_name=str(payload.get("display_name") or ""),
        total_kobo=_to_int(payload.get("total_kobo")),
        total_naira=_to_float(payload.get("total_naira")),
        currency=str(payload.get("currency") or "NGN"),
        delivery_eta=str(payload.get("delivery_eta") or ""),
        estimated_days=_to_int(payload.get("estimated_days")),
        min_days=_to_int(payload.get("min_days")) if payload.get("min_days") is not None else None,
        max_days=_to_int(payload.get("max_days")) if payload.get("max_days") is not None else None,
    )


def _sanitize_quote_response(result: dict[str, Any]) -> TopshipQuoteResponse:
    quotes_raw = result.get("quotes") if isinstance(result.get("quotes"), list) else []
    quotes = [_sanitize_quote_option(item) for item in quotes_raw if isinstance(item, dict)]
    return TopshipQuoteResponse(
        status="ok",
        route=str(result.get("route") or ""),
        sender_city=str(result.get("sender_city") or ""),
        receiver_city=str(result.get("receiver_city") or ""),
        weight_kg=float(result.get("weight_kg") or 0.0),
        recommended=_sanitize_quote_option(result.get("recommended")),
        cheapest=_sanitize_quote_option(result.get("cheapest")),
        fastest=_sanitize_quote_option(result.get("fastest")),
        quotes=quotes,
    )


def _safe_topship_error_detail(code: str) -> dict[str, str]:
    safe_code = sanitize_log(code)
    return {
        "code": safe_code,
        "message": _TOPSHIP_ERROR_MESSAGE_MAP.get(code, "Topship error"),
    }


def _sanitize_tracking(raw: object) -> ShippingTrackingResponse:
    payload = raw if isinstance(raw, dict) else {}
    raw_events = payload.get("events") if isinstance(payload.get("events"), list) else []
    events = [
        ShippingTrackingEventResponse(
            status=str(item.get("status") or ""),
            description=str(item.get("description") or ""),
            location=str(item.get("location") or ""),
            timestamp=str(item.get("timestamp") or ""),
        )
        for item in raw_events
        if isinstance(item, dict)
    ]
    return ShippingTrackingResponse(
        status=str(payload.get("status") or ""),
        provider_status=str(payload.get("provider_status") or ""),
        estimated_delivery_at=str(payload.get("estimated_delivery_at") or ""),
        delivered_at=str(payload.get("delivered_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        last_checked_at=str(payload.get("last_checked_at") or ""),
        events=events,
    )


def _sanitize_review_followup(raw: object) -> ShippingReviewFollowupResponse:
    payload = raw if isinstance(raw, dict) else {}
    return ShippingReviewFollowupResponse(
        requested=bool(payload.get("requested", False)),
        requested_at=str(payload.get("requested_at") or ""),
        sms_sent=bool(payload.get("sms_sent", False)),
        whatsapp_sent=bool(payload.get("whatsapp_sent", False)),
    )


def _sanitize_order_response(result: dict[str, Any]) -> ShippingOrderResponse:
    order = result.get("order") if isinstance(result.get("order"), dict) else {}
    review_payload = result.get("review_followup")
    if not isinstance(review_payload, dict):
        review_payload = order.get("review_followup") if isinstance(order.get("review_followup"), dict) else {}
    return ShippingOrderResponse(
        status="ok",
        order_id=str(result.get("order_id") or ""),
        order_status=str(order.get("status") or ""),
        tracking=_sanitize_tracking(order.get("tracking")),
        review_followup=_sanitize_review_followup(review_payload),
        note=str(result.get("note") or ""),
    )


async def _resolve_quote_or_raise(
    *,
    sender_city: str,
    receiver_city: str,
    weight_kg: float,
    sender_country_code: str,
    receiver_country_code: str,
    prefer: str,
) -> TopshipQuoteResponse:
    result = await get_topship_delivery_quote(
        sender_city=sender_city,
        receiver_city=receiver_city,
        weight_kg=weight_kg,
        sender_country_code=sender_country_code,
        receiver_country_code=receiver_country_code,
        prefer=prefer,
    )

    if result.get("status") == "ok":
        return _sanitize_quote_response(result)

    code = str(result.get("code") or "TOPSHIP_ERROR")
    status_code = _TOPSHIP_ERROR_STATUS_MAP.get(code, 500)
    raise HTTPException(status_code=status_code, detail=_safe_topship_error_detail(code))


@router.post("/shipping/topship/quote", response_model=TopshipQuoteResponse)
async def topship_quote(req: TopshipQuoteRequest) -> TopshipQuoteResponse:
    """Get Topship delivery quotes for a route and weight."""
    return await _resolve_quote_or_raise(
        sender_city=req.sender_city,
        receiver_city=req.receiver_city,
        weight_kg=req.weight_kg,
        sender_country_code=req.sender_country_code,
        receiver_country_code=req.receiver_country_code,
        prefer=req.prefer,
    )


@router.get("/shipping/topship/quote", response_model=TopshipQuoteResponse)
async def topship_quote_get(
    sender_city: str = Query(alias="senderCity", min_length=2, max_length=120),
    receiver_city: str = Query(alias="receiverCity", min_length=2, max_length=120),
    weight_kg: float = Query(default=1.0, alias="weightKg", gt=0, le=1000),
    sender_country_code: str = Query(default="NG", alias="senderCountryCode", min_length=2, max_length=3),
    receiver_country_code: str = Query(default="NG", alias="receiverCountryCode", min_length=2, max_length=3),
    prefer: str = Query(default="cheapest", pattern="^(cheapest|fastest)$"),
) -> TopshipQuoteResponse:
    """Get Topship delivery quotes via query params (quick/manual checks)."""
    return await _resolve_quote_or_raise(
        sender_city=sender_city,
        receiver_city=receiver_city,
        weight_kg=weight_kg,
        sender_country_code=sender_country_code,
        receiver_country_code=receiver_country_code,
        prefer=prefer,
    )


def _raise_order_tool_error(result: dict) -> None:
    code = str(result.get("code") or "SHIPPING_ORDER_ERROR")
    status_code = _SHIPPING_ORDER_ERROR_STATUS_MAP.get(code, 500)
    detail = {
        "error": _SHIPPING_ORDER_ERROR_MESSAGE_MAP.get(code, "Shipping order request failed"),
        "code": sanitize_log(code),
    }
    raise HTTPException(status_code=status_code, detail=detail)


@router.post("/shipping/orders", response_model=ShippingOrderResponse)
async def shipping_order_create(req: ShippingOrderCreateRequest) -> ShippingOrderResponse:
    """Persist an order before tracking/payment follow-up."""
    result = await create_order_record(
        customer_name=req.customer_name,
        customer_phone=req.customer_phone,
        items_summary=req.items_summary,
        amount_kobo=req.amount_kobo,
        payment_reference=req.payment_reference,
        sender_city=req.sender_city,
        receiver_city=req.receiver_city,
        delivery_address=req.delivery_address,
        shipping_provider=req.shipping_provider,
        provider_tracking_id=req.provider_tracking_id,
        provider_shipment_id=req.provider_shipment_id,
        order_id=req.order_id,
        tenant_id=req.tenant_id,
        company_id=req.company_id,
    )
    if result.get("status") != "ok":
        _raise_order_tool_error(result)
    return _sanitize_order_response(result)


@router.get("/shipping/orders/{order_id}/tracking", response_model=ShippingOrderResponse)
async def shipping_order_track_get(
    order_id: str,
    refresh_provider: bool = Query(default=True, alias="refreshProvider"),
    provider_tracking_id: str | None = Query(default=None, alias="providerTrackingId"),
    trigger_review_followup: bool = Query(default=True, alias="triggerReviewFollowup"),
) -> ShippingOrderResponse:
    """Fetch tracking status for a saved order, optionally refreshing from provider."""
    result = await track_order_delivery(
        order_id=order_id,
        refresh_from_provider=refresh_provider,
        provider_tracking_id=provider_tracking_id,
        trigger_review_followup=trigger_review_followup,
    )
    if result.get("status") != "ok":
        _raise_order_tool_error(result)
    return _sanitize_order_response(result)


@router.post("/shipping/orders/{order_id}/tracking/status", response_model=ShippingOrderResponse)
async def shipping_order_tracking_update(
    order_id: str,
    req: ShippingTrackingStatusUpdateRequest,
) -> ShippingOrderResponse:
    """Update order tracking status (manual/webhook side)."""
    result = await update_order_tracking_status(
        order_id=order_id,
        tracking_status=req.tracking_status,
        provider_status=req.provider_status,
        provider_tracking_id=req.provider_tracking_id,
        provider_shipment_id=req.provider_shipment_id,
        provider=req.provider,
        event_description=req.event_description,
        location=req.location,
        event_timestamp=req.event_timestamp,
        trigger_review_followup=req.trigger_review_followup,
    )
    if result.get("status") != "ok":
        _raise_order_tool_error(result)
    return _sanitize_order_response(result)


@router.post("/shipping/orders/{order_id}/review-followup", response_model=ShippingOrderResponse)
async def shipping_order_review_followup(
    order_id: str,
    req: ShippingReviewFollowupRequest | None = None,
) -> ShippingOrderResponse:
    """Manually trigger review follow-up messaging for an order."""
    payload = req or ShippingReviewFollowupRequest()
    result = await send_order_review_followup(
        order_id=order_id,
        force=payload.force,
        message=payload.message,
    )
    if result.get("status") != "ok":
        _raise_order_tool_error(result)
    return _sanitize_order_response(result)
