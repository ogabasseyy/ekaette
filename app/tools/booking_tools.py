"""Booking tools — availability checking, booking creation, cancellation.

Uses Firestore for slot storage. All functions are async for non-blocking
operation in the voice pipeline. Queries are tenant/company-scoped when
session state contains canonical keys.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.tools.scoped_queries import scoped_collection_or_global

logger = logging.getLogger(__name__)

_firestore_db: Any = None


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
        logger.warning("Firestore client unavailable: %s", exc)
        return None


def _generate_confirmation_id() -> str:
    """Generate a human-friendly confirmation ID."""
    return f"EKT-{uuid.uuid4().hex[:8].upper()}"


async def check_availability(
    date: str,
    location: str | None = None,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Check available booking slots for a given date.

    Args:
        date: Date string (YYYY-MM-DD).
        location: Optional location filter.
        tool_context: ADK ToolContext for tenant/company scoping.

    Returns:
        Dict with list of available slots.
    """
    db = _get_firestore_db()
    if db is None:
        return {"error": "Booking service unavailable", "slots": []}

    try:
        query = scoped_collection_or_global(db, tool_context, "booking_slots")
        if query is None:
            return {"error": "Booking service unavailable", "slots": []}
        query = query.where("date", "==", date)

        if location:
            query = query.where("location", "==", location)

        docs = await asyncio.to_thread(lambda: list(query.stream()))

        slots = []
        for doc in docs:
            slot_data = doc.to_dict()
            slot_data["id"] = doc.id
            if slot_data.get("available", False):
                slots.append(slot_data)

        return {"date": date, "slots": slots}

    except Exception as exc:
        logger.error("Availability check failed: %s", exc)
        return {"error": str(exc), "slots": []}


async def create_booking(
    slot_id: str,
    user_id: str,
    user_name: str,
    device_name: str,
    service_type: str,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Create a new booking.

    Args:
        slot_id: The slot ID to book.
        user_id: Customer user ID.
        user_name: Customer display name.
        device_name: Device being serviced.
        service_type: Type of service (e.g. "trade-in pickup").
        tool_context: ADK ToolContext for tenant/company scoping.

    Returns:
        Dict with confirmation_id and booking details.
    """
    db = _get_firestore_db()
    if db is None:
        return {"error": "Booking service unavailable"}

    try:
        slots_col = scoped_collection_or_global(db, tool_context, "booking_slots")
        bookings_col = scoped_collection_or_global(db, tool_context, "bookings")
        if slots_col is None or bookings_col is None:
            return {"error": "Booking service unavailable"}

        slot_ref = slots_col.document(slot_id)
        slot_doc = await asyncio.to_thread(slot_ref.get)
        if not slot_doc.exists:
            return {"error": f"Slot '{slot_id}' not found"}
        slot_data = slot_doc.to_dict() or {}
        if not slot_data.get("available", False):
            return {"error": f"Slot '{slot_id}' is no longer available"}

        # Extract tenant/company from context for data provenance.
        _state = getattr(tool_context, "state", {}) if tool_context else {}
        _tenant = _state.get("app:tenant_id", "")
        _company = _state.get("app:company_id", "")

        confirmation_id = _generate_confirmation_id()
        now = datetime.now(timezone.utc).isoformat()

        booking_data: dict[str, Any] = {
            "confirmation_id": confirmation_id,
            "slot_id": slot_id,
            "user_id": user_id,
            "user_name": user_name,
            "device_name": device_name,
            "service_type": service_type,
            "status": "confirmed",
            "created_at": now,
            "date": slot_data.get("date", ""),
            "time": slot_data.get("time", ""),
            "location": slot_data.get("location", ""),
        }
        if isinstance(_tenant, str) and _tenant:
            booking_data["tenant_id"] = _tenant
        if isinstance(_company, str) and _company:
            booking_data["company_id"] = _company

        # Atomic commit after availability check to reduce overbooking risk.
        booking_ref = bookings_col.document(confirmation_id)
        batch = db.batch()
        batch.set(booking_ref, booking_data)
        batch.update(slot_ref, {"available": False})
        await asyncio.to_thread(batch.commit)

        return booking_data

    except Exception as exc:
        logger.error("Booking creation failed: %s", exc)
        return {"error": str(exc)}


async def cancel_booking(
    confirmation_id: str,
    user_id: str,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Cancel an existing booking.

    Args:
        confirmation_id: Booking confirmation ID.
        user_id: User requesting cancellation (must match booking owner).
        tool_context: ADK ToolContext for tenant/company scoping.

    Returns:
        Dict with cancellation confirmation or error.
    """
    db = _get_firestore_db()
    if db is None:
        return {"error": "Booking service unavailable"}

    try:
        bookings_col = scoped_collection_or_global(db, tool_context, "bookings")
        slots_col = scoped_collection_or_global(db, tool_context, "booking_slots")
        if bookings_col is None:
            return {"error": "Booking service unavailable"}

        doc_ref = bookings_col.document(confirmation_id)
        doc = await asyncio.to_thread(doc_ref.get)

        if not doc.exists:
            return {"error": f"Booking '{confirmation_id}' not found"}

        booking = doc.to_dict()

        # Verify ownership
        if booking.get("user_id") != user_id:
            return {"error": "You can only cancel your own bookings"}

        # Verify company ownership (cross-company guard)
        _state = getattr(tool_context, "state", {}) if tool_context else {}
        caller_company = _state.get("app:company_id")
        booking_company = booking.get("company_id")
        if (
            isinstance(caller_company, str) and caller_company
            and isinstance(booking_company, str) and booking_company
            and caller_company != booking_company
        ):
            return {"error": "Cannot cancel bookings belonging to another company"}

        # Cancel the booking
        await asyncio.to_thread(lambda: doc_ref.update({"status": "cancelled"}))

        # Re-open the slot
        slot_id = booking.get("slot_id")
        if slot_id and slots_col is not None:
            slot_ref = slots_col.document(slot_id)
            await asyncio.to_thread(lambda: slot_ref.update({"available": True}))

        return {
            "confirmation_id": confirmation_id,
            "status": "cancelled",
        }

    except Exception as exc:
        logger.error("Booking cancellation failed: %s", exc)
        return {"error": str(exc)}
