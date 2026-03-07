"""Shared test fixtures for Ekaette backend tests."""

import os
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

# WhatsApp settings module validates at import time. Provide safe defaults
# so any test that transitively imports the AT package doesn't crash.
os.environ.setdefault("WA_TASKS_INVOKER_EMAIL", "test@example.com")
os.environ.setdefault("WA_CLOUD_TASKS_AUDIENCE", "https://test.example.com")
os.environ.setdefault("WA_REPLAY_BUCKET", "test-bucket")


@pytest.fixture(autouse=True)
def _default_registry_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default REGISTRY_ENABLED=false for all tests.

    After Phase 7 cutover, production defaults to REGISTRY_ENABLED=true.
    Existing tests were written for legacy (non-registry) mode, so we default
    to false here. Tests that need registry mode explicitly set it via
    monkeypatch.setenv("REGISTRY_ENABLED", "true").
    """
    monkeypatch.setenv("REGISTRY_ENABLED", "false")


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


# ═══ Phase 0 Shared Builders (Registry Migration) ═══


def make_session_state(
    industry: str = "electronics",
    company_id: str = "ekaette-electronics",
    *,
    include_config: bool = True,
    extra: dict | None = None,
) -> dict[str, object]:
    """Build a realistic session state dict for tests.

    Mirrors the state created by build_session_state() + build_company_session_state()
    in the production code paths (main.py session init).
    """
    from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

    config = deepcopy(
        LOCAL_INDUSTRY_CONFIGS.get(
            industry,
            {
                "name": industry.title(),
                "voice": "Aoede",
                "greeting": f"Welcome to {industry}.",
            },
        )
    )

    state: dict[str, object] = {
        "app:industry": industry,
        "app:voice": config.get("voice", "Aoede"),
        "app:greeting": config.get("greeting", ""),
        "app:company_id": company_id,
        "app:company_profile": {"name": f"Test {company_id}", "overview": "Test company"},
        "app:company_knowledge": [],
    }
    if include_config:
        state["app:industry_config"] = config
    if extra:
        state.update(extra)
    return state


def make_ws_message(msg_type: str, **fields: object) -> dict[str, object]:
    """Build a WebSocket server message dict for tests.

    Mirrors the JSON messages sent from backend to frontend.
    """
    message: dict[str, object] = {"type": msg_type}
    message.update(fields)
    return message


@pytest.fixture
def electronics_session_state():
    """Pre-built session state for electronics industry."""
    return make_session_state("electronics", "ekaette-electronics")


@pytest.fixture
def hotel_session_state():
    """Pre-built session state for hotel industry."""
    return make_session_state(
        "hotel",
        "ekaette-hotel",
        extra={"app:tenant_id": "public"},
    )


# ═══ Factory Fixtures (2026 pattern) ═══


@pytest.fixture
def product_factory():
    """Factory fixture for generating valid product dicts with overrides."""
    _counter = 0

    def _make(**overrides):
        nonlocal _counter
        _counter += 1
        defaults = {
            "id": f"prod-test-{_counter}",
            "name": f"Test Product {_counter}",
            "price": 100000,
            "currency": "NGN",
            "category": "test",
            "brand": "TestBrand",
            "in_stock": True,
            "features": ["feature-a"],
            "data_tier": "demo",
        }
        defaults.update(overrides)
        return defaults

    return _make


@pytest.fixture
def booking_slot_factory():
    """Factory fixture for generating valid booking slot dicts with overrides."""
    _counter = 0

    def _make(**overrides):
        nonlocal _counter
        _counter += 1
        defaults = {
            "id": f"slot-test-{_counter}",
            "date": "2026-03-15",
            "time": f"{10 + (_counter % 8):02d}:00",
            "location": "Lagos - Ikeja",
            "available": True,
            "data_tier": "demo",
        }
        defaults.update(overrides)
        return defaults

    return _make


@pytest.fixture
def knowledge_entry_factory():
    """Factory fixture for generating valid knowledge entry dicts with overrides."""
    _counter = 0

    def _make(**overrides):
        nonlocal _counter
        _counter += 1
        defaults = {
            "id": f"kb-test-{_counter}",
            "title": f"Test Knowledge {_counter}",
            "text": f"Test knowledge content for entry {_counter}.",
            "tags": ["test"],
            "source": "factory",
        }
        defaults.update(overrides)
        return defaults

    return _make
