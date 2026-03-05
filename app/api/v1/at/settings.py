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

    # Payments (Paystack)
    paystack_enabled: bool = Field(default=False, alias="PAYSTACK_ENABLED")
    paystack_secret_key: str = Field(default="", alias="PAYSTACK_SECRET_KEY")
    paystack_public_key: str = Field(default="", alias="PAYSTACK_PUBLIC_KEY")
    paystack_webhook_secret: str = Field(default="", alias="PAYSTACK_WEBHOOK_SECRET")
    paystack_initialize_url: str = Field(
        default="https://api.paystack.co/transaction/initialize",
        alias="PAYSTACK_INITIALIZE_URL",
    )
    paystack_verify_url_template: str = Field(
        default="https://api.paystack.co/transaction/verify/{reference}",
        alias="PAYSTACK_VERIFY_URL_TEMPLATE",
    )
    paystack_default_callback_url: str = Field(
        default="",
        alias="PAYSTACK_DEFAULT_CALLBACK_URL",
    )
    paystack_customer_url: str = Field(
        default="https://api.paystack.co/customer",
        alias="PAYSTACK_CUSTOMER_URL",
    )
    paystack_dedicated_account_url: str = Field(
        default="https://api.paystack.co/dedicated_account",
        alias="PAYSTACK_DEDICATED_ACCOUNT_URL",
    )
    paystack_dedicated_account_providers_url: str = Field(
        default="https://api.paystack.co/dedicated_account/available_providers",
        alias="PAYSTACK_DEDICATED_ACCOUNT_PROVIDERS_URL",
    )
    paystack_default_dva_bank_slug: str = Field(
        default="",
        alias="PAYSTACK_DEFAULT_DVA_BANK_SLUG",
    )
    paystack_default_dva_country: str = Field(
        default="NG",
        alias="PAYSTACK_DEFAULT_DVA_COUNTRY",
    )

    # WhatsApp Cloud API (Meta Business)
    whatsapp_enabled: bool = Field(default=False, alias="WHATSAPP_ENABLED")
    whatsapp_access_token: str = Field(default="", alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str = Field(default="", alias="WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_api_version: str = Field(default="v25.0", alias="WHATSAPP_API_VERSION")
    whatsapp_app_secret: str = Field(default="", alias="WHATSAPP_APP_SECRET")
    whatsapp_verify_token: str = Field(default="", alias="WHATSAPP_VERIFY_TOKEN")

    # WhatsApp service-to-service auth
    wa_service_secret: str = Field(default="", alias="WA_SERVICE_SECRET")
    wa_service_secret_previous: str = Field(default="", alias="WA_SERVICE_SECRET_PREVIOUS")
    wa_service_auth_max_skew_seconds: int = Field(default=300, alias="WA_SERVICE_AUTH_MAX_SKEW_SECONDS")

    # WhatsApp Cloud Tasks
    wa_cloud_tasks_max_attempts: int = Field(default=3, alias="WA_CLOUD_TASKS_MAX_ATTEMPTS")
    wa_cloud_tasks_queue_name: str = Field(default="wa-webhook-processing", alias="WA_CLOUD_TASKS_QUEUE_NAME")
    wa_cloud_tasks_audience: str = Field(default="", alias="WA_CLOUD_TASKS_AUDIENCE")
    wa_tasks_invoker_email: str = Field(default="", alias="WA_TASKS_INVOKER_EMAIL")

    # WhatsApp Graph API retry
    wa_graph_retry_max_attempts: int = Field(default=3, alias="WA_GRAPH_RETRY_MAX_ATTEMPTS")
    wa_graph_retry_max_backoff_seconds: int = Field(default=8, alias="WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS")

    # WhatsApp template fallback
    wa_utility_template_name: str = Field(default="", alias="WA_UTILITY_TEMPLATE_NAME")
    wa_utility_template_language: str = Field(default="en_US", alias="WA_UTILITY_TEMPLATE_LANGUAGE")

    # WhatsApp send idempotency
    wa_send_idempotency_ttl_hours: int = Field(default=24, alias="WA_SEND_IDEMPOTENCY_TTL_HOURS")

    # WhatsApp webhook rate limiting
    wa_webhook_rate_limit_mode: str = Field(default="edge_enforced", alias="WA_WEBHOOK_RATE_LIMIT_MODE")
    wa_edge_ratelimit_header: str = Field(default="X-Edge-RateLimit-Checked", alias="WA_EDGE_RATELIMIT_HEADER")

    # WhatsApp replay artifacts
    wa_replay_bucket: str = Field(default="", alias="WA_REPLAY_BUCKET")
    wa_replay_blob_prefix: str = Field(default="wa/replay/", alias="WA_REPLAY_BLOB_PREFIX")
    wa_replay_blob_ttl_hours: int = Field(default=24, alias="WA_REPLAY_BLOB_TTL_HOURS")


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

# Paystack
PAYSTACK_ENABLED = cfg.paystack_enabled
PAYSTACK_SECRET_KEY = cfg.paystack_secret_key
PAYSTACK_PUBLIC_KEY = cfg.paystack_public_key
PAYSTACK_WEBHOOK_SECRET = cfg.paystack_webhook_secret
PAYSTACK_INITIALIZE_URL = cfg.paystack_initialize_url
PAYSTACK_VERIFY_URL_TEMPLATE = cfg.paystack_verify_url_template
PAYSTACK_DEFAULT_CALLBACK_URL = cfg.paystack_default_callback_url
PAYSTACK_CUSTOMER_URL = cfg.paystack_customer_url
PAYSTACK_DEDICATED_ACCOUNT_URL = cfg.paystack_dedicated_account_url
PAYSTACK_DEDICATED_ACCOUNT_PROVIDERS_URL = cfg.paystack_dedicated_account_providers_url
def _resolve_dva_bank_slug(explicit: str, secret_key: str) -> str:
    """Return the DVA bank slug, auto-detecting test-bank for test keys."""
    if explicit:
        return explicit
    if secret_key.startswith("sk_test_"):
        return "test-bank"
    return "wema-bank"


PAYSTACK_DEFAULT_DVA_BANK_SLUG = _resolve_dva_bank_slug(cfg.paystack_default_dva_bank_slug, cfg.paystack_secret_key)
PAYSTACK_DEFAULT_DVA_COUNTRY = cfg.paystack_default_dva_country

# WhatsApp Cloud API
WHATSAPP_ENABLED = cfg.whatsapp_enabled
WHATSAPP_ACCESS_TOKEN = cfg.whatsapp_access_token
WHATSAPP_PHONE_NUMBER_ID = cfg.whatsapp_phone_number_id
WHATSAPP_API_VERSION = cfg.whatsapp_api_version
WHATSAPP_APP_SECRET = cfg.whatsapp_app_secret
WHATSAPP_VERIFY_TOKEN = cfg.whatsapp_verify_token

# WhatsApp service-to-service auth
WA_SERVICE_SECRET = cfg.wa_service_secret
WA_SERVICE_SECRET_PREVIOUS = cfg.wa_service_secret_previous
WA_SERVICE_AUTH_MAX_SKEW_SECONDS = cfg.wa_service_auth_max_skew_seconds

# WhatsApp Cloud Tasks
WA_CLOUD_TASKS_MAX_ATTEMPTS = cfg.wa_cloud_tasks_max_attempts
WA_CLOUD_TASKS_QUEUE_NAME = cfg.wa_cloud_tasks_queue_name
WA_CLOUD_TASKS_AUDIENCE = cfg.wa_cloud_tasks_audience
WA_TASKS_INVOKER_EMAIL = cfg.wa_tasks_invoker_email

# WhatsApp Graph API retry
WA_GRAPH_RETRY_MAX_ATTEMPTS = cfg.wa_graph_retry_max_attempts
WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS = cfg.wa_graph_retry_max_backoff_seconds

# WhatsApp template fallback
WA_UTILITY_TEMPLATE_NAME = cfg.wa_utility_template_name
WA_UTILITY_TEMPLATE_LANGUAGE = cfg.wa_utility_template_language

# WhatsApp send idempotency
WA_SEND_IDEMPOTENCY_TTL_HOURS = cfg.wa_send_idempotency_ttl_hours

# WhatsApp webhook rate limiting
WA_WEBHOOK_RATE_LIMIT_MODE = cfg.wa_webhook_rate_limit_mode
WA_EDGE_RATELIMIT_HEADER = cfg.wa_edge_ratelimit_header

# WhatsApp replay artifacts
WA_REPLAY_BUCKET = cfg.wa_replay_bucket
WA_REPLAY_BLOB_PREFIX = cfg.wa_replay_blob_prefix
WA_REPLAY_BLOB_TTL_HOURS = cfg.wa_replay_blob_ttl_hours


def _validate_whatsapp_config() -> None:
    """Fail-closed validation: raise on missing required config when WhatsApp is enabled."""
    if not WHATSAPP_ENABLED:
        return
    required = {
        "WHATSAPP_ACCESS_TOKEN": WHATSAPP_ACCESS_TOKEN,
        "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP_PHONE_NUMBER_ID,
        "WHATSAPP_APP_SECRET": WHATSAPP_APP_SECRET,
        "WHATSAPP_VERIFY_TOKEN": WHATSAPP_VERIFY_TOKEN,
        "WA_SERVICE_SECRET": WA_SERVICE_SECRET,
        "WA_TASKS_INVOKER_EMAIL": WA_TASKS_INVOKER_EMAIL,
        "WA_CLOUD_TASKS_AUDIENCE": WA_CLOUD_TASKS_AUDIENCE,
        "WA_REPLAY_BUCKET": WA_REPLAY_BUCKET,
    }
    # Optional for demo: WA_UTILITY_TEMPLATE_NAME, WA_UTILITY_TEMPLATE_LANGUAGE.
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(
            f"WHATSAPP_ENABLED=true but required config is missing: {', '.join(missing)}"
        )


_validate_whatsapp_config()
