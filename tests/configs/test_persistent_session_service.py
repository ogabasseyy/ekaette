"""Tests for file-backed session fallback service."""

from pathlib import Path

import pytest
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

from app.configs.persistent_session_service import PersistentInMemorySessionService


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
