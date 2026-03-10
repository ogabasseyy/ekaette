"""Shipping tools for Topship quote/tracking and order follow-up workflows."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.tools.sms_messaging import resolve_sms_sender_id_from_state
from app.tools.scoped_queries import scoped_collection_or_global

logger = logging.getLogger(__name__)

TOPSHIP_API_KEY = os.getenv("TOPSHIP_API_KEY", "").strip()
TOPSHIP_USE_SANDBOX = os.getenv("TOPSHIP_USE_SANDBOX", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TOPSHIP_BASE_URL = (
    os.getenv("TOPSHIP_SANDBOX_URL", "https://topship-staging.africa/api").strip()
    if TOPSHIP_USE_SANDBOX
    else os.getenv("TOPSHIP_BASE_URL", "https://api-topship.com/api").strip()
)
TOPSHIP_TIMEOUT_SECONDS = float(os.getenv("TOPSHIP_TIMEOUT_SECONDS", "8"))

# Review follow-up messaging controls (kept patchable for tests).
AT_SMS_ENABLED = os.getenv("AT_SMS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()

_TOPSHIP_DAYS_RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
_TOPSHIP_DAYS_SINGLE_RE = re.compile(r"(\d+)")
_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{4,80}$")

_TRACKING_STATUS_MAP: dict[str, str] = {
    "pending": "pending",
    "booked": "booked",
    "awaitingpickup": "pickup_scheduled",
    "pickupinprogress": "pickup_scheduled",
    "successfullypicked": "picked_up",
    "intransit": "in_transit",
    "deliveryinprogress": "out_for_delivery",
    "outfordelivery": "out_for_delivery",
    "delivered": "delivered",
    "cancelled": "cancelled",
    "deliveryfailed": "failed",
    "failed": "failed",
    "returned": "returned",
}

_order_lock = threading.Lock()
_order_records: dict[str, dict[str, Any]] = {}
_firestore_db: Any = None


def _currency_name(currency: object) -> str:
    raw = str(currency or "").strip().upper()
    if raw == "NGN":
        return "naira"
    return raw or "currency"


def _format_amount_display(total_kobo: int, currency: object) -> str:
    amount = round(total_kobo / 100, 2)
    currency_name = _currency_name(currency)
    if currency_name == "naira":
        return f"{amount:,.2f} naira"
    return f"{currency_name} {amount:,.2f}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deepcopy(value: Any) -> Any:
    return copy.deepcopy(value)


def _clean_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return 0


def _normalize_order_id(order_id: str | None) -> str:
    candidate = _clean_str(order_id)
    if candidate and _ORDER_ID_RE.fullmatch(candidate):
        return candidate
    return f"EKT-ORD-{uuid.uuid4().hex[:10].upper()}"


def _validated_order_id(order_id: str | None) -> str | None:
    candidate = _clean_str(order_id)
    if not candidate:
        return None
    if not _ORDER_ID_RE.fullmatch(candidate):
        return None
    return candidate


def _tenant_company_from_context(
    tool_context: Any,
    *,
    tenant_id: str | None = None,
    company_id: str | None = None,
) -> tuple[str, str]:
    if _clean_str(tenant_id) and _clean_str(company_id):
        return _clean_str(tenant_id), _clean_str(company_id)

    state = getattr(tool_context, "state", {}) if tool_context is not None else {}
    tenant = _clean_str(state.get("app:tenant_id")) if hasattr(state, "get") else ""
    company = _clean_str(state.get("app:company_id")) if hasattr(state, "get") else ""
    return tenant or "public", company or "ekaette-electronics"


def _get_firestore_db() -> Any | None:
    """Get or create Firestore client. Returns None if unavailable."""
    global _firestore_db
    if _firestore_db is not None:
        return _firestore_db
    try:
        from google.cloud import firestore

        _firestore_db = firestore.Client()
        return _firestore_db
    except Exception as exc:
        logger.warning("Firestore client unavailable for shipping orders: %s", exc)
        return None


def _orders_collection(tool_context: Any) -> tuple[Any | None, str]:
    """Return (collection, storage_mode) for orders."""
    db = _get_firestore_db()
    if db is None:
        return None, "memory"
    collection = scoped_collection_or_global(db, tool_context, "orders")
    if collection is None:
        return None, "scope_error"
    return collection, "firestore"


async def _read_order_record(order_id: str, tool_context: Any) -> tuple[dict[str, Any] | None, str]:
    collection, storage_mode = _orders_collection(tool_context)
    if storage_mode == "scope_error":
        return None, storage_mode
    if storage_mode == "memory":
        with _order_lock:
            record = _order_records.get(order_id)
            return (_deepcopy(record) if isinstance(record, dict) else None), storage_mode

    doc_ref = collection.document(order_id)
    doc = await asyncio.to_thread(doc_ref.get)
    if not getattr(doc, "exists", False):
        return None, storage_mode

    data = doc.to_dict() if hasattr(doc, "to_dict") else {}
    if not isinstance(data, dict):
        return None, storage_mode
    data["order_id"] = order_id
    return data, storage_mode


async def _write_order_record(order_id: str, record: dict[str, Any], tool_context: Any) -> str:
    payload = _deepcopy(record)
    payload["order_id"] = order_id
    payload["updated_at"] = _now_iso()

    collection, storage_mode = _orders_collection(tool_context)
    if storage_mode == "scope_error":
        return storage_mode

    if storage_mode == "memory":
        with _order_lock:
            _order_records[order_id] = payload
        return storage_mode

    doc_ref = collection.document(order_id)
    await asyncio.to_thread(lambda: doc_ref.set(payload, merge=True))
    return storage_mode


def _tracking_status_or_default(status: str | None) -> str:
    raw = _clean_str(status)
    if not raw:
        return "pending"
    canonical = raw.replace("_", "").replace("-", "").replace(" ", "").lower()
    return _TRACKING_STATUS_MAP.get(canonical, raw.lower().replace("-", "_").replace(" ", "_"))


def _base_review_followup_payload() -> dict[str, Any]:
    return {
        "requested": False,
        "requested_at": None,
        "message": "",
        "sms_sent": False,
        "whatsapp_sent": False,
    }


def _base_tracking_payload(provider: str = "topship") -> dict[str, Any]:
    return {
        "provider": _clean_str(provider).lower() or "topship",
        "status": "pending",
        "provider_status": "",
        "tracking_id": "",
        "shipment_id": "",
        "estimated_delivery_at": None,
        "delivered_at": None,
        "events": [],
        "updated_at": _now_iso(),
        "last_checked_at": None,
    }


def _coerce_kobo(value: object) -> int:
    coerced = _coerce_positive_int(value)
    return coerced if isinstance(coerced, int) else 0


def _parse_delivery_eta(eta_text: str) -> dict[str, int | None]:
    raw = (eta_text or "").strip().lower()
    if not raw:
        return {"estimated_days": 5, "min_days": None, "max_days": None}

    range_match = _TOPSHIP_DAYS_RANGE_RE.search(raw)
    if range_match:
        min_days = int(range_match.group(1))
        max_days = int(range_match.group(2))
        return {
            "estimated_days": round((min_days + max_days) / 2),
            "min_days": min_days,
            "max_days": max_days,
        }

    single_match = _TOPSHIP_DAYS_SINGLE_RE.search(raw)
    if single_match:
        days = int(single_match.group(1))
        return {"estimated_days": days, "min_days": days, "max_days": days}

    return {"estimated_days": 5, "min_days": None, "max_days": None}


def _carrier_display_name(pricing_tier: str, carrier_name: str) -> str:
    if carrier_name:
        return carrier_name

    tier = pricing_tier.lower()
    if "fedex" in tier:
        return "FedEx"
    if "dhl" in tier:
        return "DHL"
    if "ups" in tier:
        return "UPS"
    if "aramex" in tier:
        return "Aramex"
    if "gig" in tier:
        return "GIG Logistics"
    if tier == "budget":
        return "Budget Shipping"
    if tier == "express":
        return "Express Shipping"
    if tier == "premium":
        return "Premium Shipping"
    return pricing_tier or "Topship"


async def _fetch_topship_rates(shipment_detail: dict[str, Any]) -> tuple[int, object]:
    encoded = json.dumps(shipment_detail, separators=(",", ":"))
    url = f"{TOPSHIP_BASE_URL.rstrip('/')}/get-shipment-rate"
    headers = {
        "Authorization": f"Bearer {TOPSHIP_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=TOPSHIP_TIMEOUT_SECONDS) as client:
        response = await client.get(url, params={"shipmentDetail": encoded}, headers=headers)

    try:
        payload = response.json()
    except Exception:
        payload = response.text
    return response.status_code, payload


async def _fetch_topship_tracking(tracking_id: str) -> dict[str, Any]:
    if not TOPSHIP_API_KEY:
        return {
            "status": "error",
            "error": "Topship is not configured.",
            "code": "TOPSHIP_NOT_CONFIGURED",
            "provider": "topship",
        }
    if not _clean_str(tracking_id):
        return {
            "status": "error",
            "error": "tracking_id is required.",
            "code": "TOPSHIP_TRACKING_ID_REQUIRED",
            "provider": "topship",
        }

    url = f"{TOPSHIP_BASE_URL.rstrip('/')}/track-shipment"
    headers = {
        "Authorization": f"Bearer {TOPSHIP_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TOPSHIP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params={"trackingId": tracking_id}, headers=headers)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"Topship tracking request failed: {exc}",
            "code": "TOPSHIP_TRACKING_REQUEST_FAILED",
            "provider": "topship",
        }

    try:
        payload = response.json()
    except Exception:
        payload = {}

    if response.status_code >= 400:
        return {
            "status": "error",
            "error": "Topship returned an error while tracking shipment.",
            "code": "TOPSHIP_TRACKING_API_ERROR",
            "provider": "topship",
            "status_code": response.status_code,
        }

    if not isinstance(payload, dict) or payload.get("status") is not True:
        return {
            "status": "error",
            "error": str(payload.get("message") or "Shipment not found"),
            "code": "TOPSHIP_TRACKING_NOT_FOUND",
            "provider": "topship",
        }

    data = payload.get("data")
    if not isinstance(data, dict):
        return {
            "status": "error",
            "error": "Topship tracking response missing data.",
            "code": "TOPSHIP_TRACKING_INVALID_RESPONSE",
            "provider": "topship",
        }

    provider_status = _clean_str(data.get("status"))
    normalized_status = _tracking_status_or_default(provider_status)

    events: list[dict[str, Any]] = []
    for raw_event in data.get("events", []):
        if not isinstance(raw_event, dict):
            continue
        raw_status = _clean_str(raw_event.get("status"))
        events.append(
            {
                "status": _tracking_status_or_default(raw_status or normalized_status),
                "provider_status": raw_status or provider_status,
                "description": _clean_str(raw_event.get("description")) or raw_status or provider_status,
                "location": _clean_str(raw_event.get("location")),
                "timestamp": _clean_str(raw_event.get("timestamp")) or _now_iso(),
            }
        )

    events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)

    return {
        "status": "ok",
        "provider": "topship",
        "tracking_id": _clean_str(data.get("trackingId")) or _clean_str(tracking_id),
        "provider_status": provider_status,
        "normalized_status": normalized_status,
        "estimated_delivery_at": data.get("estimatedDeliveryDate"),
        "delivered_at": data.get("deliveredAt"),
        "events": events[:50],
        "raw": data,
    }


def _extract_rate_items(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

    return []


def _map_rate_to_quote(rate: dict[str, Any]) -> dict[str, Any]:
    service_type = str(rate.get("serviceType") or rate.get("mode") or "Standard").strip() or "Standard"
    pricing_tier = str(rate.get("pricingTier") or "Budget").strip() or "Budget"
    carrier_name = _carrier_display_name(
        pricing_tier=pricing_tier,
        carrier_name=str(rate.get("carrierName") or "").strip(),
    )

    total_kobo = _coerce_kobo(rate.get("total"))
    if total_kobo <= 0:
        total_kobo = _coerce_kobo(rate.get("cost"))

    vat_kobo = _coerce_kobo(rate.get("vat"))
    delivery_eta = str(rate.get("deliveryEta") or rate.get("duration") or "").strip()
    parsed_eta = _parse_delivery_eta(delivery_eta)

    currency_code = str(rate.get("currency") or "NGN").upper()
    return {
        "service_type": service_type,
        "pricing_tier": pricing_tier,
        "carrier_name": carrier_name,
        "display_name": f"{carrier_name} - {service_type}",
        "total_kobo": total_kobo,
        "total_naira": round(total_kobo / 100, 2),
        "vat_kobo": vat_kobo,
        "currency": currency_code,
        "currency_name": _currency_name(currency_code),
        "total_display": _format_amount_display(total_kobo, currency_code),
        "delivery_eta": delivery_eta,
        "estimated_days": int(parsed_eta["estimated_days"] or 5),
        "min_days": parsed_eta["min_days"],
        "max_days": parsed_eta["max_days"],
    }


def _route_summary(sender_city: str, receiver_city: str, weight_kg: float) -> str:
    return f"{sender_city} to {receiver_city} ({weight_kg:.1f}kg)"


async def get_topship_delivery_quote(
    sender_city: str,
    receiver_city: str,
    weight_kg: float = 1.0,
    sender_country_code: str = "NG",
    receiver_country_code: str = "NG",
    prefer: str = "cheapest",
    tool_context: Any = None,
) -> dict[str, Any]:
    """Get Topship delivery quote so the agent can state delivery cost pre-payment."""
    _ = tool_context

    if not TOPSHIP_API_KEY:
        return {
            "error": "Topship is not configured.",
            "code": "TOPSHIP_NOT_CONFIGURED",
            "provider": "topship",
        }

    sender = (sender_city or "").strip()
    receiver = (receiver_city or "").strip()
    if not sender or not receiver:
        return {
            "error": "sender_city and receiver_city are required.",
            "code": "TOPSHIP_INVALID_ROUTE",
            "provider": "topship",
        }

    if weight_kg <= 0:
        weight_kg = 1.0

    shipment_detail = {
        "senderDetails": {
            "cityName": sender,
            "countryCode": (sender_country_code or "NG").strip().upper(),
        },
        "receiverDetails": {
            "cityName": receiver,
            "countryCode": (receiver_country_code or "NG").strip().upper(),
        },
        "totalWeight": round(weight_kg, 2),
    }

    try:
        status_code, payload = await _fetch_topship_rates(shipment_detail)
    except Exception as exc:
        return {
            "error": f"Topship request failed: {exc}",
            "code": "TOPSHIP_REQUEST_FAILED",
            "provider": "topship",
            "route": _route_summary(sender, receiver, weight_kg),
        }

    if status_code >= 400:
        return {
            "error": "Topship returned an error while fetching rates.",
            "code": "TOPSHIP_API_ERROR",
            "provider": "topship",
            "status_code": status_code,
            "route": _route_summary(sender, receiver, weight_kg),
        }

    rates = _extract_rate_items(payload)
    quotes = [_map_rate_to_quote(rate) for rate in rates]
    quotes = [quote for quote in quotes if quote["total_kobo"] > 0]

    if not quotes:
        return {
            "error": "No delivery quotes available for this route right now.",
            "code": "TOPSHIP_NO_QUOTES",
            "provider": "topship",
            "route": _route_summary(sender, receiver, weight_kg),
        }

    cheapest = min(quotes, key=lambda item: (item["total_kobo"], item["estimated_days"]))
    fastest = min(quotes, key=lambda item: (item["estimated_days"], item["total_kobo"]))

    preferred_key = (prefer or "cheapest").strip().lower()
    recommended = fastest if preferred_key == "fastest" else cheapest

    quotes_sorted = sorted(quotes, key=lambda item: (item["total_kobo"], item["estimated_days"]))

    return {
        "status": "ok",
        "provider": "topship",
        "route": _route_summary(sender, receiver, weight_kg),
        "sender_city": sender,
        "receiver_city": receiver,
        "weight_kg": round(weight_kg, 2),
        "recommended": recommended,
        "cheapest": cheapest,
        "fastest": fastest,
        "quotes": quotes_sorted,
    }


def _review_message_for(order_id: str) -> str:
    return (
        f"Thanks for your order {order_id}. "
        "Please rate your delivery experience 1-5 and share a short review."
    )


async def _send_sms_message(phone: str, message: str, sender_id: str | None = None) -> bool:
    if not AT_SMS_ENABLED:
        return False
    clean_phone = _clean_str(phone)
    if not clean_phone:
        return False
    try:
        from app.api.v1.at import providers

        await providers.send_sms(
            message=message,
            recipients=[clean_phone],
            sender_id=sender_id or None,
        )
        return True
    except Exception:
        logger.warning("Order review SMS failed for %s", clean_phone, exc_info=True)
        return False


async def _send_whatsapp_message(phone: str, message: str) -> bool:
    if not WHATSAPP_ENABLED or not WHATSAPP_ACCESS_TOKEN:
        return False
    clean_phone = _clean_str(phone)
    if not clean_phone:
        return False
    try:
        from app.api.v1.at import providers

        status_code, _ = await providers.whatsapp_send_text(
            access_token=WHATSAPP_ACCESS_TOKEN,
            to=clean_phone,
            body=message,
        )
        return status_code < 400
    except Exception:
        logger.warning("Order review WhatsApp failed for %s", clean_phone, exc_info=True)
        return False


def _tracking_event(
    *,
    tracking_status: str,
    provider_status: str,
    event_description: str,
    location: str,
    event_timestamp: str,
) -> dict[str, Any]:
    return {
        "status": tracking_status,
        "provider_status": provider_status,
        "description": event_description,
        "location": location,
        "timestamp": event_timestamp,
    }


def _resolve_order_status_from_tracking(tracking_status: str) -> str:
    if tracking_status == "delivered":
        return "fulfilled"
    if tracking_status in {"failed", "cancelled", "returned"}:
        return tracking_status
    if tracking_status in {"pending", "booked", "pickup_scheduled", "picked_up", "in_transit", "out_for_delivery"}:
        return "in_fulfillment"
    return tracking_status


async def create_order_record(
    customer_name: str,
    customer_phone: str | None = None,
    items_summary: str | None = None,
    amount_kobo: int | None = None,
    payment_reference: str | None = None,
    sender_city: str | None = None,
    receiver_city: str | None = None,
    delivery_address: str | None = None,
    shipping_provider: str = "topship",
    provider_tracking_id: str | None = None,
    provider_shipment_id: str | None = None,
    order_id: str | None = None,
    tenant_id: str | None = None,
    company_id: str | None = None,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Create or upsert an order record before tracking/payment follow-up."""
    if not _clean_str(customer_name):
        return {
            "status": "error",
            "error": "customer_name is required.",
            "code": "ORDER_INVALID",
        }

    if order_id is not None and _validated_order_id(order_id) is None:
        return {
            "status": "error",
            "error": "order_id format is invalid.",
            "code": "ORDER_INVALID",
        }
    resolved_order_id = _normalize_order_id(order_id)
    existing, storage_mode = await _read_order_record(resolved_order_id, tool_context)
    if storage_mode == "scope_error":
        return {
            "status": "error",
            "error": "Order scope is not available for this session context.",
            "code": "ORDER_SCOPE_UNAVAILABLE",
        }

    now = _now_iso()
    resolved_tenant_id, resolved_company_id = _tenant_company_from_context(
        tool_context,
        tenant_id=tenant_id,
        company_id=company_id,
    )
    record = _deepcopy(existing) if isinstance(existing, dict) else {}

    customer = record.get("customer")
    if not isinstance(customer, dict):
        customer = {}
    customer["name"] = _clean_str(customer_name)
    if customer_phone is not None:
        customer["phone"] = _clean_str(customer_phone)

    delivery = record.get("delivery")
    if not isinstance(delivery, dict):
        delivery = {}
    if sender_city is not None:
        delivery["sender_city"] = _clean_str(sender_city)
    if receiver_city is not None:
        delivery["receiver_city"] = _clean_str(receiver_city)
    if delivery_address is not None:
        delivery["address"] = _clean_str(delivery_address)

    tracking = record.get("tracking")
    if not isinstance(tracking, dict):
        tracking = _base_tracking_payload(provider=shipping_provider)
    tracking["provider"] = _clean_str(shipping_provider).lower() or _clean_str(tracking.get("provider")) or "topship"
    if provider_tracking_id is not None:
        tracking["tracking_id"] = _clean_str(provider_tracking_id)
    if provider_shipment_id is not None:
        tracking["shipment_id"] = _clean_str(provider_shipment_id)
    tracking["status"] = _tracking_status_or_default(_clean_str(tracking.get("status")) or "pending")
    tracking.setdefault("events", [])
    tracking["updated_at"] = now

    review_followup = record.get("review_followup")
    if not isinstance(review_followup, dict):
        review_followup = _base_review_followup_payload()

    record["order_id"] = resolved_order_id
    record["tenant_id"] = resolved_tenant_id
    record["company_id"] = resolved_company_id
    record["customer"] = customer
    record["items_summary"] = _clean_str(items_summary) if items_summary is not None else record.get("items_summary", "")
    if amount_kobo is not None:
        record["amount_kobo"] = _coerce_positive_int(amount_kobo) or 0
    else:
        record.setdefault("amount_kobo", 0)
    if payment_reference is not None:
        record["payment_reference"] = _clean_str(payment_reference)
    else:
        record.setdefault("payment_reference", "")
    record["delivery"] = delivery
    record["tracking"] = tracking
    record["review_followup"] = review_followup
    record["status"] = _clean_str(record.get("status")) or "order_received"
    record["created_at"] = _clean_str(record.get("created_at")) or now
    record["updated_at"] = now

    saved_mode = await _write_order_record(resolved_order_id, record, tool_context)
    if saved_mode == "scope_error":
        return {
            "status": "error",
            "error": "Order scope is not available for this session context.",
            "code": "ORDER_SCOPE_UNAVAILABLE",
        }
    return {
        "status": "ok",
        "order_id": resolved_order_id,
        "order": record,
        "storage": saved_mode,
    }


async def send_order_review_followup(
    order_id: str,
    force: bool = False,
    message: str | None = None,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Send review follow-up over SMS/WhatsApp for a delivered order."""
    resolved_order_id = _validated_order_id(order_id)
    if not resolved_order_id:
        return {
            "status": "error",
            "error": "order_id format is invalid.",
            "code": "ORDER_INVALID",
        }
    order, storage_mode = await _read_order_record(resolved_order_id, tool_context)
    if storage_mode == "scope_error":
        return {
            "status": "error",
            "error": "Order scope is not available for this session context.",
            "code": "ORDER_SCOPE_UNAVAILABLE",
            "order_id": resolved_order_id,
        }
    if order is None:
        return {
            "status": "error",
            "error": f"Order '{resolved_order_id}' not found.",
            "code": "ORDER_NOT_FOUND",
            "order_id": resolved_order_id,
        }

    review_followup = order.get("review_followup")
    if not isinstance(review_followup, dict):
        review_followup = _base_review_followup_payload()
    if review_followup.get("requested") and not force:
        return {
            "status": "ok",
            "order_id": resolved_order_id,
            "order": order,
            "review_followup": review_followup,
            "note": "review_followup_already_sent",
        }

    customer = order.get("customer", {})
    phone = _clean_str(customer.get("phone")) if isinstance(customer, dict) else ""
    if not phone:
        return {
            "status": "error",
            "error": "Customer phone is required for review follow-up.",
            "code": "ORDER_REVIEW_CONTACT_MISSING",
            "order_id": resolved_order_id,
        }

    resolved_message = _clean_str(message) or _review_message_for(resolved_order_id)
    sender_id = resolve_sms_sender_id_from_state(getattr(tool_context, "state", {}))
    sms_sent = await _send_sms_message(phone, resolved_message, sender_id=sender_id)
    whatsapp_sent = await _send_whatsapp_message(phone, resolved_message)

    if not sms_sent and not whatsapp_sent:
        return {
            "status": "error",
            "error": "No follow-up channel succeeded (SMS/WhatsApp).",
            "code": "ORDER_REVIEW_NOTIFICATION_FAILED",
            "order_id": resolved_order_id,
        }

    now = _now_iso()
    review_followup.update(
        {
            "requested": True,
            "requested_at": now,
            "message": resolved_message,
            "sms_sent": sms_sent,
            "whatsapp_sent": whatsapp_sent,
        }
    )
    order["review_followup"] = review_followup
    order["updated_at"] = now

    saved_mode = await _write_order_record(resolved_order_id, order, tool_context)
    if saved_mode == "scope_error":
        return {
            "status": "error",
            "error": "Order scope is not available for this session context.",
            "code": "ORDER_SCOPE_UNAVAILABLE",
            "order_id": resolved_order_id,
        }

    return {
        "status": "ok",
        "order_id": resolved_order_id,
        "order": order,
        "review_followup": review_followup,
        "storage": saved_mode,
    }


async def update_order_tracking_status(
    order_id: str,
    tracking_status: str,
    provider_status: str | None = None,
    provider_tracking_id: str | None = None,
    provider_shipment_id: str | None = None,
    provider: str | None = None,
    event_description: str | None = None,
    location: str | None = None,
    event_timestamp: str | None = None,
    provider_events: list[dict[str, Any]] | None = None,
    estimated_delivery_at: str | None = None,
    delivered_at: str | None = None,
    trigger_review_followup: bool = True,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Update order tracking state and trigger review follow-up after delivery."""
    resolved_order_id = _validated_order_id(order_id)
    if not resolved_order_id:
        return {
            "status": "error",
            "error": "order_id format is invalid.",
            "code": "ORDER_INVALID",
        }
    order, storage_mode = await _read_order_record(resolved_order_id, tool_context)
    if storage_mode == "scope_error":
        return {
            "status": "error",
            "error": "Order scope is not available for this session context.",
            "code": "ORDER_SCOPE_UNAVAILABLE",
            "order_id": resolved_order_id,
        }
    if order is None:
        return {
            "status": "error",
            "error": f"Order '{resolved_order_id}' not found.",
            "code": "ORDER_NOT_FOUND",
            "order_id": resolved_order_id,
        }

    now = _now_iso()
    tracking = order.get("tracking")
    if not isinstance(tracking, dict):
        tracking = _base_tracking_payload(provider=provider or "topship")

    normalized_status = _tracking_status_or_default(tracking_status or provider_status)
    resolved_provider_status = _clean_str(provider_status) or _clean_str(tracking_status)

    if provider is not None:
        tracking["provider"] = _clean_str(provider).lower() or _clean_str(tracking.get("provider")) or "topship"
    tracking["status"] = normalized_status
    tracking["provider_status"] = resolved_provider_status
    if provider_tracking_id is not None:
        tracking["tracking_id"] = _clean_str(provider_tracking_id)
    if provider_shipment_id is not None:
        tracking["shipment_id"] = _clean_str(provider_shipment_id)
    if estimated_delivery_at is not None:
        tracking["estimated_delivery_at"] = estimated_delivery_at
    if delivered_at is not None:
        tracking["delivered_at"] = delivered_at
    elif normalized_status == "delivered" and not tracking.get("delivered_at"):
        tracking["delivered_at"] = now

    events = tracking.get("events")
    if not isinstance(events, list):
        events = []
    events = [event for event in events if isinstance(event, dict)]
    if isinstance(provider_events, list) and provider_events:
        merged_events = []
        for raw_event in provider_events:
            if not isinstance(raw_event, dict):
                continue
            raw_status = _clean_str(raw_event.get("provider_status")) or _clean_str(raw_event.get("status"))
            merged_events.append(
                _tracking_event(
                    tracking_status=_tracking_status_or_default(raw_event.get("status") or raw_status or normalized_status),
                    provider_status=raw_status or resolved_provider_status,
                    event_description=_clean_str(raw_event.get("description")) or raw_status or resolved_provider_status,
                    location=_clean_str(raw_event.get("location")),
                    event_timestamp=_clean_str(raw_event.get("timestamp")) or now,
                )
            )
        events = merged_events or events
    else:
        events.insert(
            0,
            _tracking_event(
                tracking_status=normalized_status,
                provider_status=resolved_provider_status,
                event_description=_clean_str(event_description) or resolved_provider_status or normalized_status,
                location=_clean_str(location),
                event_timestamp=_clean_str(event_timestamp) or now,
            ),
        )

    events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    tracking["events"] = events[:50]
    tracking["updated_at"] = now
    tracking["last_checked_at"] = now

    order["tracking"] = tracking
    order["status"] = _resolve_order_status_from_tracking(normalized_status)
    order["updated_at"] = now
    saved_mode = await _write_order_record(resolved_order_id, order, tool_context)
    if saved_mode == "scope_error":
        return {
            "status": "error",
            "error": "Order scope is not available for this session context.",
            "code": "ORDER_SCOPE_UNAVAILABLE",
            "order_id": resolved_order_id,
        }

    followup_result: dict[str, Any] | None = None
    if normalized_status == "delivered" and trigger_review_followup:
        followup_result = await send_order_review_followup(
            order_id=resolved_order_id,
            force=False,
            tool_context=tool_context,
        )
        if followup_result.get("status") == "ok" and isinstance(followup_result.get("order"), dict):
            order = followup_result["order"]

    return {
        "status": "ok",
        "order_id": resolved_order_id,
        "order": order,
        "tracking": order.get("tracking", tracking),
        "review_followup": order.get("review_followup", _base_review_followup_payload()),
        "storage": saved_mode,
        "followup_result": followup_result,
    }


async def track_order_delivery(
    order_id: str,
    refresh_from_provider: bool = True,
    provider_tracking_id: str | None = None,
    trigger_review_followup: bool = True,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Return current order tracking and optionally refresh from Topship."""
    resolved_order_id = _validated_order_id(order_id)
    if not resolved_order_id:
        return {
            "status": "error",
            "error": "order_id format is invalid.",
            "code": "ORDER_INVALID",
            "order_id": _clean_str(order_id),
        }
    order, storage_mode = await _read_order_record(resolved_order_id, tool_context)
    if storage_mode == "scope_error":
        return {
            "status": "error",
            "error": "Order scope is not available for this session context.",
            "code": "ORDER_SCOPE_UNAVAILABLE",
            "order_id": resolved_order_id,
        }
    if order is None:
        return {
            "status": "error",
            "error": f"Order '{resolved_order_id}' not found.",
            "code": "ORDER_NOT_FOUND",
            "order_id": resolved_order_id,
        }

    tracking = order.get("tracking", {})
    if not isinstance(tracking, dict):
        tracking = _base_tracking_payload()
        order["tracking"] = tracking

    provider = _clean_str(tracking.get("provider")).lower() or "topship"
    tracking_id = _clean_str(provider_tracking_id) or _clean_str(tracking.get("tracking_id"))
    provider_result: dict[str, Any] | None = None

    if refresh_from_provider and provider == "topship" and tracking_id:
        provider_result = await _fetch_topship_tracking(tracking_id)
        if provider_result.get("status") == "ok":
            updated = await update_order_tracking_status(
                order_id=resolved_order_id,
                tracking_status=str(provider_result.get("normalized_status") or "pending"),
                provider_status=str(provider_result.get("provider_status") or ""),
                provider_tracking_id=str(provider_result.get("tracking_id") or tracking_id),
                provider=provider,
                provider_events=provider_result.get("events") if isinstance(provider_result.get("events"), list) else None,
                estimated_delivery_at=str(provider_result.get("estimated_delivery_at") or "") or None,
                delivered_at=str(provider_result.get("delivered_at") or "") or None,
                trigger_review_followup=trigger_review_followup,
                tool_context=tool_context,
            )
            if updated.get("status") == "ok" and isinstance(updated.get("order"), dict):
                order = updated["order"]
                tracking = order.get("tracking", tracking)
        else:
            tracking["last_checked_at"] = _now_iso()
            order["tracking"] = tracking
            await _write_order_record(resolved_order_id, order, tool_context)

    return {
        "status": "ok",
        "order_id": resolved_order_id,
        "order": order,
        "tracking": tracking,
        "provider_tracking": provider_result,
    }


def reset_shipping_state() -> None:
    """Reset in-memory shipping state for tests."""
    with _order_lock:
        _order_records.clear()
