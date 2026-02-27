"""Typed AT channel settings (pydantic-settings).

Mirrors the PublicRuntimeSettings pattern from app/api/v1/public/settings.py.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_ip_set(raw: str) -> set[str]:
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


class ATSettings(BaseSettings):
    """Africa's Talking environment-driven settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    at_username: str = Field(default="sandbox", alias="AT_USERNAME")
    at_api_key: str = Field(default="", alias="AT_API_KEY")
    at_environment: str = Field(default="sandbox", alias="AT_ENVIRONMENT")
    at_virtual_number: str = Field(default="", alias="AT_VIRTUAL_NUMBER")
    at_webhook_shared_secret: str = Field(default="", alias="AT_WEBHOOK_SHARED_SECRET")
    at_allowed_source_ips: str = Field(default="", alias="AT_ALLOWED_SOURCE_IPS")
    at_voice_enabled: bool = Field(default=False, alias="AT_VOICE_ENABLED")
    at_sms_enabled: bool = Field(default=False, alias="AT_SMS_ENABLED")
    at_callback_dial_fallback: bool = Field(default=True, alias="AT_CALLBACK_DIAL_FALLBACK_ENABLED")
    sip_bridge_endpoint: str = Field(default="", alias="SIP_BRIDGE_ENDPOINT")

    # Data governance (V2 addendum)
    at_recording_enabled: bool = Field(default=False, alias="AT_RECORDING_ENABLED")
    at_recording_disclosure: str = Field(
        default="This call may be recorded for quality assurance.",
        alias="AT_RECORDING_DISCLOSURE",
    )
    at_call_metadata_retention_days: int = Field(default=90, alias="AT_CALL_METADATA_RETENTION_DAYS")
    at_sms_retention_days: int = Field(default=30, alias="AT_SMS_RETENTION_DAYS")


cfg = ATSettings()

# Parsed constants (module-level, like public/settings.py)
AT_USERNAME = cfg.at_username
AT_API_KEY = cfg.at_api_key
AT_ENVIRONMENT = cfg.at_environment
AT_VIRTUAL_NUMBER = cfg.at_virtual_number
AT_WEBHOOK_SHARED_SECRET = cfg.at_webhook_shared_secret
AT_VOICE_ENABLED = cfg.at_voice_enabled
AT_SMS_ENABLED = cfg.at_sms_enabled
AT_CALLBACK_DIAL_FALLBACK = cfg.at_callback_dial_fallback
SIP_BRIDGE_ENDPOINT = cfg.sip_bridge_endpoint
ALLOWED_SOURCE_IPS = _parse_ip_set(cfg.at_allowed_source_ips)

# Data governance
AT_RECORDING_ENABLED = cfg.at_recording_enabled
AT_RECORDING_DISCLOSURE = cfg.at_recording_disclosure
AT_CALL_METADATA_RETENTION_DAYS = cfg.at_call_metadata_retention_days
AT_SMS_RETENTION_DAYS = cfg.at_sms_retention_days
