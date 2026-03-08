"""SIP bridge runtime configuration.

All config from env vars — no app.* imports (separate runtime).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_LIVE_MODEL_ID = "gemini-2.5-flash-native-audio-preview-12-2025"
_DISALLOWED_LIVE_MODEL_IDS = frozenset({"gemini-3-flash-preview"})


def _read_int_env(name: str, default: str) -> int:
    """Read an integer env var and raise a contextual error on bad values."""
    raw_value = os.getenv(name, default)
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc


@dataclass(slots=True, frozen=True)
class BridgeConfig:
    """Immutable bridge runtime config loaded from env."""

    sip_host: str
    sip_port: int
    sip_public_ip: str
    sip_allowed_peers: frozenset[str]
    gemini_api_key: str
    live_model_id: str
    system_instruction: str
    gemini_voice: str
    company_id: str
    tenant_id: str
    health_port: int
    # AT SIP registration
    sip_registrar: str
    sip_username: str
    sip_password: str
    sip_register_interval: int
    # Phone identity
    default_phone_region: str = "NG"
    # Gateway mode — route via Cloud Run instead of direct Gemini
    gateway_mode: bool = False
    gateway_ws_url: str = ""
    gateway_ws_secret: str = ""

    @classmethod
    def from_env(cls) -> BridgeConfig:
        """Load config from environment variables."""
        allowed_raw = os.getenv("SIP_ALLOWED_PEERS", "")
        allowed = frozenset(
            ip.strip() for ip in allowed_raw.split(",") if ip.strip()
        )
        return cls(
            sip_host=os.getenv("SIP_BRIDGE_HOST", "0.0.0.0"),
            sip_port=_read_int_env("SIP_BRIDGE_PORT", "6060"),
            sip_public_ip=os.getenv("SIP_PUBLIC_IP", "127.0.0.1"),
            sip_allowed_peers=allowed,
            gemini_api_key=os.getenv("GOOGLE_API_KEY", ""),
            live_model_id=os.getenv(
                "LIVE_MODEL_ID",
                _DEFAULT_LIVE_MODEL_ID,
            ),
            system_instruction=os.getenv(
                "SIP_SYSTEM_INSTRUCTION",
                "You are the virtual assistant named ehkaitay. "
                "Your name is ehkaitay — always say it exactly like that. "
                "You are answering a phone call. Greet the caller warmly and ask how you can help. "
                "Always speak in English. "
                "Be helpful, concise, and professional. Keep responses short for phone conversation.",
            ),
            gemini_voice=os.getenv("SIP_GEMINI_VOICE", "Aoede"),
            company_id=os.getenv("SIP_COMPANY_ID", "ekaette-electronics"),
            tenant_id=os.getenv("SIP_TENANT_ID", "public"),
            health_port=_read_int_env("SIP_HEALTH_PORT", "8081"),
            default_phone_region=os.getenv("SIP_DEFAULT_PHONE_REGION", "NG").upper(),
            gateway_mode=os.getenv("GATEWAY_MODE", "false").lower() in ("true", "1", "yes"),
            gateway_ws_url=os.getenv("GATEWAY_WS_URL", ""),
            gateway_ws_secret=os.getenv("GATEWAY_WS_SECRET", ""),
            sip_registrar=os.getenv("SIP_REGISTRAR", "ng.sip.africastalking.com"),
            sip_username=os.getenv("SIP_USERNAME", ""),
            sip_password=os.getenv("SIP_PASSWORD", ""),
            sip_register_interval=_read_int_env("SIP_REGISTER_INTERVAL", "300"),
        )

    def validate(self) -> list[str]:
        """Return list of config validation errors."""
        errors: list[str] = []
        if not self.gateway_mode and not self.gemini_api_key:
            errors.append("GOOGLE_API_KEY is required for Gemini Live")
        if not self.sip_public_ip or self.sip_public_ip == "127.0.0.1":
            errors.append("SIP_PUBLIC_IP should be set to a reachable public IP")
        if not self.sip_username:
            errors.append("SIP_USERNAME required for AT SIP registration")
        if not self.sip_password:
            errors.append("SIP_PASSWORD required for AT SIP registration")

        port_fields: dict[str, object] = {
            "SIP_BRIDGE_PORT": self.sip_port,
            "SIP_HEALTH_PORT": self.health_port,
        }
        for field_name, value in port_fields.items():
            if not isinstance(value, int) or value < 1 or value > 65535:
                errors.append(f"{field_name} must be an integer between 1 and 65535")

        if not isinstance(self.sip_register_interval, int) or self.sip_register_interval <= 0:
            errors.append("SIP_REGISTER_INTERVAL must be an integer greater than 0")

        if self.gateway_mode and not self.gateway_ws_url:
            errors.append("GATEWAY_WS_URL is required when GATEWAY_MODE is enabled")
        if self.gateway_mode and not self.gateway_ws_secret:
            errors.append("GATEWAY_WS_SECRET is required when GATEWAY_MODE is enabled")

        if not self.default_phone_region or len(self.default_phone_region) != 2 or not self.default_phone_region.isalpha():
            errors.append("SIP_DEFAULT_PHONE_REGION must be a valid 2-letter ISO 3166-1 alpha-2 country code")

        normalized_live_model = self.live_model_id.strip().lower()
        if normalized_live_model == "":
            errors.append(
                "LIVE_MODEL_ID is required; set it to a Live API-compatible model "
                f"(default: {_DEFAULT_LIVE_MODEL_ID!r})"
            )
        elif normalized_live_model in _DISALLOWED_LIVE_MODEL_IDS:
            errors.append(
                "LIVE_MODEL_ID does not support Gemini Live bidirectional audio; "
                f"use {_DEFAULT_LIVE_MODEL_ID!r} or another native-audio model"
            )
        return errors
