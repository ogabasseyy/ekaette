"""Tests for Agent Engine provisioning script."""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestProvisionConfig:
    """Verify provisioning script configuration constants."""

    def test_memory_ttl_is_90_days(self):
        """TTL should be 90 days in seconds, matching AT call retention."""
        from scripts.provision_agent_engine import MEMORY_TTL_SECONDS

        assert MEMORY_TTL_SECONDS == 90 * 24 * 3600  # 7,776,000s

    def test_memory_bank_config_has_ttl(self):
        """Memory bank config should include TTL configuration."""
        from scripts.provision_agent_engine import MEMORY_BANK_CONFIG

        assert "ttl_config" in MEMORY_BANK_CONFIG
        assert "default_ttl" in MEMORY_BANK_CONFIG["ttl_config"]
        # TTL format: "<seconds>s"
        ttl = MEMORY_BANK_CONFIG["ttl_config"]["default_ttl"]
        assert ttl.endswith("s")
        assert int(ttl[:-1]) == 90 * 24 * 3600

    def test_engine_display_name(self):
        """Display name should be 'ekaette-memory'."""
        from scripts.provision_agent_engine import ENGINE_DISPLAY_NAME

        assert ENGINE_DISPLAY_NAME == "ekaette-memory"


class TestResolveProject:
    """Test project resolution from args, env, or default."""

    def test_arg_takes_precedence(self):
        from scripts.provision_agent_engine import _resolve_project

        assert _resolve_project("my-project") == "my-project"

    def test_env_used_when_no_arg(self):
        from scripts.provision_agent_engine import _resolve_project

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-project"}):
            assert _resolve_project(None) == "env-project"

    def test_exits_when_no_project(self):
        from scripts.provision_agent_engine import _resolve_project

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit):
                _resolve_project(None)


class TestResolveLocation:
    """Test location resolution."""

    def test_arg_takes_precedence(self):
        from scripts.provision_agent_engine import _resolve_location

        assert _resolve_location("europe-west1") == "europe-west1"

    def test_defaults_to_us_central1(self):
        from scripts.provision_agent_engine import _resolve_location

        with patch.dict(os.environ, {}, clear=True):
            assert _resolve_location(None) == "us-central1"


class TestProvisionIdempotency:
    """Test that provision() is idempotent — skips if engine exists."""

    def test_skips_when_engine_exists(self):
        """Should return existing engine ID without creating a new one."""
        from scripts.provision_agent_engine import provision

        # Build mock matching real SDK structure:
        # engine.api_resource.displayName / engine.api_resource.name
        mock_resource = MagicMock()
        mock_resource.displayName = "ekaette-memory"
        mock_resource.display_name = "ekaette-memory"
        mock_resource.name = (
            "projects/ekaette/locations/us-central1/reasoningEngines/99999"
        )

        mock_engine = MagicMock()
        mock_engine.api_resource = mock_resource
        mock_engine.apiResource = mock_resource

        mock_client = MagicMock()
        mock_client.agent_engines.list.return_value = [mock_engine]

        with patch("vertexai.Client", return_value=mock_client):
            engine_id = provision("ekaette", "us-central1")

        assert engine_id == "99999"
        mock_client.agent_engines.create.assert_not_called()

    def test_creates_when_no_engine_exists(self):
        """Should create engine when none with matching name exists."""
        from scripts.provision_agent_engine import provision

        mock_resource = MagicMock()
        mock_resource.name = (
            "projects/ekaette/locations/us-central1/reasoningEngines/12345"
        )

        mock_created = MagicMock()
        mock_created.api_resource = mock_resource
        mock_created.apiResource = mock_resource

        mock_client = MagicMock()
        mock_client.agent_engines.list.return_value = []
        mock_client.agent_engines.create.return_value = mock_created

        with patch("vertexai.Client", return_value=mock_client):
            engine_id = provision("ekaette", "us-central1")

        assert engine_id == "12345"
        mock_client.agent_engines.create.assert_called_once()

    def test_dry_run_does_not_create(self):
        """Dry run should not call create."""
        from scripts.provision_agent_engine import provision

        mock_client = MagicMock()
        mock_client.agent_engines.list.return_value = []

        with patch("vertexai.Client", return_value=mock_client):
            engine_id = provision("ekaette", "us-central1", dry_run=True)

        assert engine_id == ""
        mock_client.agent_engines.create.assert_not_called()


class TestMemoryFactoryVertexRouting:
    """Verify that memory_factory correctly routes to Vertex when AGENT_ENGINE_ID is set."""

    def test_factory_routes_to_vertex_with_engine_id(self):
        """create_memory_service should use VertexAi when AGENT_ENGINE_ID is set."""
        from app.memory.memory_factory import create_memory_service

        env = {
            "GOOGLE_CLOUD_PROJECT": "ekaette",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "AGENT_ENGINE_ID": "12345",
            "MEMORY_BACKEND": "auto",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("google.adk.memory.VertexAiMemoryBankService") as mock_cls:
                mock_cls.return_value = MagicMock()
                service = create_memory_service()

        mock_cls.assert_called_once_with(
            project="ekaette",
            location="us-central1",
            agent_engine_id="12345",
        )
        assert service is mock_cls.return_value
