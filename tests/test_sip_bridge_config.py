"""TDD tests for SIP bridge configuration.

Covers: env loading, defaults, validation, frozen immutability.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch


class TestBridgeConfigDefaults:
    """BridgeConfig.from_env() with no env vars uses sane defaults."""

    def test_default_host(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.sip_host == "0.0.0.0"

    def test_default_port(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.sip_port == 6060

    def test_default_model(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        assert "gemini" in cfg.live_model_id.lower()

    def test_default_voice(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.gemini_voice == "Aoede"

    def test_default_tenant(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.tenant_id == "public"


class TestBridgeConfigFromEnv:
    """BridgeConfig.from_env() reads env vars correctly."""

    def test_custom_host_and_port(self) -> None:
        from sip_bridge.config import BridgeConfig

        env = {"SIP_BRIDGE_HOST": "10.0.0.1", "SIP_BRIDGE_PORT": "5060"}
        with patch.dict("os.environ", env, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.sip_host == "10.0.0.1"
        assert cfg.sip_port == 5060

    def test_allowed_peers_parsed(self) -> None:
        from sip_bridge.config import BridgeConfig

        env = {"SIP_ALLOWED_PEERS": "10.0.0.1, 10.0.0.2, "}
        with patch.dict("os.environ", env, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.sip_allowed_peers == frozenset({"10.0.0.1", "10.0.0.2"})

    def test_empty_allowed_peers(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.sip_allowed_peers == frozenset()

    def test_custom_system_instruction(self) -> None:
        from sip_bridge.config import BridgeConfig

        env = {"SIP_SYSTEM_INSTRUCTION": "You are a hotel receptionist."}
        with patch.dict("os.environ", env, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.system_instruction == "You are a hotel receptionist."


class TestBridgeConfigValidation:
    """BridgeConfig.validate() returns meaningful errors."""

    def test_missing_api_key_flagged(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        errors = cfg.validate()
        assert any("GOOGLE_API_KEY" in e for e in errors)

    def test_localhost_public_ip_flagged(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        errors = cfg.validate()
        assert any("SIP_PUBLIC_IP" in e for e in errors)

    def test_missing_sip_username_flagged(self) -> None:
        from sip_bridge.config import BridgeConfig

        env = {
            "GOOGLE_API_KEY": "test-key-123",
            "SIP_PUBLIC_IP": "203.0.113.1",
            "SIP_PASSWORD": "secret-pass",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = BridgeConfig.from_env()
        errors = cfg.validate()
        assert any("SIP_USERNAME" in e for e in errors)

    def test_missing_sip_password_flagged(self) -> None:
        from sip_bridge.config import BridgeConfig

        env = {
            "GOOGLE_API_KEY": "test-key-123",
            "SIP_PUBLIC_IP": "203.0.113.1",
            "SIP_USERNAME": "agent1.ekaette@ng.sip.africastalking.com",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = BridgeConfig.from_env()
        errors = cfg.validate()
        assert any("SIP_PASSWORD" in e for e in errors)

    def test_valid_config_no_errors(self) -> None:
        from sip_bridge.config import BridgeConfig

        env = {
            "GOOGLE_API_KEY": "test-key-123",
            "SIP_PUBLIC_IP": "203.0.113.1",
            "SIP_USERNAME": "agent1.ekaette@ng.sip.africastalking.com",
            "SIP_PASSWORD": "secret-pass",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = BridgeConfig.from_env()
        assert cfg.validate() == []


class TestBridgeConfigImmutability:
    """Frozen dataclass cannot be mutated after creation."""

    def test_cannot_set_attribute(self) -> None:
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {}, clear=True):
            cfg = BridgeConfig.from_env()
        with pytest.raises(AttributeError):
            cfg.sip_port = 9999  # type: ignore[misc]
