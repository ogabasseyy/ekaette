"""Tests for booking tools — TDD for S10."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


SAMPLE_SLOTS = [
    {
        "id": "slot-001",
        "date": "2026-03-01",
        "time": "10:00",
        "location": "Lagos - Ikeja",
        "available": True,
    },
    {
        "id": "slot-002",
        "date": "2026-03-01",
        "time": "14:00",
        "location": "Lagos - Ikeja",
        "available": True,
    },
    {
        "id": "slot-003",
        "date": "2026-03-01",
        "time": "10:00",
        "location": "Lagos - Lekki",
        "available": False,
    },
    {
        "id": "slot-004",
        "date": "2026-03-02",
        "time": "11:00",
        "location": "Abuja - Wuse",
        "available": True,
    },
]


class TestCheckAvailability:
    """Test availability checking for booking slots."""

    @pytest.mark.asyncio
    async def test_returns_available_slots_for_date(self):
        """Should return only available slots for requested date."""
        from app.tools.booking_tools import check_availability

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_docs = []
        for slot in SAMPLE_SLOTS:
            doc = MagicMock()
            doc.id = slot["id"]
            doc.to_dict.return_value = slot
            mock_docs.append(doc)

        mock_query.where.return_value = mock_query
        mock_query.stream.return_value = iter(mock_docs)
        mock_db.collection.return_value = mock_query

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
        ):
            result = await check_availability(date="2026-03-01")

        assert "slots" in result
        # Only available slots for that date
        available = [s for s in result["slots"] if s["available"]]
        assert len(available) >= 1

    @pytest.mark.asyncio
    async def test_returns_empty_for_fully_booked_date(self):
        """Should return empty slots list when all booked."""
        from app.tools.booking_tools import check_availability

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.stream.return_value = iter([])
        mock_db.collection.return_value = mock_query

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
        ):
            result = await check_availability(date="2026-12-25")

        assert result["slots"] == []

    @pytest.mark.asyncio
    async def test_filters_by_location(self):
        """Should filter slots by location when provided."""
        from app.tools.booking_tools import check_availability

        mock_db = MagicMock()
        mock_query = MagicMock()
        ikeja_slots = [s for s in SAMPLE_SLOTS if "Ikeja" in s["location"] and s["available"]]
        mock_docs = []
        for slot in ikeja_slots:
            doc = MagicMock()
            doc.id = slot["id"]
            doc.to_dict.return_value = slot
            mock_docs.append(doc)

        mock_query.where.return_value = mock_query
        mock_query.stream.return_value = iter(mock_docs)
        mock_db.collection.return_value = mock_query

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
        ):
            result = await check_availability(
                date="2026-03-01", location="Lagos - Ikeja"
            )

        assert "slots" in result
        for slot in result["slots"]:
            assert "Ikeja" in slot["location"]

    @pytest.mark.asyncio
    async def test_returns_error_when_db_unavailable(self):
        """Should return error when Firestore is unavailable."""
        from app.tools.booking_tools import check_availability

        with patch("app.tools.booking_tools._get_firestore_db", return_value=None):
            result = await check_availability(date="2026-03-01")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_location_fallback_returns_city_matches_when_exact_branch_missing(self):
        """Should fallback to loosely matching city tokens for voice-entered locations."""
        from app.tools.booking_tools import check_availability

        mock_db = MagicMock()
        mock_query = MagicMock()

        ikeja_slot = {
            "id": "slot-ikeja-fallback",
            "date": "2026-03-01",
            "time": "10:00",
            "location": "Lagos - Ikeja",
            "available": True,
        }
        doc = MagicMock()
        doc.id = ikeja_slot["id"]
        doc.to_dict.return_value = ikeja_slot

        mock_query.where.return_value = mock_query
        mock_query.stream.side_effect = [iter([]), iter([doc])]
        mock_db.collection.return_value = mock_query

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
        ):
            result = await check_availability(date="2026-03-01", location="Lagos, Yaba")

        assert result.get("location_fallback") is True
        assert result.get("requested_location") == "Lagos, Yaba"
        assert len(result["slots"]) == 1
        assert "Lagos" in result["slots"][0]["location"]


class TestCreateBooking:
    """Test booking creation."""

    @staticmethod
    def _build_db_for_slot(slot_data):
        mock_db = MagicMock()

        slot_doc = MagicMock()
        slot_doc.exists = True
        slot_doc.to_dict.return_value = slot_data

        slot_ref = MagicMock()
        slot_ref.get.return_value = slot_doc

        bookings_collection = MagicMock()
        booking_ref = MagicMock()
        bookings_collection.document.return_value = booking_ref

        slots_collection = MagicMock()
        slots_collection.document.return_value = slot_ref

        def collection_side_effect(name):
            if name == "booking_slots":
                return slots_collection
            if name == "bookings":
                return bookings_collection
            return MagicMock()

        mock_db.collection.side_effect = collection_side_effect
        mock_tx = MagicMock()
        mock_db.transaction.return_value = mock_tx
        return mock_db, slot_ref, booking_ref, mock_tx

    @pytest.mark.asyncio
    async def test_creates_booking_with_confirmation_id(self):
        """Should create booking and return confirmation ID."""
        from app.tools.booking_tools import create_booking

        slot_data = {
            "date": "2026-03-01",
            "time": "10:00",
            "location": "Lagos - Ikeja",
            "available": True,
        }
        mock_db, slot_ref, booking_ref, mock_tx = self._build_db_for_slot(slot_data)

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
            patch("google.cloud.firestore.transactional", side_effect=lambda fn: fn),
        ):
            result = await create_booking(
                slot_id="slot-001",
                user_id="test-user",
                user_name="Chidi",
                device_name="iPhone 14 Pro",
                service_type="trade-in pickup",
            )

        assert "confirmation_id" in result
        assert result["user_name"] == "Chidi"
        assert result["device_name"] == "iPhone 14 Pro"
        assert result["service_type"] == "trade-in pickup"
        assert result["date"] == "2026-03-01"
        assert result["time"] == "10:00"
        assert result["location"] == "Lagos - Ikeja"
        mock_tx.set.assert_called_once_with(booking_ref, result)
        mock_tx.update.assert_called_once_with(slot_ref, {"available": False})

    @pytest.mark.asyncio
    async def test_returns_booking_details(self):
        """Should return all booking details in result."""
        from app.tools.booking_tools import create_booking

        slot_data = {
            "date": "2026-03-02",
            "time": "11:00",
            "location": "Abuja - Wuse",
            "available": True,
        }
        mock_db, _, _, _ = self._build_db_for_slot(slot_data)

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
            patch("google.cloud.firestore.transactional", side_effect=lambda fn: fn),
        ):
            result = await create_booking(
                slot_id="slot-002",
                user_id="test-user",
                user_name="Amaka",
                device_name="Samsung S24",
                service_type="trade-in pickup",
            )

        assert "confirmation_id" in result
        assert "slot_id" in result
        assert result["slot_id"] == "slot-002"
        assert result["location"] == "Abuja - Wuse"

    @pytest.mark.asyncio
    async def test_rejects_unavailable_slot(self):
        """Should reject booking when slot is already unavailable."""
        from app.tools.booking_tools import create_booking

        slot_data = {
            "date": "2026-03-01",
            "time": "10:00",
            "location": "Lagos - Ikeja",
            "available": False,
        }
        mock_db, _, _, mock_tx = self._build_db_for_slot(slot_data)

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
            patch("google.cloud.firestore.transactional", side_effect=lambda fn: fn),
        ):
            result = await create_booking(
                slot_id="slot-001",
                user_id="test-user",
                user_name="Test",
                device_name="iPhone",
                service_type="pickup",
            )

        assert "error" in result
        mock_tx.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_error_when_db_unavailable(self):
        """Should return error when Firestore is unavailable."""
        from app.tools.booking_tools import create_booking

        with patch("app.tools.booking_tools._get_firestore_db", return_value=None):
            result = await create_booking(
                slot_id="slot-001",
                user_id="test-user",
                user_name="Test",
                device_name="iPhone",
                service_type="pickup",
            )

        assert "error" in result


class TestCancelBooking:
    """Test booking cancellation."""

    @pytest.mark.asyncio
    async def test_cancels_booking_by_id(self):
        """Should cancel booking and return confirmation."""
        from app.tools.booking_tools import cancel_booking

        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "confirmation_id": "EKT-ABC123",
            "user_id": "test-user",
            "slot_id": "slot-001",
            "status": "confirmed",
        }
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
        ):
            result = await cancel_booking(
                confirmation_id="EKT-ABC123",
                user_id="test-user",
            )

        assert result["status"] == "cancelled"
        assert result["confirmation_id"] == "EKT-ABC123"

    @pytest.mark.asyncio
    async def test_rejects_invalid_booking_id(self):
        """Should return error for non-existent booking."""
        from app.tools.booking_tools import cancel_booking

        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
        ):
            result = await cancel_booking(
                confirmation_id="INVALID-ID",
                user_id="test-user",
            )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_rejects_wrong_user(self):
        """Should reject cancellation if user_id doesn't match."""
        from app.tools.booking_tools import cancel_booking

        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "confirmation_id": "EKT-ABC123",
            "user_id": "original-user",
            "slot_id": "slot-001",
            "status": "confirmed",
        }
        mock_doc_ref.get.return_value = mock_doc
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with (
            patch("app.tools.booking_tools._get_firestore_db", return_value=mock_db),
            patch(
                "app.tools.booking_tools.scoped_collection",
                side_effect=lambda db, _ctx, name: mock_db.collection(name),
            ),
        ):
            result = await cancel_booking(
                confirmation_id="EKT-ABC123",
                user_id="different-user",
            )

        assert "error" in result
