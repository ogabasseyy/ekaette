"""Tests for industry config loader — TDD for S6."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLoadIndustryConfig:
    """Test loading industry configs from Firestore."""

    @pytest.mark.asyncio
    async def test_loads_electronics_config(self, sample_electronics_config):
        """Should load electronics config from Firestore."""
        from app.configs.industry_loader import load_industry_config

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = sample_electronics_config

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        config = await load_industry_config(mock_db, "electronics")

        mock_db.collection.assert_called_once_with("industry_configs")
        mock_collection.document.assert_called_once_with("electronics")
        assert config["name"] == "Electronics & Gadgets"
        assert config["voice"] == "Aoede"
        assert "pricing" in config

    @pytest.mark.asyncio
    async def test_loads_hotel_config(self, sample_hotel_config):
        """Should load hotel config from Firestore."""
        from app.configs.industry_loader import load_industry_config

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = sample_hotel_config

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        config = await load_industry_config(mock_db, "hotel")

        assert config["name"] == "Hotels & Hospitality"
        assert config["voice"] == "Puck"
        assert "room_types" in config

    @pytest.mark.asyncio
    async def test_returns_default_for_unknown_industry(self):
        """Should return default config when industry doc doesn't exist."""
        from app.configs.industry_loader import load_industry_config

        mock_doc = MagicMock()
        mock_doc.exists = False

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        config = await load_industry_config(mock_db, "nonexistent")

        assert config["name"] == "General"
        assert "voice" in config
        assert "greeting" in config

    @pytest.mark.asyncio
    async def test_returns_default_on_firestore_error(self):
        """Should return default config when Firestore fails."""
        from app.configs.industry_loader import load_industry_config

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(side_effect=Exception("Firestore unavailable"))

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        config = await load_industry_config(mock_db, "electronics")

        assert config["name"] == "Electronics & Gadgets"

    @pytest.mark.asyncio
    async def test_uses_local_fallback_for_known_industry_without_firestore(self):
        """When Firestore is unavailable, known industries should still differ."""
        from app.configs.industry_loader import load_industry_config

        electronics = await load_industry_config(None, "electronics")
        hotel = await load_industry_config(None, "hotel")

        assert electronics["voice"] == "Aoede"
        assert hotel["voice"] == "Puck"

    @pytest.mark.asyncio
    async def test_uses_general_default_for_unknown_industry_without_firestore(self):
        """Unknown industries should still map to generic default fallback."""
        from app.configs.industry_loader import load_industry_config

        config = await load_industry_config(None, "nonexistent")
        assert config["name"] == "General"

    @pytest.mark.asyncio
    async def test_registry_enabled_prefers_registry_template_adapter(self, monkeypatch):
        """Phase 1: when registry is enabled, adapt template -> legacy config shape."""
        from app.configs.industry_loader import load_industry_config

        monkeypatch.setenv("REGISTRY_ENABLED", "true")
        mock_db = MagicMock()

        with patch(
            "app.configs.registry_loader.load_industry_template",
            AsyncMock(
                return_value={
                    "id": "telecom",
                    "label": "Telecom Support",
                    "default_voice": "Puck",
                    "greeting_policy": "Welcome to telecom support.",
                }
            ),
        ) as mock_registry_load:
            config = await load_industry_config(mock_db, "telecom")

        mock_registry_load.assert_awaited_once_with(mock_db, "telecom")
        # Registry path should short-circuit legacy industry_configs collection lookup.
        mock_db.collection.assert_not_called()
        assert config == {
            "name": "Telecom Support",
            "voice": "Puck",
            "greeting": "Welcome to telecom support.",
        }

    @pytest.mark.asyncio
    async def test_registry_enabled_raises_when_template_missing(self, monkeypatch):
        """Phase 7 cutover: registry miss raises RegistryDataMissingError (no silent fallback)."""
        from app.configs.industry_loader import RegistryDataMissingError, load_industry_config

        monkeypatch.setenv("REGISTRY_ENABLED", "true")

        mock_db = MagicMock()
        mock_db.collection.return_value = MagicMock()

        with patch(
            "app.configs.registry_loader.load_industry_template",
            AsyncMock(return_value=None),
        ) as mock_registry_load:
            with pytest.raises(RegistryDataMissingError, match="not found"):
                await load_industry_config(mock_db, "hotel")

        mock_registry_load.assert_awaited_once_with(mock_db, "hotel")


class TestInjectConfigToSessionState:
    """Test injecting config into session state with app: prefix."""

    def test_injects_config_with_app_prefix(self, sample_electronics_config):
        """Config should be stored under app:industry_config key."""
        from app.configs.industry_loader import build_session_state

        state = build_session_state(sample_electronics_config, "electronics")

        assert "app:industry_config" in state
        assert state["app:industry_config"]["name"] == "Electronics & Gadgets"
        assert state["app:industry"] == "electronics"

    def test_state_uses_key_prefixes(self, sample_electronics_config):
        """All state keys should use proper prefixes."""
        from app.configs.industry_loader import build_session_state

        state = build_session_state(sample_electronics_config, "electronics")

        for key in state:
            assert key.startswith("app:") or key.startswith("user:") or key.startswith("temp:"), (
                f"State key '{key}' missing required prefix"
            )

    def test_includes_voice_config(self, sample_electronics_config):
        """Voice config should be accessible from state."""
        from app.configs.industry_loader import build_session_state

        state = build_session_state(sample_electronics_config, "electronics")

        assert state["app:voice"] == "Aoede"

    def test_includes_greeting(self, sample_electronics_config):
        """Greeting should be accessible from state."""
        from app.configs.industry_loader import build_session_state

        state = build_session_state(sample_electronics_config, "electronics")

        assert "app:greeting" in state
        assert "trade-ins" in state["app:greeting"]
