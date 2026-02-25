"""File-backed session service built on ADK InMemorySessionService.

Used as a graceful local fallback when SQL-backed DatabaseSessionService
dependencies are unavailable. Persists session/app/user state snapshots to disk
so reconnects and local restarts can retain context.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from google.adk.events import Event
from google.adk.sessions import Session
from google.adk.sessions.in_memory_session_service import InMemorySessionService

logger = logging.getLogger(__name__)


class PersistentInMemorySessionService(InMemorySessionService):
    """InMemorySessionService with JSON snapshot persistence."""

    def __init__(self, file_path: str = "./.data/ekaette_sessions_snapshot.json"):
        super().__init__()
        self._file_path = Path(file_path).expanduser()
        if not self._file_path.is_absolute():
            self._file_path = Path.cwd() / self._file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._io_lock = asyncio.Lock()
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self._file_path.exists():
            return
        try:
            payload = json.loads(self._file_path.read_text(encoding="utf-8"))
            self.app_state = payload.get("app_state", {})
            self.user_state = payload.get("user_state", {})
            raw_sessions = payload.get("sessions", {})
            self.sessions = {}
            for app_name, users in raw_sessions.items():
                self.sessions.setdefault(app_name, {})
                for user_id, by_session in users.items():
                    self.sessions[app_name].setdefault(user_id, {})
                    for session_id, raw_session in by_session.items():
                        self.sessions[app_name][user_id][session_id] = Session.model_validate(
                            raw_session
                        )
            logger.info(
                "Loaded persisted session snapshot from %s", self._file_path
            )
        except Exception as exc:
            logger.warning("Failed to load session snapshot: %s", exc)

    async def _save_to_disk(self) -> None:
        async with self._io_lock:
            sessions_payload: dict[str, dict[str, dict[str, Any]]] = {}
            for app_name, users in self.sessions.items():
                sessions_payload.setdefault(app_name, {})
                for user_id, by_session in users.items():
                    sessions_payload[app_name].setdefault(user_id, {})
                    for session_id, session in by_session.items():
                        sessions_payload[app_name][user_id][session_id] = session.model_dump(
                            mode="json"
                        )

            payload = {
                "app_state": self.app_state,
                "user_state": self.user_state,
                "sessions": sessions_payload,
            }
            temp_path = self._file_path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
                encoding="utf-8",
            )
            temp_path.replace(self._file_path)

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        session = await super().create_session(
            app_name=app_name,
            user_id=user_id,
            state=state,
            session_id=session_id,
        )
        await self._save_to_disk()
        return session

    async def append_event(self, session: Session, event: Event) -> Event:
        persisted = await super().append_event(session=session, event=event)
        if not event.partial:
            await self._save_to_disk()
        return persisted

    async def delete_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        await super().delete_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        await self._save_to_disk()
