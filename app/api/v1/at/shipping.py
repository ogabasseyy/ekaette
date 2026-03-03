"""AT shipping quote endpoints (Topship)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.configs import sanitize_log

logger = logging.getLogger(__name__)

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


async def _resolve_quote_or_raise(
    *,
    sender_city: str,
    receiver_city: str,
    weight_kg: float,
    sender_country_code: str,
    receiver_country_code: str,
    prefer: str,
) -> dict:
    result = await get_topship_delivery_quote(
        sender_city=sender_city,
        receiver_city=receiver_city,
        weight_kg=weight_kg,
        sender_country_code=sender_country_code,
        receiver_country_code=receiver_country_code,
        prefer=prefer,
    )

    if result.get("status") == "ok":
        return result

    code = str(result.get("code") or "TOPSHIP_ERROR")
    status_code = _TOPSHIP_ERROR_STATUS_MAP.get(code, 500)
    logger.warning("Topship quote error", extra={"code": sanitize_log(code)})
    raise HTTPException(status_code=status_code, detail={"code": code, "error": "Shipping quote request failed"})


@router.post("/shipping/topship/quote")
async def topship_quote(req: TopshipQuoteRequest) -> dict:
    """Get Topship delivery quotes for a route and weight."""
    return await _resolve_quote_or_raise(
        sender_city=req.sender_city,
        receiver_city=req.receiver_city,
        weight_kg=req.weight_kg,
        sender_country_code=req.sender_country_code,
        receiver_country_code=req.receiver_country_code,
        prefer=req.prefer,
    )


@router.get("/shipping/topship/quote")
async def topship_quote_get(
    sender_city: str = Query(alias="senderCity", min_length=2, max_length=120),
    receiver_city: str = Query(alias="receiverCity", min_length=2, max_length=120),
    weight_kg: float = Query(default=1.0, alias="weightKg", gt=0, le=1000),
    sender_country_code: str = Query(default="NG", alias="senderCountryCode", min_length=2, max_length=3),
    receiver_country_code: str = Query(default="NG", alias="receiverCountryCode", min_length=2, max_length=3),
    prefer: str = Query(default="cheapest", pattern="^(cheapest|fastest)$"),
) -> dict:
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
    logger.warning("Shipping order error", extra={"code": sanitize_log(code)})
    raise HTTPException(status_code=status_code, detail={"code": code, "error": "Shipping order operation failed"})


@router.post("/shipping/orders")
async def shipping_order_create(req: ShippingOrderCreateRequest) -> dict:
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
    return result


@router.get("/shipping/orders/{order_id}/tracking")
async def shipping_order_track_get(
    order_id: str,
    refresh_provider: bool = Query(default=True, alias="refreshProvider"),
    provider_tracking_id: str | None = Query(default=None, alias="providerTrackingId"),
    trigger_review_followup: bool = Query(default=True, alias="triggerReviewFollowup"),
) -> dict:
    """Fetch tracking status for a saved order, optionally refreshing from provider."""
    result = await track_order_delivery(
        order_id=order_id,
        refresh_from_provider=refresh_provider,
        provider_tracking_id=provider_tracking_id,
        trigger_review_followup=trigger_review_followup,
    )
    if result.get("status") != "ok":
        _raise_order_tool_error(result)
    return result


@router.post("/shipping/orders/{order_id}/tracking/status")
async def shipping_order_tracking_update(order_id: str, req: ShippingTrackingStatusUpdateRequest) -> dict:
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
    return result


@router.post("/shipping/orders/{order_id}/review-followup")
async def shipping_order_review_followup(
    order_id: str,
    req: ShippingReviewFollowupRequest | None = None,
) -> dict:
    """Manually trigger review follow-up messaging for an order."""
    payload = req or ShippingReviewFollowupRequest()
    result = await send_order_review_followup(
        order_id=order_id,
        force=payload.force,
        message=payload.message,
    )
    if result.get("status") != "ok":
        _raise_order_tool_error(result)
    return result
