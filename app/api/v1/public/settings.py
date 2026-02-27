"""Typed public runtime settings and parsed constants.

These values are loaded once at process start and exposed as constants that
main.py can alias for backward-compatible monkeypatching in tests.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_allowlist(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


class PublicRuntimeSettings(BaseSettings):
    """Typed env-driven settings for public API/realtime runtime."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    default_company_id: str = Field(default="default", alias="DEFAULT_COMPANY_ID")
    knowledge_import_max_bytes: int = Field(default=1_048_576, alias="KNOWLEDGE_IMPORT_MAX_BYTES")

    allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
        alias="ALLOWED_ORIGINS",
    )
    allow_missing_ws_origin: bool = Field(default=False, alias="ALLOW_MISSING_WS_ORIGIN")

    max_upload_bytes: int = Field(default=10 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")
    allowed_upload_mime_types: str = Field(
        default="image/jpeg,image/png,image/webp,image/heic,image/heif",
        alias="ALLOWED_UPLOAD_MIME_TYPES",
    )

    token_rate_limit: int = Field(default=10, alias="TOKEN_RATE_LIMIT")
    upload_rate_limit: int = Field(default=20, alias="UPLOAD_RATE_LIMIT")
    rate_limit_window: int = Field(default=60, alias="RATE_LIMIT_WINDOW")
    rate_limit_max_buckets: int = Field(default=5000, alias="RATE_LIMIT_MAX_BUCKETS")

    gemini_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    token_max_uses: int = Field(default=1, alias="TOKEN_MAX_USES")
    token_ttl_seconds: int = Field(default=300, alias="TOKEN_TTL_SECONDS")
    token_new_session_ttl_seconds: int = Field(default=600, alias="TOKEN_NEW_SESSION_TTL_SECONDS")
    token_allowed_tenants: str = Field(default="public", alias="TOKEN_ALLOWED_TENANTS")

    manual_vad: bool = Field(default=False, alias="MANUAL_VAD")
    auto_vad_prefix_padding_ms: int = Field(default=80, alias="AUTO_VAD_PREFIX_PADDING_MS")
    auto_vad_silence_duration_ms: int = Field(default=320, alias="AUTO_VAD_SILENCE_DURATION_MS")
    silence_nudge_seconds: int = Field(default=8, alias="SILENCE_NUDGE_SECONDS")
    silence_nudge_max: int = Field(default=2, alias="SILENCE_NUDGE_MAX")
    silence_nudge_backoff_multiplier: float = Field(
        default=1.8,
        alias="SILENCE_NUDGE_BACKOFF_MULTIPLIER",
    )
    silence_nudge_max_interval_seconds: int = Field(
        default=30,
        alias="SILENCE_NUDGE_MAX_INTERVAL_SECONDS",
    )
    debug_telemetry: bool = Field(default=False, alias="DEBUG_TELEMETRY")
    token_price_prompt_per_million_usd: float = Field(default=0.0, alias="TOKEN_PRICE_PROMPT_PER_MILLION_USD")
    token_price_completion_per_million_usd: float = Field(
        default=0.0,
        alias="TOKEN_PRICE_COMPLETION_PER_MILLION_USD",
    )

    registry_enabled: bool = Field(default=True, alias="REGISTRY_ENABLED")
    registry_require_company_template_match: bool = Field(
        default=False,
        alias="REGISTRY_REQUIRE_COMPANY_TEMPLATE_MATCH",
    )


_cfg = PublicRuntimeSettings()

DEFAULT_COMPANY_ID = (_cfg.default_company_id or "default").strip().lower() or "default"
KNOWLEDGE_IMPORT_MAX_BYTES = int(_cfg.knowledge_import_max_bytes)

ALLOWED_ORIGINS = _parse_allowlist(_cfg.allowed_origins)
ALLOWED_ORIGIN_SET = set(ALLOWED_ORIGINS)
ALLOW_MISSING_WS_ORIGIN = bool(_cfg.allow_missing_ws_origin)

MAX_UPLOAD_BYTES = int(_cfg.max_upload_bytes)
ALLOWED_UPLOAD_MIME_TYPES = set(_parse_allowlist(_cfg.allowed_upload_mime_types))

TOKEN_RATE_LIMIT = int(_cfg.token_rate_limit)
UPLOAD_RATE_LIMIT = int(_cfg.upload_rate_limit)
RATE_LIMIT_WINDOW = int(_cfg.rate_limit_window)
RATE_LIMIT_MAX_BUCKETS = int(_cfg.rate_limit_max_buckets)

GEMINI_API_KEY = (_cfg.gemini_api_key or "").strip()
TOKEN_MAX_USES = int(_cfg.token_max_uses)
TOKEN_TTL_SECONDS = int(_cfg.token_ttl_seconds)
TOKEN_NEW_SESSION_TTL_SECONDS = int(_cfg.token_new_session_ttl_seconds)
TOKEN_ALLOWED_TENANTS = set(_parse_allowlist(_cfg.token_allowed_tenants))

MANUAL_VAD = bool(_cfg.manual_vad)
AUTO_VAD_PREFIX_PADDING_MS = int(_cfg.auto_vad_prefix_padding_ms)
AUTO_VAD_SILENCE_DURATION_MS = int(_cfg.auto_vad_silence_duration_ms)
SILENCE_NUDGE_SECONDS = int(_cfg.silence_nudge_seconds)
SILENCE_NUDGE_MAX = int(_cfg.silence_nudge_max)
SILENCE_NUDGE_BACKOFF_MULTIPLIER = float(_cfg.silence_nudge_backoff_multiplier)
SILENCE_NUDGE_MAX_INTERVAL_SECONDS = int(_cfg.silence_nudge_max_interval_seconds)
DEBUG_TELEMETRY = bool(_cfg.debug_telemetry)
TOKEN_PRICE_PROMPT_PER_MILLION = float(_cfg.token_price_prompt_per_million_usd)
TOKEN_PRICE_COMPLETION_PER_MILLION = float(_cfg.token_price_completion_per_million_usd)

REGISTRY_ENABLED = bool(_cfg.registry_enabled)
REGISTRY_REQUIRE_COMPANY_TEMPLATE_MATCH = bool(_cfg.registry_require_company_template_match)


def build_live_model_candidates(primary_model: str, additional_models: list[str]) -> list[str]:
    """Build deduplicated model candidate list preserving order."""
    candidates = [model for model in [primary_model, *additional_models] if model]
    seen: set[str] = set()
    return [
        model for model in candidates if not (model in seen or seen.add(model))
    ]
