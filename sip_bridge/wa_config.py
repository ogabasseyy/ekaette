"""WhatsApp SIP bridge runtime configuration.

All config from env vars with WA_* prefix — no app.* imports (separate runtime).
Same frozen-dataclass pattern as BridgeConfig.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class WhatsAppBridgeConfig:
    """Immutable WhatsApp bridge runtime config loaded from env."""

    sip_host: str
    sip_port: int
    sip_username: str
    sip_password: str
    sip_allowed_cidrs: frozenset[str]
    tls_certfile: str
    tls_keyfile: str
    sandbox_mode: bool
    gemini_api_key: str
    live_model_id: str
    system_instruction: str
    gemini_voice: str
    company_id: str
    tenant_id: str
    health_port: int
    wa_service_api_base_url: str
    wa_service_secret: str
    # Gateway mode — route via Cloud Run instead of direct Gemini
    gateway_mode: bool = False
    gateway_ws_url: str = ""
    gateway_ws_secret: str = ""

    @classmethod
    def from_env(cls) -> WhatsAppBridgeConfig:
        """Load config from environment variables."""
        cidrs_raw = os.getenv("WA_SIP_ALLOWED_CIDRS", "")
        cidrs = frozenset(
            c.strip() for c in cidrs_raw.split(",") if c.strip()
        )
        sandbox_raw = os.getenv("WA_SANDBOX_MODE", "false").lower()
        return cls(
            sip_host=os.getenv("WA_SIP_HOST", "0.0.0.0"),
            sip_port=int(os.getenv("WA_SIP_PORT", "5061")),
            sip_username=os.getenv("WA_SIP_USERNAME", ""),
            sip_password=os.getenv("WA_SIP_PASSWORD", ""),
            sip_allowed_cidrs=cidrs,
            tls_certfile=os.getenv("WA_TLS_CERTFILE", ""),
            tls_keyfile=os.getenv("WA_TLS_KEYFILE", ""),
            sandbox_mode=sandbox_raw in ("true", "1", "yes"),
            gemini_api_key=os.getenv("GOOGLE_API_KEY", ""),
            live_model_id=os.getenv(
                "LIVE_MODEL_ID",
                "gemini-2.5-flash-native-audio-preview-12-2025",
            ),
            system_instruction=os.getenv(
                "WA_SYSTEM_INSTRUCTION",
                "You are Ekaette, an AI customer service assistant. "
                "Be helpful, concise, and professional.",
            ),
            gemini_voice=os.getenv("WA_GEMINI_VOICE", "Aoede"),
            company_id=os.getenv("WA_COMPANY_ID", "ekaette-electronics"),
            tenant_id=os.getenv("WA_TENANT_ID", "public"),
            health_port=int(os.getenv("WA_HEALTH_PORT", "8082")),
            gateway_mode=os.getenv("WA_GATEWAY_MODE", "false").lower() in ("true", "1", "yes"),
            gateway_ws_url=os.getenv("WA_GATEWAY_WS_URL", ""),
            gateway_ws_secret=os.getenv("WA_GATEWAY_WS_SECRET", ""),
            wa_service_api_base_url=os.getenv("WA_SERVICE_API_BASE_URL", "").rstrip("/"),
            wa_service_secret=os.getenv("WA_SERVICE_SECRET", ""),
        )

    def validate(self) -> list[str]:
        """Return list of config validation errors."""
        errors: list[str] = []
        if self.gateway_mode and not self.gateway_ws_url:
            errors.append("WA_GATEWAY_WS_URL is required when WA_GATEWAY_MODE is enabled")
        if self.gateway_mode and not self.gateway_ws_secret:
            errors.append("WA_GATEWAY_WS_SECRET is required when WA_GATEWAY_MODE is enabled")
        if not self.gateway_mode and not self.gemini_api_key:
            errors.append("GOOGLE_API_KEY is required for Gemini Live")
        if not self.sip_username:
            errors.append("WA_SIP_USERNAME is required (business phone number)")
        if not self.sip_password:
            errors.append("WA_SIP_PASSWORD is required (Meta-generated)")
        if not self.sandbox_mode and not self.sip_allowed_cidrs:
            errors.append(
                "WA_SIP_ALLOWED_CIDRS must be set in production "
                "(non-sandbox) mode — IP allowlist is mandatory"
            )
        if not self.sandbox_mode and (not self.tls_certfile or not self.tls_keyfile):
            errors.append(
                "WA_TLS_CERTFILE and WA_TLS_KEYFILE are required in production "
                "(non-sandbox) mode — SIP over TLS is mandatory"
            )
        if not self.sandbox_mode and not self.wa_service_api_base_url:
            errors.append(
                "WA_SERVICE_API_BASE_URL is required in production "
                "(non-sandbox) mode — needed for during-call messaging"
            )
        if not self.sandbox_mode and not self.wa_service_secret:
            errors.append(
                "WA_SERVICE_SECRET is required in production "
                "(non-sandbox) mode — needed for during-call messaging auth"
            )
        return errors
