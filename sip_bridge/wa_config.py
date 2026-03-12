"""WhatsApp SIP bridge runtime configuration.

All config from env vars with WA_* prefix — no app.* imports (separate runtime).
Same frozen-dataclass pattern as BridgeConfig.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass

_DEFAULT_LIVE_MODEL_ID = "gemini-2.5-flash-native-audio-preview-12-2025"
_DISALLOWED_LIVE_MODEL_IDS = frozenset({"gemini-3-flash-preview"})


def _is_text_only_model_id(model_id: str) -> bool:
    normalized = model_id.strip().lower()
    if normalized in _DISALLOWED_LIVE_MODEL_IDS:
        return True
    return (
        normalized.endswith("-preview")
        and "native-audio" not in normalized
        and "live" not in normalized
    )


@dataclass(slots=True, frozen=True)
class WhatsAppBridgeConfig:
    """Immutable WhatsApp bridge runtime config loaded from env."""

    sip_host: str
    sip_port: int
    sip_public_ip: str
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
    # Phone identity
    default_phone_region: str = "NG"
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
            sip_public_ip=os.getenv("WA_SIP_PUBLIC_IP", "").strip(),
            sip_username=os.getenv("WA_SIP_USERNAME", ""),
            sip_password=os.getenv("WA_SIP_PASSWORD", ""),
            sip_allowed_cidrs=cidrs,
            tls_certfile=os.getenv("WA_TLS_CERTFILE", ""),
            tls_keyfile=os.getenv("WA_TLS_KEYFILE", ""),
            sandbox_mode=sandbox_raw in ("true", "1", "yes"),
            gemini_api_key=os.getenv("GOOGLE_API_KEY", ""),
            live_model_id=os.getenv(
                "LIVE_MODEL_ID",
                _DEFAULT_LIVE_MODEL_ID,
            ),
            system_instruction=os.getenv(
                "WA_SYSTEM_INSTRUCTION",
                "You are the virtual assistant named ehkaitay, pronounced 'eh-KAI-tay'. "
                "The middle syllable is exactly 'kai', rhyming with 'sky'. "
                "You are answering a phone call. Greet the caller warmly and ask how you can help. "
                "Always speak in English. "
                "Be helpful, concise, and professional. Keep responses short for phone conversation.",
            ),
            gemini_voice=os.getenv("WA_GEMINI_VOICE", "Aoede"),
            company_id=os.getenv("WA_COMPANY_ID", "ekaette-electronics"),
            tenant_id=os.getenv("WA_TENANT_ID", "public"),
            health_port=int(os.getenv("WA_HEALTH_PORT", "8082")),
            default_phone_region=os.getenv("WA_DEFAULT_PHONE_REGION", "NG").strip().upper(),
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
        if not self.gateway_mode:
            normalized_live_model = self.live_model_id.strip().lower()
            if normalized_live_model == "":
                errors.append(
                    "LIVE_MODEL_ID is required; set it to a Gemini Live-capable model "
                    f"(default: {_DEFAULT_LIVE_MODEL_ID!r})"
                )
            elif _is_text_only_model_id(normalized_live_model):
                errors.append(
                    "LIVE_MODEL_ID does not support Gemini Live bidirectional audio; "
                    f"use {_DEFAULT_LIVE_MODEL_ID!r} or another native-audio model"
                )
        if not self.sip_username:
            errors.append("WA_SIP_USERNAME is required (business phone number)")
        if not self.sip_password:
            errors.append("WA_SIP_PASSWORD is required (Meta-generated)")
        if not self.sandbox_mode and self._requires_public_ip_override() and not self.sip_public_ip:
            errors.append(
                "WA_SIP_PUBLIC_IP is required in production when WA_SIP_HOST is wildcard, "
                "loopback, or private so SIP Contact/SDP advertise a reachable public IPv4"
            )
        if self.sip_public_ip:
            try:
                advertised_ip = ipaddress.ip_address(self.sip_public_ip)
                if advertised_ip.version != 4:
                    errors.append("WA_SIP_PUBLIC_IP must be an IPv4 address")
                elif (
                    advertised_ip.is_loopback
                    or advertised_ip.is_private
                    or advertised_ip.is_link_local
                    or advertised_ip.is_unspecified
                ):
                    errors.append("WA_SIP_PUBLIC_IP must be a reachable public IPv4 address")
            except ValueError:
                errors.append("WA_SIP_PUBLIC_IP must be a valid IPv4 address")
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
        if not self.default_phone_region or len(self.default_phone_region) != 2 or not self.default_phone_region.isalpha():
            errors.append(
                "WA_DEFAULT_PHONE_REGION must be a valid 2-letter ISO 3166-1 alpha-2 country code"
            )
        return errors

    def _requires_public_ip_override(self) -> bool:
        """True when the bind host is not a routable public IP for SIP signaling."""
        candidate = self.sip_host.strip()
        if candidate in {"", "0.0.0.0", "::"}:
            return True
        try:
            bind_ip = ipaddress.ip_address(candidate)
        except ValueError:
            return True
        return (
            bind_ip.version != 4
            or bind_ip.is_loopback
            or bind_ip.is_private
            or bind_ip.is_link_local
            or bind_ip.is_unspecified
        )
