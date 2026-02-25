"""Industry config loader — loads config from Firestore and builds session state."""

import asyncio
import logging
import os
from typing import Any

from google.adk.events import Event
from google.adk.events.event_actions import EventActions

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "name": "General",
    "voice": "Aoede",
    "greeting": "Hello! How can I help you today?",
}

LOCAL_INDUSTRY_CONFIGS: dict[str, dict[str, Any]] = {
    "electronics": {
        "name": "Electronics & Gadgets",
        "voice": "Aoede",
        "greeting": "Welcome! I can help you with device trade-ins, swaps, and purchases.",
    },
    "hotel": {
        "name": "Hotels & Hospitality",
        "voice": "Puck",
        "greeting": "Good day! Welcome to our hotel. How can I make your stay perfect?",
    },
    "automotive": {
        "name": "Automotive",
        "voice": "Charon",
        "greeting": "Hello! Looking to buy, sell, or service a vehicle?",
    },
    "fashion": {
        "name": "Fashion & Retail",
        "voice": "Kore",
        "greeting": "Hey there! Let me help you find your perfect style.",
    },
}


def _fallback_config_for(industry: str) -> dict[str, Any]:
    """Return local fallback config for known industries, else default."""
    key = (industry or "").strip().lower()
    if key in LOCAL_INDUSTRY_CONFIGS:
        return dict(LOCAL_INDUSTRY_CONFIGS[key])
    return dict(DEFAULT_CONFIG)


def create_industry_config_client(project: str | None = None) -> Any | None:
    """Create a Firestore async client for industry config lookups.

    Returns None when Firestore is unavailable so callers can gracefully
    fall back to DEFAULT_CONFIG.
    """
    project_id = (project or os.getenv("GOOGLE_CLOUD_PROJECT", "")).strip()
    if not project_id:
        logger.warning(
            "GOOGLE_CLOUD_PROJECT not set — using default in-memory industry config"
        )
        return None

    try:
        from google.cloud import firestore

        return firestore.AsyncClient(project=project_id)
    except Exception as exc:
        logger.warning("Failed to initialize Firestore async client: %s", exc)
        return None


async def load_industry_config(
    db: Any,
    industry: str,
) -> dict[str, Any]:
    """Load an industry config from Firestore.

    Falls back to DEFAULT_CONFIG when the document is missing or Firestore
    is unreachable — never let config loading break the voice session.
    """
    if db is None:
        logger.debug("No Firestore client — returning default config for '%s'", industry)
        return _fallback_config_for(industry)

    try:
        doc_ref = db.collection("industry_configs").document(industry)
        # Async client path.
        if asyncio.iscoroutinefunction(doc_ref.get):
            doc = await doc_ref.get()
        # Sync client path (if injected in tests or local utilities).
        else:
            doc = await asyncio.to_thread(doc_ref.get)
        if doc.exists:
            return doc.to_dict()
    except Exception as exc:
        logger.warning("Failed to load industry config '%s': %s", industry, exc)

    return _fallback_config_for(industry)


def build_session_state(
    config: dict[str, Any],
    industry: str,
    user_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ADK session state dict with proper key prefixes.

    Prefix conventions:
      - app:*   — application/industry config (read-only during session)
      - user:*  — user-specific data (persists across sessions)
      - temp:*  — transient data (cleared on session end)
    """
    state: dict[str, Any] = {
        "app:industry": industry,
        "app:industry_config": config,
        "app:voice": config.get("voice", "Aoede"),
        "app:greeting": config.get("greeting", DEFAULT_CONFIG["greeting"]),
    }

    if user_data:
        for key, value in user_data.items():
            state[f"user:{key}"] = value

    return state


def async_save_session_state(
    session_service: Any,
    app_name: str,
    user_id: str,
    session_id: str,
    state_updates: dict[str, Any],
) -> asyncio.Task:
    """Fire-and-forget session state save — never blocks the audio path.

    Returns the Task so callers can optionally await it in tests.
    """

    async def _save() -> None:
        try:
            session = await session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            if not session:
                logger.warning(
                    "Session not found for async save (app=%s user=%s session=%s)",
                    app_name,
                    user_id,
                    session_id,
                )
                return

            event = Event(
                author="system:session_state",
                actions=EventActions(state_delta=state_updates),
            )
            await session_service.append_event(session=session, event=event)
        except Exception as exc:
            logger.error("Async session save failed: %s", exc)

    return asyncio.create_task(_save())
