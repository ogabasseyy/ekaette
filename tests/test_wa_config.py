"""Tests for WhatsApp bridge configuration (wa_config.py).

Follows the BridgeConfig pattern — frozen dataclass, env vars, WA_* prefix.
"""

from __future__ import annotations

import pytest


class TestWhatsAppBridgeConfigDefaults:
    """Default values when env vars are not set."""

    def test_default_sip_host(self, monkeypatch):
        monkeypatch.delenv("WA_SIP_HOST", raising=False)
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.sip_host == "0.0.0.0"

    def test_default_sip_port(self, monkeypatch):
        monkeypatch.delenv("WA_SIP_PORT", raising=False)
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.sip_port == 5061

    def test_default_tenant(self, monkeypatch):
        monkeypatch.delenv("WA_TENANT_ID", raising=False)
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.tenant_id == "public"

    def test_default_sandbox_mode(self, monkeypatch):
        monkeypatch.delenv("WA_SANDBOX_MODE", raising=False)
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.sandbox_mode is False

    def test_default_phone_region(self, monkeypatch):
        monkeypatch.delenv("WA_DEFAULT_PHONE_REGION", raising=False)
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.default_phone_region == "NG"


class TestWhatsAppBridgeConfigFromEnv:
    """Config from env vars with WA_* prefix."""

    def test_custom_phone_region(self, monkeypatch):
        monkeypatch.setenv("WA_DEFAULT_PHONE_REGION", "GB")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.default_phone_region == "GB"

    def test_custom_host_and_port(self, monkeypatch):
        monkeypatch.setenv("WA_SIP_HOST", "10.0.0.1")
        monkeypatch.setenv("WA_SIP_PORT", "5062")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.sip_host == "10.0.0.1"
        assert cfg.sip_port == 5062

    def test_sip_credentials(self, monkeypatch):
        monkeypatch.setenv("WA_SIP_USERNAME", "+2348001234567")
        monkeypatch.setenv("WA_SIP_PASSWORD", "meta-secret")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.sip_username == "+2348001234567"
        assert cfg.sip_password == "meta-secret"

    def test_allowed_cidrs_parsed(self, monkeypatch):
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "157.240.0.0/16, 31.13.24.0/21")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert "157.240.0.0/16" in cfg.sip_allowed_cidrs
        assert "31.13.24.0/21" in cfg.sip_allowed_cidrs

    def test_empty_cidrs(self, monkeypatch):
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert len(cfg.sip_allowed_cidrs) == 0

    def test_sandbox_mode_true(self, monkeypatch):
        monkeypatch.setenv("WA_SANDBOX_MODE", "true")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.sandbox_mode is True

    def test_tls_cert_paths(self, monkeypatch):
        monkeypatch.setenv("WA_TLS_CERTFILE", "/etc/ssl/cert.pem")
        monkeypatch.setenv("WA_TLS_KEYFILE", "/etc/ssl/key.pem")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.tls_certfile == "/etc/ssl/cert.pem"
        assert cfg.tls_keyfile == "/etc/ssl/key.pem"


class TestWhatsAppBridgeConfigValidation:
    """Validation errors for incomplete config."""

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("API_KEY" in e or "api_key" in e.lower() for e in errors)

    def test_missing_sip_credentials(self, monkeypatch):
        monkeypatch.setenv("WA_SIP_USERNAME", "")
        monkeypatch.setenv("WA_SIP_PASSWORD", "")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("username" in e.lower() or "password" in e.lower() for e in errors)

    def test_invalid_phone_region_flagged(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "user")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        monkeypatch.setenv("WA_DEFAULT_PHONE_REGION", "123")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("PHONE_REGION" in e for e in errors)

    def test_production_refuses_empty_cidrs(self, monkeypatch):
        """Non-sandbox mode with empty CIDRs is a validation error."""
        monkeypatch.setenv("WA_SANDBOX_MODE", "false")
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("cidr" in e.lower() or "allowlist" in e.lower() for e in errors)

    def test_sandbox_allows_empty_cidrs(self, monkeypatch):
        """Sandbox mode with empty CIDRs is NOT an error."""
        monkeypatch.setenv("WA_SANDBOX_MODE", "true")
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        monkeypatch.setenv("WA_SIP_USERNAME", "+123")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert not any("cidr" in e.lower() or "allowlist" in e.lower() for e in errors)


class TestWhatsAppBridgeConfigTLSValidation:
    """TLS cert/key presence is mandatory in non-sandbox mode."""

    def test_production_missing_tls_cert_is_error(self, monkeypatch):
        """Non-sandbox with empty TLS certfile must be a validation error."""
        monkeypatch.setenv("WA_SANDBOX_MODE", "false")
        monkeypatch.setenv("WA_TLS_CERTFILE", "")
        monkeypatch.setenv("WA_TLS_KEYFILE", "/etc/ssl/key.pem")
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "10.0.0.0/8")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "+123")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("tls" in e.lower() or "cert" in e.lower() for e in errors)

    def test_production_missing_tls_key_is_error(self, monkeypatch):
        """Non-sandbox with empty TLS keyfile must be a validation error."""
        monkeypatch.setenv("WA_SANDBOX_MODE", "false")
        monkeypatch.setenv("WA_TLS_CERTFILE", "/etc/ssl/cert.pem")
        monkeypatch.setenv("WA_TLS_KEYFILE", "")
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "10.0.0.0/8")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "+123")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("tls" in e.lower() or "key" in e.lower() for e in errors)

    def test_sandbox_allows_missing_tls(self, monkeypatch):
        """Sandbox mode with no TLS cert/key is NOT an error."""
        monkeypatch.setenv("WA_SANDBOX_MODE", "true")
        monkeypatch.setenv("WA_TLS_CERTFILE", "")
        monkeypatch.setenv("WA_TLS_KEYFILE", "")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "+123")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert not any("tls" in e.lower() or "cert" in e.lower() for e in errors)


class TestWhatsAppServiceApiUrl:
    """WA_SERVICE_API_BASE_URL config and validation."""

    def test_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("WA_SERVICE_API_BASE_URL", "https://wa-service.example.com/")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.wa_service_api_base_url == "https://wa-service.example.com"

    def test_default_empty(self, monkeypatch):
        monkeypatch.delenv("WA_SERVICE_API_BASE_URL", raising=False)
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.wa_service_api_base_url == ""

    def test_production_validation_fails_when_missing(self, monkeypatch):
        monkeypatch.setenv("WA_SANDBOX_MODE", "false")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "user")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "10.0.0.0/8")
        monkeypatch.setenv("WA_TLS_CERTFILE", "/cert.pem")
        monkeypatch.setenv("WA_TLS_KEYFILE", "/key.pem")
        monkeypatch.setenv("WA_SERVICE_API_BASE_URL", "")
        monkeypatch.setenv("WA_SERVICE_SECRET", "secret")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("WA_SERVICE_API_BASE_URL" in e for e in errors)

    def test_production_validation_fails_when_secret_missing(self, monkeypatch):
        monkeypatch.setenv("WA_SANDBOX_MODE", "false")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "user")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        monkeypatch.setenv("WA_SIP_ALLOWED_CIDRS", "10.0.0.0/8")
        monkeypatch.setenv("WA_TLS_CERTFILE", "/cert.pem")
        monkeypatch.setenv("WA_TLS_KEYFILE", "/key.pem")
        monkeypatch.setenv("WA_SERVICE_API_BASE_URL", "https://wa-service.example.com")
        monkeypatch.setenv("WA_SERVICE_SECRET", "")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("WA_SERVICE_SECRET" in e for e in errors)

    def test_sandbox_allows_missing(self, monkeypatch):
        monkeypatch.setenv("WA_SANDBOX_MODE", "true")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "user")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        monkeypatch.setenv("WA_SERVICE_API_BASE_URL", "")
        monkeypatch.setenv("WA_SERVICE_SECRET", "")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert not any("WA_SERVICE_API_BASE_URL" in e for e in errors)
        assert not any("WA_SERVICE_SECRET" in e for e in errors)


class TestWhatsAppBridgeConfigGateway:
    """Gateway mode config fields for WA bridge."""

    def test_gateway_mode_default_false(self, monkeypatch):
        monkeypatch.delenv("WA_GATEWAY_MODE", raising=False)
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.gateway_mode is False

    def test_gateway_mode_from_env(self, monkeypatch):
        monkeypatch.setenv("WA_GATEWAY_MODE", "true")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.gateway_mode is True

    def test_gateway_ws_url_from_env(self, monkeypatch):
        monkeypatch.setenv("WA_GATEWAY_WS_URL", "wss://ekaette-test.run.app")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.gateway_ws_url == "wss://ekaette-test.run.app"

    def test_gateway_ws_secret_from_env(self, monkeypatch):
        monkeypatch.setenv("WA_GATEWAY_WS_SECRET", "shared-secret")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        assert cfg.gateway_ws_secret == "shared-secret"

    def test_gateway_mode_requires_url(self, monkeypatch):
        monkeypatch.setenv("WA_GATEWAY_MODE", "true")
        monkeypatch.setenv("WA_GATEWAY_WS_URL", "")
        monkeypatch.setenv("WA_GATEWAY_WS_SECRET", "shared-secret")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("WA_SIP_USERNAME", "user")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("WA_GATEWAY_WS_URL" in e for e in errors)

    def test_gateway_mode_requires_secret(self, monkeypatch):
        monkeypatch.setenv("WA_GATEWAY_MODE", "true")
        monkeypatch.setenv("WA_GATEWAY_WS_URL", "wss://ekaette-test.run.app")
        monkeypatch.setenv("WA_GATEWAY_WS_SECRET", "")
        monkeypatch.setenv("WA_SIP_USERNAME", "user")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("WA_GATEWAY_WS_SECRET" in e for e in errors)

    def test_direct_mode_rejects_text_only_live_model(self, monkeypatch):
        monkeypatch.setenv("WA_GATEWAY_MODE", "false")
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        monkeypatch.setenv("LIVE_MODEL_ID", "gemini-3-flash-preview")
        monkeypatch.setenv("WA_SIP_USERNAME", "user")
        monkeypatch.setenv("WA_SIP_PASSWORD", "pass")
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        errors = cfg.validate()
        assert any("LIVE_MODEL_ID" in e for e in errors)


class TestWhatsAppBridgeConfigImmutability:
    """Config is frozen — cannot be modified after creation."""

    def test_cannot_set_attribute(self):
        from sip_bridge.wa_config import WhatsAppBridgeConfig

        cfg = WhatsAppBridgeConfig.from_env()
        with pytest.raises(AttributeError):
            cfg.sip_port = 9999  # type: ignore[misc]
