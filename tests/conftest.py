"""Shared test fixtures for Ekaette backend tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_genai_client():
    """Mock Google GenAI client — never hit real API in tests."""
    client = MagicMock()
    client.models.generate_content = AsyncMock(
        return_value=MagicMock(text="Mock response")
    )
    return client


@pytest.fixture
def mock_firestore_db():
    """Mock Firestore client for session and config tests."""
    db = MagicMock()
    collection = MagicMock()
    document = MagicMock()
    document.get = MagicMock(return_value=MagicMock(exists=True, to_dict=MagicMock(return_value={})))
    document.set = MagicMock()
    collection.document = MagicMock(return_value=document)
    db.collection = MagicMock(return_value=collection)
    return db


@pytest.fixture
def sample_electronics_config():
    """Sample electronics industry config for tests."""
    return {
        "name": "Electronics & Gadgets",
        "voice": "Aoede",
        "greeting": "Welcome! I can help you with device trade-ins, swaps, and purchases.",
        "rubric": {
            "categories": ["screen", "body", "battery", "functionality"],
            "scale": {"Excellent": 10, "Good": 7, "Fair": 5, "Poor": 2},
        },
        "pricing": {
            "iPhone 14 Pro": {"Excellent": 220000, "Good": 185000, "Fair": 140000, "Poor": 80000},
            "iPhone 15": {"Excellent": 280000, "Good": 240000, "Fair": 190000, "Poor": 120000},
            "Samsung S24": {"Excellent": 250000, "Good": 210000, "Fair": 165000, "Poor": 95000},
        },
    }


@pytest.fixture
def sample_hotel_config():
    """Sample hotel industry config for tests."""
    return {
        "name": "Hotels & Hospitality",
        "voice": "Puck",
        "greeting": "Good day! Welcome to our hotel. How can I make your stay perfect?",
        "room_types": ["Standard", "Deluxe", "Ocean View", "Suite"],
        "pricing": {
            "Standard": 25000,
            "Deluxe": 45000,
            "Ocean View": 65000,
            "Suite": 120000,
        },
    }


@pytest.fixture
def session_factory():
    """Factory for creating mock ADK sessions with state."""
    def _create(user_id="test-user", session_id="test-session", state=None):
        session = MagicMock()
        session.user_id = user_id
        session.id = session_id
        session.state = state or {}
        session.events = []
        return session
    return _create
