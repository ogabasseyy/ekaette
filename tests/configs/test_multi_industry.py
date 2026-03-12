"""Tests for multi-industry config switching — TDD for S12.

Verifies that all 4 industries (electronics, hotel, automotive, fashion)
have local fallback configs with correct voice personas and greetings.
"""

class TestLocalIndustryConfigs:
    """All 4 industries should have local fallback configs."""

    def test_electronics_config_exists(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        assert "electronics" in LOCAL_INDUSTRY_CONFIGS
        config = LOCAL_INDUSTRY_CONFIGS["electronics"]
        assert config["voice"] == "Kore"
        assert "trade-in" in config["greeting"].lower() or "device" in config["greeting"].lower()

    def test_hotel_config_exists(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        assert "hotel" in LOCAL_INDUSTRY_CONFIGS
        config = LOCAL_INDUSTRY_CONFIGS["hotel"]
        assert config["voice"] == "Puck"
        assert "hotel" in config["greeting"].lower() or "stay" in config["greeting"].lower()

    def test_automotive_config_exists(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        assert "automotive" in LOCAL_INDUSTRY_CONFIGS
        config = LOCAL_INDUSTRY_CONFIGS["automotive"]
        assert config["voice"] == "Charon"
        assert "vehicle" in config["greeting"].lower() or "car" in config["greeting"].lower()

    def test_fashion_config_exists(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        assert "fashion" in LOCAL_INDUSTRY_CONFIGS
        config = LOCAL_INDUSTRY_CONFIGS["fashion"]
        assert config["voice"] == "Aoede"
        assert "style" in config["greeting"].lower() or "fashion" in config["greeting"].lower()


class TestConfigSwitchVoiceMapping:
    """Each industry should map to a distinct voice persona."""

    def test_all_industries_have_unique_voices(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        voices = [cfg["voice"] for cfg in LOCAL_INDUSTRY_CONFIGS.values()]
        assert len(voices) == len(set(voices)), "Voice personas should be unique per industry"

    def test_voice_map_matches_local_configs(self):
        """main.py voice map should match LOCAL_INDUSTRY_CONFIGS voices."""
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS

        expected_map = {
            "electronics": "Kore",
            "hotel": "Puck",
            "automotive": "Charon",
            "fashion": "Aoede",
        }
        for industry, expected_voice in expected_map.items():
            assert LOCAL_INDUSTRY_CONFIGS[industry]["voice"] == expected_voice


class TestConfigSwitchSessionState:
    """Switching industry should produce correct session state."""

    def test_build_state_for_electronics(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS, build_session_state

        config = LOCAL_INDUSTRY_CONFIGS["electronics"]
        state = build_session_state(config, "electronics")
        assert state["app:industry"] == "electronics"
        assert state["app:voice"] == "Kore"
        assert state["app:industry_config"] == config

    def test_build_state_for_hotel(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS, build_session_state

        config = LOCAL_INDUSTRY_CONFIGS["hotel"]
        state = build_session_state(config, "hotel")
        assert state["app:industry"] == "hotel"
        assert state["app:voice"] == "Puck"

    def test_build_state_for_automotive(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS, build_session_state

        config = LOCAL_INDUSTRY_CONFIGS["automotive"]
        state = build_session_state(config, "automotive")
        assert state["app:industry"] == "automotive"
        assert state["app:voice"] == "Charon"

    def test_build_state_for_fashion(self):
        from app.configs.industry_loader import LOCAL_INDUSTRY_CONFIGS, build_session_state

        config = LOCAL_INDUSTRY_CONFIGS["fashion"]
        state = build_session_state(config, "fashion")
        assert state["app:industry"] == "fashion"
        assert state["app:voice"] == "Aoede"

    def test_fallback_returns_default_for_unknown(self):
        from app.configs.industry_loader import _fallback_config_for, DEFAULT_CONFIG

        config = _fallback_config_for("unknown_industry")
        assert config["voice"] == DEFAULT_CONFIG["voice"]
        assert config["greeting"] == DEFAULT_CONFIG["greeting"]

    def test_fallback_returns_automotive_for_automotive(self):
        from app.configs.industry_loader import _fallback_config_for

        config = _fallback_config_for("automotive")
        assert config["voice"] == "Charon"

    def test_fallback_returns_fashion_for_fashion(self):
        from app.configs.industry_loader import _fallback_config_for

        config = _fallback_config_for("fashion")
        assert config["voice"] == "Aoede"
