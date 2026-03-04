"""Session service factory.

Default backend is ADK DatabaseSessionService using a local SQLite database for
durable session state in local/dev environments.

Optional backends:
  - memory: InMemorySessionService (explicit fallback/testing only)
  - vertex: VertexAiSessionService (managed backend when running on Vertex)

IMPORTANT (Vertex backend):
  VertexAiSessionService expects ``app_name`` in session operations to be the
  Agent Engine resource name/ID — NOT a friendly name like "ekaette".  Use
  ``get_effective_app_name()`` to resolve the correct value at runtime.
"""

import logging
import os
import importlib.util
from pathlib import Path

from google.adk.sessions import InMemorySessionService

from app.configs import sanitize_log
from app.configs.persistent_session_service import PersistentInMemorySessionService

logger = logging.getLogger(__name__)


def _safe_db_url_label(db_url: str) -> str:
    """Return a redacted DB URL label safe for logs."""
    scheme, _, _rest = db_url.partition("://")
    safe_scheme = scheme or "db"
    return sanitize_log(f"{safe_scheme}://<redacted>")


def _safe_exception_text(exc: Exception) -> str:
    return sanitize_log(str(exc))


def get_effective_app_name() -> str:
    """Return the app_name that ADK session operations should use.

    For ``vertex`` backend the ADK requires the Agent Engine resource name/ID
    as ``app_name``.  For all other backends the friendly APP_NAME is fine.
    """
    backend = os.getenv("SESSION_BACKEND", "database").strip().lower()
    if backend == "vertex":
        engine_id = os.getenv("AGENT_ENGINE_ID", "").strip()
        if engine_id:
            return engine_id
        raise RuntimeError(
            "Invalid vertex session config: SESSION_BACKEND=vertex requires "
            "AGENT_ENGINE_ID to be set."
        )
    return os.getenv("APP_NAME", "ekaette")


def create_session_service(force_in_memory: bool = False):
    """Create the configured ADK session service.

    Env controls:
      SESSION_BACKEND=database|memory|vertex (default: database)
      SESSION_DB_URL=<sqlalchemy async URL> (default: sqlite+aiosqlite:///./.data/ekaette_sessions.db)
    """
    backend = os.getenv("SESSION_BACKEND", "database").strip().lower()

    if force_in_memory or backend == "memory":
        logger.info("Using InMemorySessionService (forced or SESSION_BACKEND=memory)")
        return InMemorySessionService()

    if backend == "vertex":
        engine_id = os.getenv("AGENT_ENGINE_ID", "").strip()
        if not engine_id:
            raise RuntimeError(
                "Invalid vertex session config: SESSION_BACKEND=vertex requires "
                "AGENT_ENGINE_ID to be set."
            )
        try:
            from google.adk.sessions import VertexAiSessionService

            service = VertexAiSessionService(
                project=os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None,
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "").strip() or None,
                agent_engine_id=engine_id,
            )
            logger.info("Using VertexAiSessionService")
            return service
        except Exception as exc:
            logger.warning(
                "Failed to init VertexAiSessionService, falling back to InMemory: %s",
                _safe_exception_text(exc),
            )
            return InMemorySessionService()

    if importlib.util.find_spec("greenlet") is None:
        logger.warning(
            "greenlet is not installed; using PersistentInMemorySessionService "
            "fallback. Install project dependencies to enable SQL persistence."
        )
        return PersistentInMemorySessionService(
            file_path=os.getenv(
                "SESSION_FILE_PATH",
                "./.data/ekaette_sessions_snapshot.json",
            )
        )

    try:
        from google.adk.sessions import DatabaseSessionService

        db_url = os.getenv(
            "SESSION_DB_URL",
            "sqlite+aiosqlite:///./.data/ekaette_sessions.db",
        ).strip()
        if not db_url:
            db_url = "sqlite+aiosqlite:///./.data/ekaette_sessions.db"

        # Ensure local SQLite parent directory exists.
        sqlite_prefix = "sqlite+aiosqlite:///"
        if db_url.startswith(sqlite_prefix) and ":memory:" not in db_url:
            raw_path = db_url[len(sqlite_prefix):]
            db_path = Path(raw_path).expanduser()
            if not db_path.is_absolute():
                db_path = Path.cwd() / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)

        service = DatabaseSessionService(db_url=db_url)
        logger.info("Using DatabaseSessionService (db_url=%s)", _safe_db_url_label(db_url))
        return service
    except Exception as exc:
        logger.warning(
            "Failed to init DatabaseSessionService, using PersistentInMemory fallback: %s",
            _safe_exception_text(exc),
        )
        return PersistentInMemorySessionService(
            file_path=os.getenv(
                "SESSION_FILE_PATH",
                "./.data/ekaette_sessions_snapshot.json",
            )
        )
