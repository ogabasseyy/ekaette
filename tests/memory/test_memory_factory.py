"""Tests for memory service factory — TDD for S12."""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestCreateMemoryService:
    """Test memory service factory with backend switching."""

    def test_returns_in_memory_by_default(self):
        """Without GCP config, should return InMemoryMemoryService."""
        from app.memory.memory_factory import create_memory_service

        with patch.dict(os.environ, {}, clear=True):
            service = create_memory_service()

        from google.adk.memory import InMemoryMemoryService

        assert isinstance(service, InMemoryMemoryService)

    def test_returns_vertex_when_configured(self):
        """With AGENT_ENGINE_ID + project, should return VertexAiMemoryBankService."""
        from app.memory.memory_factory import create_memory_service

        env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "AGENT_ENGINE_ID": "12345",
            "MEMORY_BACKEND": "vertex",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "google.adk.memory.VertexAiMemoryBankService"
            ) as mock_cls:
                mock_cls.return_value = MagicMock()
                create_memory_service()

        mock_cls.assert_called_once_with(
            project="test-project",
            location="us-central1",
            agent_engine_id="12345",
        )

    def test_auto_backend_uses_vertex_when_engine_id_exists(self):
        """Default auto mode should use Vertex when AGENT_ENGINE_ID is available."""
        from app.memory.memory_factory import create_memory_service

        env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "AGENT_ENGINE_ID": "12345",
            # MEMORY_BACKEND intentionally omitted (auto default)
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("google.adk.memory.VertexAiMemoryBankService") as mock_cls:
                mock_cls.return_value = MagicMock()
                create_memory_service()

        mock_cls.assert_called_once_with(
            project="test-project",
            location="us-central1",
            agent_engine_id="12345",
        )

    def test_raises_on_vertex_error_when_explicit(self):
        """If MEMORY_BACKEND=vertex and VertexAi init fails, should raise RuntimeError."""
        from app.memory.memory_factory import create_memory_service

        env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "AGENT_ENGINE_ID": "12345",
            "MEMORY_BACKEND": "vertex",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "google.adk.memory.VertexAiMemoryBankService",
                side_effect=Exception("Auth failed"),
            ):
                with pytest.raises(RuntimeError, match="VertexAiMemoryBankService init failed"):
                    create_memory_service()

    def test_falls_back_to_in_memory_on_auto_vertex_error(self):
        """If MEMORY_BACKEND=auto and VertexAi init fails, should gracefully fall back to InMemory."""
        from app.memory.memory_factory import create_memory_service

        env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "AGENT_ENGINE_ID": "12345",
            "MEMORY_BACKEND": "auto",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "google.adk.memory.VertexAiMemoryBankService",
                side_effect=Exception("Auth failed"),
            ):
                service = create_memory_service()

        from google.adk.memory import InMemoryMemoryService

        assert isinstance(service, InMemoryMemoryService)

    def test_returns_in_memory_when_memory_backend_is_memory(self):
        """Explicit MEMORY_BACKEND=memory should return InMemory."""
        from app.memory.memory_factory import create_memory_service

        env = {"MEMORY_BACKEND": "memory"}
        with patch.dict(os.environ, env, clear=True):
            service = create_memory_service()

        from google.adk.memory import InMemoryMemoryService

        assert isinstance(service, InMemoryMemoryService)

    def test_raises_when_vertex_missing_agent_engine_id(self):
        """Vertex backend without AGENT_ENGINE_ID should raise RuntimeError."""
        from app.memory.memory_factory import create_memory_service

        env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "MEMORY_BACKEND": "vertex",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="AGENT_ENGINE_ID"):
                create_memory_service()


class TestMemoryServiceIntegration:
    """Test that memory service integrates with Runner correctly."""

    def test_in_memory_memory_service_has_required_methods(self):
        """InMemoryMemoryService should have search_memory and add_session_to_memory."""
        from google.adk.memory import InMemoryMemoryService

        service = InMemoryMemoryService()
        assert hasattr(service, "search_memory")
        assert hasattr(service, "add_session_to_memory")
        assert hasattr(service, "add_events_to_memory")
