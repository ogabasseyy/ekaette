"""Tests for session management — TDD for S6."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSessionStateSchema:
    """Test session state key prefix conventions."""

    def test_user_prefix_for_user_data(self):
        """User-specific data must use user: prefix."""
        from app.configs.industry_loader import build_session_state

        state = build_session_state(
            {"name": "Test", "voice": "Aoede", "greeting": "Hi"},
            "electronics",
            user_data={"name": "Chidi"},
        )

        assert state.get("user:name") == "Chidi"

    def test_app_prefix_for_config_data(self, sample_electronics_config):
        """Application config must use app: prefix."""
        from app.configs.industry_loader import build_session_state

        state = build_session_state(sample_electronics_config, "electronics")

        app_keys = [k for k in state if k.startswith("app:")]
        assert len(app_keys) >= 3  # industry, industry_config, voice, greeting

    def test_temp_prefix_for_transient_data(self):
        """Temporary/transient data must use temp: prefix."""
        from app.configs.industry_loader import build_session_state

        state = build_session_state(
            {"name": "Test", "voice": "Aoede", "greeting": "Hi"},
            "electronics",
        )

        # Temp keys are optional but when present must be prefixed
        temp_keys = [k for k in state if k.startswith("temp:")]
        invalid_transient_keys = [
            key for key in state
            if key.startswith("temp") and not key.startswith("temp:")
        ]
        assert all(key.startswith("temp:") for key in temp_keys)
        assert not invalid_transient_keys


class TestDatabaseSessionServiceIntegration:
    """Test ADK DatabaseSessionService import and basic contract."""

    def test_database_session_service_importable(self):
        """DatabaseSessionService should be importable from ADK."""
        from google.adk.sessions import DatabaseSessionService

        assert DatabaseSessionService is not None

    def test_in_memory_session_service_importable(self):
        """InMemorySessionService should still be importable as fallback."""
        from google.adk.sessions import InMemorySessionService

        assert InMemorySessionService is not None


class TestSessionServiceFactory:
    """Test the session service factory backend selection behavior."""

    def test_creates_database_service_when_database_backend_configured(self):
        """Should create DatabaseSessionService when backend is configured."""
        from app.configs.session_factory import create_session_service

        with patch.dict(
            "os.environ",
            {
                "SESSION_BACKEND": "database",
                "SESSION_DB_URL": "sqlite+aiosqlite:///:memory:",
            },
            clear=False,
        ), patch(
            "app.configs.session_factory.importlib.util.find_spec",
            return_value=object(),
        ):
            service = create_session_service()

        assert "DatabaseSessionService" in type(service).__name__

    def test_falls_back_to_persistent_in_memory_for_invalid_database_url(self):
        """Invalid db URL should trigger PersistentInMemorySessionService fallback."""
        from app.configs.session_factory import create_session_service
        from app.configs.persistent_session_service import PersistentInMemorySessionService

        with patch.dict(
            "os.environ",
            {
                "SESSION_BACKEND": "database",
                "SESSION_DB_URL": "invalid://broken-url",
            },
            clear=False,
        ), patch(
            "app.configs.session_factory.importlib.util.find_spec",
            return_value=object(),
        ):
            service = create_session_service()

        assert isinstance(service, PersistentInMemorySessionService)

    def test_falls_back_to_persistent_in_memory_when_greenlet_missing(self):
        """Missing greenlet should use PersistentInMemorySessionService fallback."""
        from app.configs.session_factory import create_session_service
        from app.configs.persistent_session_service import PersistentInMemorySessionService

        with patch.dict(
            "os.environ",
            {
                "SESSION_BACKEND": "database",
                "SESSION_DB_URL": "sqlite+aiosqlite:///:memory:",
            },
            clear=False,
        ), patch(
            "app.configs.session_factory.importlib.util.find_spec",
            return_value=None,
        ):
            service = create_session_service()

        assert isinstance(service, PersistentInMemorySessionService)

    def test_falls_back_to_in_memory_without_project(self):
        """Should fall back to InMemorySessionService when no project."""
        from app.configs.session_factory import create_session_service

        with patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": ""}, clear=False):
            service = create_session_service(force_in_memory=True)

        from google.adk.sessions import InMemorySessionService
        assert isinstance(service, InMemorySessionService)


class TestGetEffectiveAppName:
    """Test app_name resolution for vertex vs local backends."""

    def test_returns_agent_engine_id_for_vertex_backend(self):
        """Vertex backend should use AGENT_ENGINE_ID as app_name."""
        from app.configs.session_factory import get_effective_app_name

        with patch.dict(
            "os.environ",
            {"SESSION_BACKEND": "vertex", "AGENT_ENGINE_ID": "projects/123/locations/us-central1/apps/my-engine"},
            clear=False,
        ):
            assert get_effective_app_name() == "projects/123/locations/us-central1/apps/my-engine"

    def test_returns_app_name_for_database_backend(self):
        """Database backend should use friendly APP_NAME."""
        from app.configs.session_factory import get_effective_app_name

        with patch.dict(
            "os.environ",
            {"SESSION_BACKEND": "database", "APP_NAME": "ekaette"},
            clear=False,
        ):
            assert get_effective_app_name() == "ekaette"

    def test_raises_when_vertex_but_no_engine_id(self):
        """Vertex backend without AGENT_ENGINE_ID should fail fast."""
        from app.configs.session_factory import get_effective_app_name

        with patch.dict(
            "os.environ",
            {"SESSION_BACKEND": "vertex", "AGENT_ENGINE_ID": "", "APP_NAME": "ekaette"},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="SESSION_BACKEND=vertex requires AGENT_ENGINE_ID"):
                get_effective_app_name()

    def test_factory_raises_when_vertex_but_no_engine_id(self):
        """Session factory should fail fast for invalid vertex config."""
        from app.configs.session_factory import create_session_service

        with patch.dict(
            "os.environ",
            {"SESSION_BACKEND": "vertex", "AGENT_ENGINE_ID": ""},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="SESSION_BACKEND=vertex requires AGENT_ENGINE_ID"):
                create_session_service()


class TestAsyncSessionSave:
    """Test that session saves don't block the audio path."""

    @pytest.mark.asyncio
    async def test_async_save_does_not_block(self):
        """Async save via create_task should return immediately."""
        from app.configs.industry_loader import async_save_session_state

        mock_session = MagicMock()
        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=mock_session)
        mock_session_service.append_event = AsyncMock()

        # This should return immediately (non-blocking)
        task = async_save_session_state(
            mock_session_service,
            app_name="ekaette",
            user_id="test-user",
            session_id="test-session",
            state_updates={"app:last_agent": "vision_agent"},
        )

        # Should return a Task, not block
        assert isinstance(task, asyncio.Task)
        completed = await task
        assert completed is None
        mock_session_service.append_event.assert_awaited_once()
        appended_event = mock_session_service.append_event.await_args.kwargs["event"]
        assert appended_event.actions.state_delta["app:last_agent"] == "vision_agent"
