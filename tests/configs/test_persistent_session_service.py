"""Tests for file-backed session fallback service."""

import json
from pathlib import Path

import pytest
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

try:
    from app.configs.persistent_session_service import PersistentInMemorySessionService
except ImportError:
    pytest.skip("app.configs.persistent_session_service not yet implemented", allow_module_level=True)


@pytest.mark.asyncio
async def test_persistent_session_service_round_trip(tmp_path: Path):
    """Session and state updates should survive service re-instantiation."""
    snapshot = tmp_path / "sessions.json"

    service_a = PersistentInMemorySessionService(str(snapshot))
    session = await service_a.create_session(
        app_name="ekaette",
        user_id="user-1",
        session_id="session-1",
        state={
            "app:industry": "electronics",
            "user:name": "Chidi",
            "temp:stage": "intro",
        },
    )

    await service_a.append_event(
        session=session,
        event=Event(
            author="system:test",
            actions=EventActions(
                state_delta={
                    "app:industry": "hotel",
                    "user:name": "Chidi",
                    "temp:stage": "booking",
                }
            ),
        ),
    )

    service_b = PersistentInMemorySessionService(str(snapshot))
    restored = await service_b.get_session(
        app_name="ekaette",
        user_id="user-1",
        session_id="session-1",
    )

    assert restored is not None
    assert restored.state["app:industry"] == "hotel"
    assert restored.state["user:name"] == "Chidi"
    # temp:* keys are intentionally non-persistent in ADK state extraction.
    assert "temp:stage" not in restored.state


@pytest.mark.asyncio
async def test_company_state_survives_session_resumption(tmp_path: Path):
    """Company grounding state should persist across service re-instantiation."""
    snapshot = tmp_path / "sessions.json"

    service_a = PersistentInMemorySessionService(str(snapshot))
    session = await service_a.create_session(
        app_name="ekaette",
        user_id="user-42",
        session_id="session-company",
        state={
            "app:industry": "hotel",
            "app:company_id": "ekaette-hotel",
            "app:company_profile": {
                "name": "Ekaette Grand Hotel",
                "facts": {"rooms": 120},
            },
            "app:company_knowledge": [
                {
                    "id": "kb-hotel-checkout",
                    "title": "Late checkout policy",
                    "text": "Late checkout until 1 PM for premium guests.",
                }
            ],
        },
    )

    await service_a.append_event(
        session=session,
        event=Event(
            author="system:test",
            actions=EventActions(
                state_delta={
                    "app:company_id": "ekaette-hotel",
                    "app:company_profile": {
                        "name": "Ekaette Grand Hotel",
                        "facts": {"rooms": 120, "check_in_time": "14:00"},
                    },
                }
            ),
        ),
    )

    service_b = PersistentInMemorySessionService(str(snapshot))
    restored = await service_b.get_session(
        app_name="ekaette",
        user_id="user-42",
        session_id="session-company",
    )

    assert restored is not None
    assert restored.state["app:company_id"] == "ekaette-hotel"
    assert restored.state["app:company_profile"]["name"] == "Ekaette Grand Hotel"
    assert restored.state["app:company_profile"]["facts"]["check_in_time"] == "14:00"
    assert restored.state["app:company_knowledge"][0]["id"] == "kb-hotel-checkout"


@pytest.mark.asyncio
async def test_user_pii_redacted_on_disk_but_preserved_in_memory(tmp_path: Path):
    """user:* state values with PII should be redacted on disk but intact in memory.

    ADK's InMemorySessionService splits user:* keys into a top-level
    ``user_state`` dict (keyed by app_name -> user_id -> field), stripping
    the ``user:`` prefix. PII redaction must apply to those persisted values.
    """
    snapshot = tmp_path / "sessions.json"

    service = PersistentInMemorySessionService(str(snapshot))
    session = await service.create_session(
        app_name="ekaette",
        user_id="user-pii",
        session_id="session-pii",
        state={
            "app:industry": "electronics",
            "user:phone": "+2348012345678",
            "user:email": "bassey@gmail.com",
            "user:name": "Bassey",
            "app:company_id": "ekaette-electronics",
        },
    )

    # In-memory state must retain original PII values
    assert session.state["user:phone"] == "+2348012345678"
    assert session.state["user:email"] == "bassey@gmail.com"
    assert session.state["user:name"] == "Bassey"

    # On-disk user_state must have PII redacted
    raw = json.loads(snapshot.read_text(encoding="utf-8"))
    disk_user_state = raw["user_state"]["ekaette"]["user-pii"]

    # Phone and email should be masked
    assert "+2348012345678" not in disk_user_state["phone"]
    assert "***" in disk_user_state["phone"]
    assert "bassey@gmail.com" not in disk_user_state["email"]
    assert "***" in disk_user_state["email"]

    # Non-PII user values should pass through unchanged
    assert disk_user_state["name"] == "Bassey"

    # app_state keys must never be redacted
    disk_app_state = raw["app_state"]["ekaette"]
    assert disk_app_state["industry"] == "electronics"
    assert disk_app_state["company_id"] == "ekaette-electronics"


@pytest.mark.asyncio
async def test_pii_redaction_on_append_event(tmp_path: Path):
    """PII should be redacted when state_delta with user:* keys is appended."""
    snapshot = tmp_path / "sessions.json"

    service = PersistentInMemorySessionService(str(snapshot))
    session = await service.create_session(
        app_name="ekaette",
        user_id="user-delta",
        session_id="session-delta",
        state={"app:industry": "hotel"},
    )

    await service.append_event(
        session=session,
        event=Event(
            author="system:test",
            actions=EventActions(
                state_delta={
                    "user:contact": "Call me at +2348099887766 or email me@example.com",
                }
            ),
        ),
    )

    # In-memory state must keep original
    assert session.state["user:contact"] == "Call me at +2348099887766 or email me@example.com"

    raw = json.loads(snapshot.read_text(encoding="utf-8"))
    disk_user_state = raw["user_state"]["ekaette"]["user-delta"]

    # The full phone number and email should be masked on disk
    assert "+2348099887766" not in disk_user_state.get("contact", "")
    assert "me@example.com" not in disk_user_state.get("contact", "")
