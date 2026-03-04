"""Tests for ADK events compaction factory — TDD."""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestCreateCompactionConfig:
    """Test compaction config creation with env-driven switching."""

    def test_returns_none_when_disabled(self):
        """COMPACTION_ENABLED=false should return None."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config is None

    def test_returns_none_by_default_when_env_missing(self):
        """Compaction should be opt-in; missing env var means disabled."""
        from app.configs.compaction_factory import create_compaction_config

        with patch.dict(os.environ, {}, clear=True):
            config = create_compaction_config()

        assert config is None

    def test_returns_config_when_enabled(self):
        """COMPACTION_ENABLED=true should return EventsCompactionConfig."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        from google.adk.apps.app import EventsCompactionConfig

        assert isinstance(config, EventsCompactionConfig)

    def test_default_compaction_interval(self):
        """Default compaction_interval should be 5 (production tuned)."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.compaction_interval == 5

    def test_default_overlap_size(self):
        """Default overlap_size should be 1."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.overlap_size == 1

    def test_custom_compaction_interval_from_env(self):
        """COMPACTION_INTERVAL env var should override default."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true", "COMPACTION_INTERVAL": "10"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.compaction_interval == 10

    def test_custom_overlap_size_from_env(self):
        """COMPACTION_OVERLAP_SIZE env var should override default."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true", "COMPACTION_OVERLAP_SIZE": "2"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.overlap_size == 2

    def test_invalid_interval_falls_back_to_default(self):
        """Non-integer COMPACTION_INTERVAL should use default."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true", "COMPACTION_INTERVAL": "abc"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.compaction_interval == 5

    def test_summarizer_uses_configured_model(self):
        """Summarizer should use COMPACTION_MODEL env var when set."""
        from google.adk.apps.base_events_summarizer import BaseEventsSummarizer
        from app.configs.compaction_factory import create_compaction_config

        env = {
            "COMPACTION_ENABLED": "true",
            "COMPACTION_MODEL": "gemini-3-flash-preview",
        }
        mock_summarizer = MagicMock(spec=BaseEventsSummarizer)
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "app.configs.compaction_factory._create_summarizer"
            ) as mock_create:
                mock_create.return_value = mock_summarizer
                config = create_compaction_config()

        mock_create.assert_called_once_with("gemini-3-flash-preview")
        assert config.summarizer is mock_summarizer

    def test_summarizer_none_when_model_not_set(self):
        """Without COMPACTION_MODEL, summarizer should be None (use agent default)."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.summarizer is None

    def test_token_threshold_from_env(self):
        """COMPACTION_TOKEN_THRESHOLD + COMPACTION_EVENT_RETENTION_SIZE should set both."""
        from app.configs.compaction_factory import create_compaction_config

        env = {
            "COMPACTION_ENABLED": "true",
            "COMPACTION_TOKEN_THRESHOLD": "50000",
            "COMPACTION_EVENT_RETENTION_SIZE": "10",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.token_threshold == 50000
        assert config.event_retention_size == 10

    def test_token_threshold_ignored_without_retention_size(self):
        """token_threshold without event_retention_size should set neither."""
        from app.configs.compaction_factory import create_compaction_config

        env = {
            "COMPACTION_ENABLED": "true",
            "COMPACTION_TOKEN_THRESHOLD": "50000",
        }
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.token_threshold is None
        assert config.event_retention_size is None

    def test_token_threshold_none_by_default(self):
        """token_threshold should be None by default."""
        from app.configs.compaction_factory import create_compaction_config

        env = {"COMPACTION_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True):
            config = create_compaction_config()

        assert config.token_threshold is None


class TestCreateApp:
    """Test ADK App construction with compaction config."""

    @pytest.fixture()
    def stub_agent(self):
        """Create a minimal real Agent for App validation."""
        from google.adk.agents import Agent

        return Agent(name="test_agent", model="gemini-3-flash-preview")

    def test_creates_app_with_compaction(self, stub_agent):
        """create_app should include compaction config when enabled."""
        from app.configs.compaction_factory import create_app

        env = {"COMPACTION_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True):
            app = create_app(
                name="test_app",
                root_agent=stub_agent,
            )

        from google.adk.apps.app import App

        assert isinstance(app, App)
        assert app.events_compaction_config is not None

    def test_creates_app_without_compaction(self, stub_agent):
        """create_app should set compaction to None when disabled."""
        from app.configs.compaction_factory import create_app

        env = {"COMPACTION_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=True):
            app = create_app(
                name="test_app",
                root_agent=stub_agent,
            )

        assert app.events_compaction_config is None

    def test_app_preserves_agent(self, stub_agent):
        """create_app should set root_agent correctly."""
        from app.configs.compaction_factory import create_app

        with patch.dict(os.environ, {}, clear=True):
            app = create_app(
                name="test_app",
                root_agent=stub_agent,
            )

        assert app.root_agent is stub_agent

    def test_app_name_set(self, stub_agent):
        """create_app should set app name."""
        from app.configs.compaction_factory import create_app

        with patch.dict(os.environ, {}, clear=True):
            app = create_app(
                name="ekaette",
                root_agent=stub_agent,
            )

        assert app.name == "ekaette"


class TestCreateSummarizer:
    """Test LLM summarizer creation."""

    def test_creates_summarizer_with_model(self):
        """_create_summarizer should return LlmEventSummarizer with given model."""
        from app.configs.compaction_factory import _create_summarizer

        with patch("app.configs.compaction_factory.Gemini") as mock_gemini:
            mock_llm = MagicMock()
            mock_gemini.return_value = mock_llm
            summarizer = _create_summarizer("gemini-3-flash-preview")

        mock_gemini.assert_called_once_with(model="gemini-3-flash-preview")
        from google.adk.apps.llm_event_summarizer import LlmEventSummarizer

        assert isinstance(summarizer, LlmEventSummarizer)

    def test_returns_none_when_gemini_init_fails(self):
        """If Gemini initialization fails, return None gracefully."""
        from app.configs.compaction_factory import _create_summarizer

        with patch(
            "app.configs.compaction_factory.Gemini",
            side_effect=Exception("Gemini init failed"),
        ):
            result = _create_summarizer("gemini-3-flash-preview")

        assert result is None
