"""SIP bridge runtime configuration.

All config from env vars — no app.* imports (separate runtime).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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

    @classmethod
    def from_env(cls) -> BridgeConfig:
        """Load config from environment variables."""
        allowed_raw = os.getenv("SIP_ALLOWED_PEERS", "")
        allowed = frozenset(
            ip.strip() for ip in allowed_raw.split(",") if ip.strip()
        )
        return cls(
            sip_host=os.getenv("SIP_BRIDGE_HOST", "0.0.0.0"),
            sip_port=int(os.getenv("SIP_BRIDGE_PORT", "6060")),
            sip_public_ip=os.getenv("SIP_PUBLIC_IP", "127.0.0.1"),
            sip_allowed_peers=allowed,
            gemini_api_key=os.getenv("GOOGLE_API_KEY", ""),
            live_model_id=os.getenv(
                "LIVE_MODEL_ID",
                "gemini-2.5-flash-native-audio-preview-12-2025",
            ),
            system_instruction=os.getenv(
                "SIP_SYSTEM_INSTRUCTION",
                "You are an AI customer service assistant named ehkaitay. "
                "Your name is ehkaitay — always say it exactly like that. "
                "You are answering a phone call. Greet the caller warmly and ask how you can help. "
                "Always speak in English. "
                "Be helpful, concise, and professional. Keep responses short for phone conversation.",
            ),
            gemini_voice=os.getenv("SIP_GEMINI_VOICE", "Aoede"),
            company_id=os.getenv("SIP_COMPANY_ID", "ekaette-electronics"),
            tenant_id=os.getenv("SIP_TENANT_ID", "public"),
            health_port=int(os.getenv("SIP_HEALTH_PORT", "8081")),
            sip_registrar=os.getenv("SIP_REGISTRAR", "ng.sip.africastalking.com"),
            sip_username=os.getenv("SIP_USERNAME", ""),
            sip_password=os.getenv("SIP_PASSWORD", ""),
            sip_register_interval=int(os.getenv("SIP_REGISTER_INTERVAL", "300")),
        )

    def validate(self) -> list[str]:
        """Return list of config validation errors."""
        errors: list[str] = []
        if not self.gemini_api_key:
            errors.append("GOOGLE_API_KEY is required for Gemini Live")
        if not self.sip_public_ip or self.sip_public_ip == "127.0.0.1":
            errors.append("SIP_PUBLIC_IP should be set to a reachable public IP")
        if not self.sip_username:
            errors.append("SIP_USERNAME required for AT SIP registration")
        if not self.sip_password:
            errors.append("SIP_PASSWORD required for AT SIP registration")
        return errors
