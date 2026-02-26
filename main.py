"""Ekaette — FastAPI Backend with ADK Bidi-Streaming."""

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import warnings

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()

from app.agents.ekaette_router.agent import ekaette_router  # noqa: E402
from app.agents.tool_scheduling import install_tool_response_scheduling_patch  # noqa: E402
from app.configs import RegistrySchemaVersionError  # noqa: E402
from app.configs.company_loader import (  # noqa: E402
    build_company_session_state,
    create_company_config_client,
    load_company_knowledge,
    load_company_profile,
)
from app.configs.industry_loader import (  # noqa: E402
    async_save_session_state,
    build_session_state,
    create_industry_config_client,
    load_industry_config,
)
from app.configs.model_resolver import get_live_model_candidates  # noqa: E402
from app.configs.session_factory import create_session_service, get_effective_app_name  # noqa: E402
from app.memory.memory_factory import create_memory_service  # noqa: E402
from app.observability import registry_log_context  # noqa: E402
from app.tools.vision_tools import cache_latest_image  # noqa: E402

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, LOG_LEVEL, logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Install S11 tool-response scheduling policy patch once at startup.
install_tool_response_scheduling_patch()

# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# ═══ Application Init ═══
APP_NAME = os.getenv("APP_NAME", "ekaette")
# For ADK session operations — maps to Agent Engine ID when using vertex backend.
SESSION_APP_NAME = get_effective_app_name()

app = FastAPI(title="Ekaette")


def _parse_allowlist(raw_origins: str) -> list[str]:
    """Parse comma-delimited origins into a clean list."""
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


# ═══ CORS Middleware — explicit allowlist, no wildcard ═══
ALLOWED_ORIGINS = _parse_allowlist(
    os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000")
)
ALLOWED_ORIGIN_SET = set(ALLOWED_ORIGINS)


def _is_origin_allowed(origin: str | None) -> bool:
    """Validate browser Origin against explicit allowlist."""
    return origin in ALLOWED_ORIGIN_SET


# Regex pattern for characters that enable log injection (newlines + control chars).
_LOG_UNSAFE_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def _sanitize_log(value: str | None) -> str:
    """Strip newlines and control characters from user-supplied values before logging."""
    if value is None:
        return "<none>"
    return _LOG_UNSAFE_RE.sub("", value)[:200]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


class TokenRequestPayload(BaseModel):
    """Request payload for ephemeral token creation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    user_id: str = Field(
        alias="userId",
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    tenant_id: str = Field(
        default="public",
        alias="tenantId",
        min_length=2,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    industry: str = Field(default="electronics")
    industry_template_id: str | None = Field(default=None, alias="industryTemplateId")
    company_id: str = Field(default="default", alias="companyId")


# Upload security constraints
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
ALLOWED_UPLOAD_MIME_TYPES = set(
    _parse_allowlist(
        os.getenv(
            "ALLOWED_UPLOAD_MIME_TYPES",
            "image/jpeg,image/png,image/webp,image/heic,image/heif",
        )
    )
)


def _validate_upload_bytes(mime_type: str, data: bytes) -> None:
    """Raise ValueError when upload MIME or size is invalid."""
    if mime_type not in ALLOWED_UPLOAD_MIME_TYPES:
        raise ValueError("MIME_TYPE_NOT_ALLOWED")
    if len(data) == 0:
        raise ValueError("EMPTY_UPLOAD")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("UPLOAD_TOO_LARGE")


def _env_flag(name: str, default: str = "false") -> bool:
    """Parse boolean-like environment variable values."""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _extract_server_message_from_state_delta(
    state_delta: dict[str, object] | None,
) -> dict[str, object] | None:
    """Extract one queued structured ServerMessage from event state delta."""
    if not isinstance(state_delta, dict):
        return None
    message = state_delta.get("temp:last_server_message")
    if isinstance(message, dict) and isinstance(message.get("type"), str):
        return message
    return None


def _usage_int(usage: object, *names: str) -> int:
    """Extract positive integer token counts from usage metadata shapes."""
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def _voice_for_industry(industry: str) -> str:
    voice_map = {
        "electronics": "Aoede",
        "hotel": "Puck",
        "automotive": "Charon",
        "fashion": "Kore",
    }
    key = (industry or "").strip().lower()
    return voice_map.get(key, "Aoede")


DEFAULT_COMPANY_ID = (
    os.getenv("DEFAULT_COMPANY_ID", "default").strip().lower() or "default"
)
_COMPANY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_TEMPLATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,127}$")


def _normalize_company_id(raw_value: object) -> str:
    """Sanitize company ID from untrusted client input."""
    if not isinstance(raw_value, str):
        return DEFAULT_COMPANY_ID
    normalized = raw_value.strip().lower()
    if not normalized:
        return DEFAULT_COMPANY_ID
    if not _COMPANY_ID_PATTERN.fullmatch(normalized):
        return DEFAULT_COMPANY_ID
    return normalized


def _normalize_tenant_id(raw_value: object, default: str = "public") -> str:
    """Sanitize tenant ID from untrusted input."""
    if not isinstance(raw_value, str):
        return default
    normalized = raw_value.strip().lower()
    if not normalized:
        return default
    if not _TENANT_ID_PATTERN.fullmatch(normalized):
        return default
    return normalized


def _normalize_template_id(raw_value: object) -> str | None:
    """Sanitize optional industry template ID from untrusted input."""
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if not _TEMPLATE_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _native_audio_live_config(
    industry: str,
    voice_override: str | None = None,
) -> dict[str, object]:
    """Shared native-audio config used by token and websocket paths."""
    voice = voice_override if voice_override else _voice_for_industry(industry)
    return {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": types.AudioTranscriptionConfig(),
        "output_audio_transcription": types.AudioTranscriptionConfig(),
        "session_resumption": types.SessionResumptionConfig(),
        "context_window_compression": types.ContextWindowCompressionConfig(
            trigger_tokens=80000,
            sliding_window=types.SlidingWindow(target_tokens=40000),
        ),
        "enable_affective_dialog": True,
        "proactivity": types.ProactivityConfig(proactive_audio=True),
        "speech_config": types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice,
                )
            ),
        ),
    }


def _registry_enabled() -> bool:
    return _env_flag("REGISTRY_ENABLED", "true")


def _registry_require_company_template_match() -> bool:
    return _env_flag("REGISTRY_REQUIRE_COMPANY_TEMPLATE_MATCH", "false")


def _registry_db_client() -> object | None:
    """Pick a Firestore client suitable for registry lookups."""
    return company_config_client or industry_config_client


async def _resolve_registry_runtime_config(
    *,
    tenant_id: str,
    company_id: str,
) -> object | None:
    """Resolve canonical registry config when registry mode is enabled."""
    if not _registry_enabled():
        return None

    db = _registry_db_client()
    if db is None:
        return None

    try:
        from app.configs.registry_loader import resolve_registry_config
        from app.configs import RegistrySchemaVersionError

        return await resolve_registry_config(db, tenant_id, company_id)
    except RegistrySchemaVersionError:
        raise
    except Exception as exc:
        logger.warning(
            "Registry runtime config resolution failed tenant=%s company=%s: %s",
            _sanitize_log(tenant_id),
            _sanitize_log(company_id),
            exc,
        )
        return None


def _registry_mismatch_response(
    *,
    requested_template_id: str | None,
    resolved_template_id: str | None,
) -> JSONResponse | None:
    """Return a strict mismatch response when configured to reject conflicts."""
    if not _registry_require_company_template_match():
        return None
    if not requested_template_id or not resolved_template_id:
        return None
    if requested_template_id == resolved_template_id:
        return None
    return JSONResponse(
        status_code=409,
        content={
            "error": "industryTemplateId does not match company configuration",
            "code": "TEMPLATE_COMPANY_MISMATCH",
            "requestedIndustryTemplateId": requested_template_id,
            "resolvedIndustryTemplateId": resolved_template_id,
        },
    )


def _legacy_industry_alias_from_registry_config(
    config: object | None,
    *,
    fallback: str,
) -> str:
    """Derive a backward-compatible legacy industry alias from registry config.

    Current frontend/runtime compatibility still expects legacy ids like
    `electronics`, `hotel`, `automotive`, `fashion`. Future templates may use
    more specific IDs (for example `aviation-support`), so we derive a stable
    legacy alias without exposing broad non-legacy categories like `retail`.
    """
    if config is None:
        return fallback

    known_legacy = {"electronics", "hotel", "automotive", "fashion"}

    explicit_alias = getattr(config, "legacy_industry_alias", None)
    if isinstance(explicit_alias, str):
        alias = explicit_alias.strip().lower()
        if alias:
            return alias

    template_id = getattr(config, "industry_template_id", None)
    if isinstance(template_id, str):
        normalized_template = template_id.strip().lower()
        if normalized_template in known_legacy:
            return normalized_template
        if "-" in normalized_template:
            prefix = normalized_template.split("-", 1)[0].strip()
            if prefix:
                return prefix

    category = getattr(config, "template_category", None)
    if isinstance(category, str):
        normalized_category = category.strip().lower()
        if normalized_category in known_legacy:
            return normalized_category
        # For new verticals (for example telecom/aviation), category often *is*
        # the desired legacy alias.
        if normalized_category in {"telecom", "aviation"}:
            return normalized_category

    return fallback


def _canonical_state_updates_from_registry(config: object) -> dict[str, object]:
    """Extract registry-derived session keys (canonical + compatibility aliases)."""
    if config is None:
        return {}

    keys = (
        "app:industry",
        "app:industry_config",
        "app:greeting",
        "app:voice",
        "app:tenant_id",
        "app:industry_template_id",
        "app:capabilities",
        "app:ui_theme",
        "app:connector_manifest",
        "app:registry_version",
    )

    try:
        from app.configs.registry_loader import build_session_state_from_registry

        registry_state = build_session_state_from_registry(config)
    except Exception:
        return {}

    updates = {k: registry_state[k] for k in keys if k in registry_state}
    fallback_industry = (
        str(getattr(config, "industry_template_id", "") or "").strip().lower() or "electronics"
    )
    updates["app:industry"] = _legacy_industry_alias_from_registry_config(
        config,
        fallback=fallback_industry,
    )
    return updates


def _build_session_started_message(
    *,
    session_id: str,
    industry: str,
    company_id: str,
    voice: str,
    manual_vad_active: bool,
    session_state: dict[str, object] | None,
) -> dict[str, object]:
    """Build a consistent session_started payload for all websocket code paths."""
    payload: dict[str, object] = {
        "type": "session_started",
        "sessionId": session_id,
        "industry": industry,
        "companyId": company_id,
        "voice": voice,
        "manualVadActive": manual_vad_active,
        "vadMode": "manual" if manual_vad_active else "auto",
    }
    if not isinstance(session_state, dict):
        return payload

    tenant = session_state.get("app:tenant_id")
    if isinstance(tenant, str) and tenant:
        payload["tenantId"] = tenant
    template_id = session_state.get("app:industry_template_id")
    if isinstance(template_id, str) and template_id:
        payload["industryTemplateId"] = template_id
    caps = session_state.get("app:capabilities")
    if isinstance(caps, list):
        payload["capabilities"] = caps
    registry_version = session_state.get("app:registry_version")
    if isinstance(registry_version, str) and registry_version:
        payload["registryVersion"] = registry_version
    return payload


def _append_canonical_lock_fields(
    payload: dict[str, object],
    session_state: dict[str, object] | None,
) -> dict[str, object]:
    """Attach canonical lock metadata to lock/error responses when present."""
    if not isinstance(session_state, dict):
        return payload
    tenant = session_state.get("app:tenant_id")
    if isinstance(tenant, str) and tenant:
        payload["tenantId"] = tenant
    template_id = session_state.get("app:industry_template_id")
    if isinstance(template_id, str) and template_id:
        payload["industryTemplateId"] = template_id
    registry_version = session_state.get("app:registry_version")
    if isinstance(registry_version, str) and registry_version:
        payload["registryVersion"] = registry_version
    return payload


# ═══ Session Service ═══
session_service = create_session_service()
industry_config_client = create_industry_config_client()
company_config_client = create_company_config_client()

# ═══ Memory Service ═══
memory_service = create_memory_service()

# ═══ Runner ═══
runner = Runner(
    app_name=SESSION_APP_NAME,
    agent=ekaette_router,
    session_service=session_service,
    memory_service=memory_service,
)


# ═══ HTTP Endpoints ═══

@app.get("/health")
async def health():
    """Health check endpoint for Cloud Run and monitoring."""
    return {"status": "ok", "app": APP_NAME}


# ═══ Rate Limiting (simple in-memory, per-IP+endpoint) ═══
_rate_limit_buckets: dict[str, list[float]] = {}
TOKEN_RATE_LIMIT = int(os.getenv("TOKEN_RATE_LIMIT", "10"))  # requests/minute
UPLOAD_RATE_LIMIT = int(os.getenv("UPLOAD_RATE_LIMIT", "20"))  # requests/minute
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds


def _check_rate_limit(client_ip: str, bucket: str, limit: int) -> bool:
    """Return True if request is within per-IP/per-endpoint rate limit."""
    now = time.time()
    key = f"{bucket}:{client_ip}"
    timestamps = _rate_limit_buckets.get(key, [])
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= limit:
        _rate_limit_buckets[key] = timestamps
        return False
    timestamps.append(now)
    _rate_limit_buckets[key] = timestamps
    return True


def _client_ip_from_request(request: Request) -> str:
    """Resolve client IP safely from FastAPI request."""
    return request.client.host if request.client else "unknown"


def _origin_or_reject(origin: str | None) -> JSONResponse | None:
    """Return a 403 response when origin is missing/invalid."""
    if not _is_origin_allowed(origin):
        return JSONResponse(
            status_code=403,
            content={"error": "Origin not allowed"},
        )
    return None


def _tenant_allowed(tenant_id: str) -> bool:
    """Check tenant against allowed list used for external-facing endpoints."""
    return not TOKEN_ALLOWED_TENANTS or tenant_id in TOKEN_ALLOWED_TENANTS


# ═══ Ephemeral Token Endpoint ═══
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY", "")
TOKEN_MAX_USES = int(os.getenv("TOKEN_MAX_USES", "1"))
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "300"))
TOKEN_NEW_SESSION_TTL_SECONDS = int(
    os.getenv("TOKEN_NEW_SESSION_TTL_SECONDS", "600")
)
TOKEN_ALLOWED_TENANTS = set(
    _parse_allowlist(os.getenv("TOKEN_ALLOWED_TENANTS", "public"))
)
MANUAL_VAD = _env_flag("MANUAL_VAD", "false")
AUTO_VAD_PREFIX_PADDING_MS = int(os.getenv("AUTO_VAD_PREFIX_PADDING_MS", "80"))
AUTO_VAD_SILENCE_DURATION_MS = int(os.getenv("AUTO_VAD_SILENCE_DURATION_MS", "320"))
SILENCE_NUDGE_SECONDS = int(os.getenv("SILENCE_NUDGE_SECONDS", "8"))
SILENCE_NUDGE_MAX = int(os.getenv("SILENCE_NUDGE_MAX", "2"))
SILENCE_NUDGE_BACKOFF_MULTIPLIER = float(
    os.getenv("SILENCE_NUDGE_BACKOFF_MULTIPLIER", "1.8")
)
SILENCE_NUDGE_MAX_INTERVAL_SECONDS = int(
    os.getenv("SILENCE_NUDGE_MAX_INTERVAL_SECONDS", "30")
)
DEBUG_TELEMETRY = _env_flag("DEBUG_TELEMETRY", "false")
TOKEN_PRICE_PROMPT_PER_MILLION = float(
    os.getenv("TOKEN_PRICE_PROMPT_PER_MILLION_USD", "0")
)
TOKEN_PRICE_COMPLETION_PER_MILLION = float(
    os.getenv("TOKEN_PRICE_COMPLETION_PER_MILLION_USD", "0")
)
LIVE_MODEL_CANDIDATES = [
    model
    for model in [ekaette_router.model, *get_live_model_candidates()]
    if model
]
_seen_models: set[str] = set()
LIVE_MODEL_CANDIDATES = [
    model for model in LIVE_MODEL_CANDIDATES if not (model in _seen_models or _seen_models.add(model))
]


def _build_manual_realtime_input_config() -> object | None:
    """Build manual-VAD config when SDK/runtime supports it."""
    realtime_input_cls = getattr(types, "RealtimeInputConfig", None)
    auto_activity_cls = getattr(types, "AutomaticActivityDetection", None)
    if realtime_input_cls is None or auto_activity_cls is None:
        return None

    try:
        return realtime_input_cls(
            automatic_activity_detection=auto_activity_cls(disabled=True)
        )
    except Exception as exc:
        logger.warning("Failed to initialize manual VAD config: %s", exc)
        return None


def _build_auto_realtime_input_config() -> object | None:
    """Build tuned Gemini automatic-VAD config when runtime supports it.

    2026 best practice for Gemini Live:
    - Keep Gemini automatic activity detection enabled by default.
    - Tune sensitivity/hangover server-side.
    - Use client VAD for UI/debug only unless MANUAL_VAD=true.
    """
    realtime_input_cls = getattr(types, "RealtimeInputConfig", None)
    auto_activity_cls = getattr(types, "AutomaticActivityDetection", None)
    start_sensitivity_cls = getattr(types, "StartSensitivity", None)
    end_sensitivity_cls = getattr(types, "EndSensitivity", None)
    if (
        realtime_input_cls is None
        or auto_activity_cls is None
        or start_sensitivity_cls is None
        or end_sensitivity_cls is None
    ):
        return None

    try:
        return realtime_input_cls(
            automatic_activity_detection=auto_activity_cls(
                disabled=False,
                startOfSpeechSensitivity=getattr(
                    start_sensitivity_cls,
                    "START_SENSITIVITY_LOW",
                    None,
                ),
                endOfSpeechSensitivity=getattr(
                    end_sensitivity_cls,
                    "END_SENSITIVITY_LOW",
                    None,
                ),
                prefixPaddingMs=AUTO_VAD_PREFIX_PADDING_MS,
                silenceDurationMs=AUTO_VAD_SILENCE_DURATION_MS,
            )
        )
    except Exception as exc:
        logger.warning("Failed to initialize automatic VAD config: %s", exc)
        return None


_MANUAL_REALTIME_INPUT_CONFIG = (
    _build_manual_realtime_input_config() if MANUAL_VAD else None
)
if MANUAL_VAD and _MANUAL_REALTIME_INPUT_CONFIG is None:
    logger.warning(
        "MANUAL_VAD=true but google-genai runtime lacks RealtimeInputConfig support; "
        "falling back to Gemini automatic activity detection."
    )
_AUTO_REALTIME_INPUT_CONFIG = (
    None if MANUAL_VAD else _build_auto_realtime_input_config()
)

MANUAL_VAD_ACTIVE = _MANUAL_REALTIME_INPUT_CONFIG is not None
REALTIME_INPUT_CONFIG = (
    _MANUAL_REALTIME_INPUT_CONFIG
    if MANUAL_VAD_ACTIVE
    else _AUTO_REALTIME_INPUT_CONFIG
)

TOKEN_CLIENT = (
    genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(api_version="v1alpha"),
    )
    if GEMINI_API_KEY
    else None
)


@app.post("/api/token")
async def create_ephemeral_token(
    payload: TokenRequestPayload,
    request: Request,
):
    """Issue a constrained short-lived Gemini Live API auth token."""
    blocked_origin = _origin_or_reject(request.headers.get("origin"))
    if blocked_origin:
        return blocked_origin

    client_ip = _client_ip_from_request(request)
    if not _check_rate_limit(client_ip, "token", TOKEN_RATE_LIMIT):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "retryAfter": RATE_LIMIT_WINDOW},
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )

    if TOKEN_ALLOWED_TENANTS and payload.tenant_id not in TOKEN_ALLOWED_TENANTS:
        logger.warning(
            "Token request rejected (tenant forbidden) %s",
            registry_log_context(
                tenant_id=payload.tenant_id,
                registry_mode=_registry_enabled(),
                source="api_token",
            ),
        )
        return JSONResponse(
            status_code=403,
            content={"error": "Tenant not allowed"},
        )

    if TOKEN_CLIENT is None:
        return JSONResponse(
            status_code=500,
            content={"error": "Server API key not configured"},
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SECONDS)
    new_session_expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=TOKEN_NEW_SESSION_TTL_SECONDS
    )
    normalized_tenant_id = _normalize_tenant_id(payload.tenant_id, default="public")
    normalized_company_id = _normalize_company_id(payload.company_id)
    requested_template_id = _normalize_template_id(payload.industry_template_id)
    requested_industry = (payload.industry or "electronics").strip().lower() or "electronics"

    try:
        registry_config = await _resolve_registry_runtime_config(
            tenant_id=normalized_tenant_id,
            company_id=normalized_company_id,
        )
    except RegistrySchemaVersionError as exc:
        logger.warning(
            "Token request rejected (registry schema version) %s details=%s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                company_id=normalized_company_id,
                registry_mode=_registry_enabled(),
                source="api_token",
            ),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Unsupported registry schema version",
                "code": getattr(exc, "code", "REGISTRY_SCHEMA_VERSION_UNSUPPORTED"),
                "tenantId": normalized_tenant_id,
                "companyId": normalized_company_id,
                "details": str(exc),
            },
        )
    if _registry_enabled() and registry_config is None:
        logger.warning(
            "Token request rejected (registry config missing) %s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                company_id=normalized_company_id,
                registry_mode=True,
                source="api_token",
            ),
        )
        return JSONResponse(
            status_code=404,
            content={
                "error": "Company configuration not found in registry",
                "code": "REGISTRY_CONFIG_NOT_FOUND",
                "tenantId": normalized_tenant_id,
                "companyId": normalized_company_id,
            },
        )
    if registry_config is not None:
        mismatch_response = _registry_mismatch_response(
            requested_template_id=requested_template_id,
            resolved_template_id=getattr(registry_config, "industry_template_id", None),
        )
        if mismatch_response is not None:
            logger.warning(
                "Token request rejected (template/company mismatch) %s requested_template_id=%s resolved_template_id=%s",
                registry_log_context(
                    tenant_id=normalized_tenant_id,
                    company_id=normalized_company_id,
                    industry_template_id=getattr(registry_config, "industry_template_id", None),
                    registry_version=getattr(registry_config, "registry_version", None),
                    registry_mode=True,
                    source="api_token",
                ),
                _sanitize_log(requested_template_id),
                _sanitize_log(getattr(registry_config, "industry_template_id", None)),
            )
            return mismatch_response

    resolved_voice = (
        getattr(registry_config, "voice", None)
        if isinstance(getattr(registry_config, "voice", None), str)
        else None
    )
    live_connect_config_kwargs = _native_audio_live_config(
        requested_industry,
        voice_override=resolved_voice,
    )
    if REALTIME_INPUT_CONFIG is not None:
        live_connect_config_kwargs["realtime_input_config"] = REALTIME_INPUT_CONFIG

    auth_token = None
    selected_model = LIVE_MODEL_CANDIDATES[0]
    last_exc: Exception | None = None
    for index, candidate_model in enumerate(LIVE_MODEL_CANDIDATES):
        try:
            live_constraints = types.LiveConnectConstraints(
                model=candidate_model,
                config=types.LiveConnectConfig(**live_connect_config_kwargs),
            )
            auth_token = await TOKEN_CLIENT.aio.auth_tokens.create(
                config=types.CreateAuthTokenConfig(
                    uses=TOKEN_MAX_USES,
                    expire_time=expires_at,
                    new_session_expire_time=new_session_expires_at,
                    live_connect_constraints=live_constraints,
                )
            )
            selected_model = candidate_model
            if index > 0:
                logger.warning(
                    "Token creation fell back to model '%s' after primary failure",
                    candidate_model,
                )
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Token creation failed for model '%s': %s",
                candidate_model,
                exc,
            )
            continue

    if auth_token is None:
        logger.error("Token creation failed for all model candidates: %s", last_exc)
        return JSONResponse(
            status_code=502,
            content={"error": "Failed to create ephemeral token"},
        )

    if not auth_token.name:
        return JSONResponse(
            status_code=502,
            content={"error": "Token response was empty"},
        )

    resolved_legacy_industry = requested_industry
    if registry_config is not None:
        resolved_legacy_industry = _legacy_industry_alias_from_registry_config(
            registry_config,
            fallback=requested_industry,
        )

    response: dict[str, object] = {
        "token": auth_token.name,
        "expiresAt": expires_at.isoformat(),
        "maxUses": TOKEN_MAX_USES,
        "industry": resolved_legacy_industry,
        "companyId": normalized_company_id,
        "tenantId": normalized_tenant_id,
        "userId": payload.user_id,
        "model": selected_model,
        "fallbackModelUsed": selected_model != LIVE_MODEL_CANDIDATES[0],
        "manualVadActive": MANUAL_VAD_ACTIVE,
        "vadMode": "manual" if MANUAL_VAD_ACTIVE else "auto",
    }

    # Phase 2 canonical fields (registry path) + voice resolution.
    response["voice"] = resolved_voice or _voice_for_industry(resolved_legacy_industry)
    if registry_config is not None:
        template_id = getattr(registry_config, "industry_template_id", None)
        capabilities = getattr(registry_config, "capabilities", None)
        registry_version = getattr(registry_config, "registry_version", None)
        if isinstance(template_id, str) and template_id:
            response["industryTemplateId"] = template_id
        if isinstance(capabilities, list):
            response["capabilities"] = capabilities
        if isinstance(registry_version, str) and registry_version:
            response["registryVersion"] = registry_version

    return response


# ═══ Onboarding Config Endpoint ═══


@app.get("/api/onboarding/config")
async def get_onboarding_config(request: Request):
    """Return industry templates + companies for the onboarding UI."""
    blocked_origin = _origin_or_reject(request.headers.get("origin"))
    if blocked_origin:
        return blocked_origin

    tenant_id = request.query_params.get("tenantId")
    if not tenant_id:
        logger.warning(
            "Onboarding config request rejected (missing tenantId) %s",
            registry_log_context(registry_mode=_registry_enabled(), source="api_onboarding"),
        )
        return JSONResponse(
            status_code=400,
            content={"error": "Missing required query parameter: tenantId"},
        )
    normalized_tenant_id = _normalize_tenant_id(tenant_id, default="public")
    if not _tenant_allowed(normalized_tenant_id):
        logger.warning(
            "Onboarding config request rejected (tenant forbidden) %s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                registry_mode=_registry_enabled(),
                source="api_onboarding",
            ),
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "Tenant not allowed",
                "tenantId": normalized_tenant_id,
            },
        )

    from app.configs.registry_loader import RegistryDataMissingError, build_onboarding_config

    try:
        config = await build_onboarding_config(industry_config_client, normalized_tenant_id)
    except RegistryDataMissingError as exc:
        logger.warning(
            "Onboarding config unavailable %s code=%s details=%s",
            registry_log_context(
                tenant_id=normalized_tenant_id,
                registry_mode=_registry_enabled(),
                source="api_onboarding",
            ),
            getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Registry onboarding config unavailable",
                "code": getattr(exc, "code", "REGISTRY_ONBOARDING_CONFIG_NOT_FOUND"),
                "tenantId": normalized_tenant_id,
                "details": str(exc),
            },
        )
    return config


@app.post("/api/upload/validate")
async def validate_upload(
    request: Request,
    file: UploadFile = File(...),
):
    """Validate upload MIME and file size before any storage write."""
    blocked_origin = _origin_or_reject(request.headers.get("origin"))
    if blocked_origin:
        return blocked_origin

    client_ip = _client_ip_from_request(request)
    if not _check_rate_limit(client_ip, "upload", UPLOAD_RATE_LIMIT):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "retryAfter": RATE_LIMIT_WINDOW},
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )

    mime_type = file.content_type or "application/octet-stream"
    data = await file.read(MAX_UPLOAD_BYTES + 1)

    try:
        _validate_upload_bytes(mime_type, data)
    except ValueError as exc:
        code = str(exc)
        if code == "UPLOAD_TOO_LARGE":
            return JSONResponse(
                status_code=413,
                content={"error": "Upload exceeds max size", "maxBytes": MAX_UPLOAD_BYTES},
            )
        if code == "MIME_TYPE_NOT_ALLOWED":
            return JSONResponse(
                status_code=415,
                content={
                    "error": "MIME type not allowed",
                    "allowedMimeTypes": sorted(ALLOWED_UPLOAD_MIME_TYPES),
                },
            )
        return JSONResponse(status_code=400, content={"error": "Invalid upload payload"})
    finally:
        await file.close()

    return {
        "status": "ok",
        "filename": file.filename,
        "mimeType": mime_type,
        "sizeBytes": len(data),
    }


# ═══ WebSocket Endpoint ═══

@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """WebSocket endpoint for bidirectional streaming with ADK."""
    origin = websocket.headers.get("origin")
    if not _is_origin_allowed(origin):
        logger.warning("Rejected WebSocket origin: %s", _sanitize_log(origin))
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    logger.debug("WebSocket connection request: user_id=%s, session_id=%s", _sanitize_log(user_id), _sanitize_log(session_id))
    await websocket.accept()
    logger.debug("WebSocket connection accepted")
    client_ip = websocket.client.host if websocket.client else "unknown"

    # ═══ Session Init ═══
    model_name = ekaette_router.model
    is_native_audio = "native-audio" in model_name.lower()

    # Parse onboarding context from query params.
    requested_industry = websocket.query_params.get("industry", "electronics")
    if not isinstance(requested_industry, str):
        requested_industry = "electronics"
    industry = requested_industry.strip().lower() or "electronics"
    requested_template_id = _normalize_template_id(
        websocket.query_params.get("industry_template_id")
        or websocket.query_params.get("industryTemplateId")
    )
    tenant_id = _normalize_tenant_id(
        websocket.query_params.get("tenant_id")
        or websocket.query_params.get("tenantId"),
        default="public",
    )
    if not _tenant_allowed(tenant_id):
        logger.warning(
            "WebSocket startup rejected (tenant forbidden) %s",
            registry_log_context(
                tenant_id=tenant_id,
                registry_mode=_registry_enabled(),
                source="ws_startup",
            ),
        )
        await websocket.send_text(json.dumps({
            "type": "error",
            "code": "TENANT_FORBIDDEN",
            "message": "Tenant not allowed",
            "tenantId": tenant_id,
        }))
        await websocket.close(code=1008, reason="Tenant not allowed")
        return

    requested_company = websocket.query_params.get(
        "company_id",
        websocket.query_params.get("companyId", DEFAULT_COMPANY_ID),
    )
    company_id = _normalize_company_id(requested_company)

    uses_vertex_sessions = session_service.__class__.__name__ == "VertexAiSessionService"
    resolved_session_id = session_id

    session = await session_service.get_session(
        app_name=SESSION_APP_NAME, user_id=user_id, session_id=resolved_session_id
    )
    registry_config = None
    if session:
        if isinstance(getattr(session, "id", None), str) and session.id:
            resolved_session_id = session.id
        # Session resumption should preserve prior selected industry.
        resumed_industry = session.state.get("app:industry")
        if isinstance(resumed_industry, str) and resumed_industry.strip():
            industry = resumed_industry.strip().lower()

        resumed_company = session.state.get("app:company_id")
        if isinstance(resumed_company, str) and resumed_company.strip():
            company_id = _normalize_company_id(resumed_company)

        resumed_tenant = session.state.get("app:tenant_id")
        if isinstance(resumed_tenant, str) and resumed_tenant.strip():
            tenant_id = _normalize_tenant_id(resumed_tenant, default=tenant_id)

        state_updates: dict[str, object] = {}
        if "app:industry_config" not in session.state:
            industry_config = await load_industry_config(industry_config_client, industry)
            state_updates.update(build_session_state(industry_config, industry))

        if (
            "app:company_profile" not in session.state
            or "app:company_knowledge" not in session.state
            or "app:company_id" not in session.state
        ):
            if _registry_enabled():
                company_profile = await load_company_profile(
                    company_config_client, company_id, tenant_id=tenant_id
                )
                company_knowledge = await load_company_knowledge(
                    company_config_client, company_id, tenant_id=tenant_id
                )
            else:
                company_profile = await load_company_profile(company_config_client, company_id)
                company_knowledge = await load_company_knowledge(company_config_client, company_id)
            state_updates.update(
                build_company_session_state(
                    company_id=company_id,
                    profile=company_profile,
                    knowledge=company_knowledge,
                )
            )

        if _registry_enabled() and (
            "app:tenant_id" not in session.state
            or "app:industry_template_id" not in session.state
            or "app:capabilities" not in session.state
            or "app:registry_version" not in session.state
        ):
            try:
                registry_config = await _resolve_registry_runtime_config(
                    tenant_id=tenant_id,
                    company_id=company_id,
                )
            except RegistrySchemaVersionError as exc:
                logger.warning(
                    "WebSocket startup rejected (registry schema version, resumed session) %s details=%s",
                    registry_log_context(
                        tenant_id=tenant_id,
                        company_id=company_id,
                        registry_mode=True,
                        source="ws_startup",
                    ),
                    exc,
                )
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "code": getattr(exc, "code", "REGISTRY_SCHEMA_VERSION_UNSUPPORTED"),
                    "message": "Unsupported registry schema version",
                    "tenantId": tenant_id,
                    "companyId": company_id,
                    "details": str(exc),
                }))
                await websocket.close(code=1011)
                return
            if registry_config is not None:
                state_updates.update(_canonical_state_updates_from_registry(registry_config))

        if state_updates:
            await async_save_session_state(
                session_service,
                app_name=SESSION_APP_NAME,
                user_id=user_id,
                session_id=resolved_session_id,
                state_updates=state_updates,
            )
    else:
        if _registry_enabled():
            try:
                registry_config = await _resolve_registry_runtime_config(
                    tenant_id=tenant_id,
                    company_id=company_id,
                )
            except RegistrySchemaVersionError as exc:
                logger.warning(
                    "WebSocket startup rejected (registry schema version, fresh session) %s details=%s",
                    registry_log_context(
                        tenant_id=tenant_id,
                        company_id=company_id,
                        registry_mode=True,
                        source="ws_startup",
                    ),
                    exc,
                )
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "code": getattr(exc, "code", "REGISTRY_SCHEMA_VERSION_UNSUPPORTED"),
                    "message": "Unsupported registry schema version",
                    "tenantId": tenant_id,
                    "companyId": company_id,
                    "details": str(exc),
                }))
                await websocket.close(code=1011)
                return
            if registry_config is not None:
                # In strict mode, reject explicit template/company mismatches.
                if requested_template_id is not None:
                    mismatch_response = _registry_mismatch_response(
                        requested_template_id=requested_template_id,
                        resolved_template_id=getattr(registry_config, "industry_template_id", None),
                    )
                    if mismatch_response is not None:
                        logger.warning(
                            "WebSocket startup rejected (template/company mismatch) %s requested_template_id=%s resolved_template_id=%s",
                            registry_log_context(
                                tenant_id=tenant_id,
                                company_id=company_id,
                                industry_template_id=getattr(registry_config, "industry_template_id", None),
                                registry_version=getattr(registry_config, "registry_version", None),
                                registry_mode=True,
                                source="ws_startup",
                            ),
                            _sanitize_log(requested_template_id),
                            _sanitize_log(getattr(registry_config, "industry_template_id", None)),
                        )
                        body = mismatch_response.body
                        if isinstance(body, bytes):
                            try:
                                body = json.loads(body.decode("utf-8"))
                            except Exception:
                                body = {"error": "Template/company mismatch"}
                        if isinstance(body, dict):
                            body.setdefault("tenantId", tenant_id)
                            body.setdefault("companyId", company_id)
                        await websocket.send_text(json.dumps(body))
                        await websocket.close(code=1008)
                        return

                resolved_template_id = getattr(registry_config, "industry_template_id", None)
                if isinstance(resolved_template_id, str) and resolved_template_id:
                    industry = resolved_template_id
            else:
                logger.warning(
                    "WebSocket startup rejected (registry config missing) %s",
                    registry_log_context(
                        tenant_id=tenant_id,
                        company_id=company_id,
                        registry_mode=True,
                        source="ws_startup",
                    ),
                )
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "code": "REGISTRY_CONFIG_NOT_FOUND",
                    "message": "Company configuration not found in registry",
                    "tenantId": tenant_id,
                    "companyId": company_id,
                }))
                await websocket.close(code=1008)
                return

        # Load onboarding context and build initial state
        industry_config = await load_industry_config(
            industry_config_client,
            industry,
        )
        if _registry_enabled():
            company_profile = await load_company_profile(
                company_config_client,
                company_id,
                tenant_id=tenant_id,
            )
            company_knowledge = await load_company_knowledge(
                company_config_client,
                company_id,
                tenant_id=tenant_id,
            )
        else:
            company_profile = await load_company_profile(company_config_client, company_id)
            company_knowledge = await load_company_knowledge(
                company_config_client, company_id
            )

        initial_state = build_session_state(industry_config, industry)
        initial_state.update(
            build_company_session_state(
                company_id=company_id,
                profile=company_profile,
                knowledge=company_knowledge,
            )
        )
        if registry_config is not None:
            initial_state.update(_canonical_state_updates_from_registry(registry_config))
        create_kwargs: dict[str, object] = {
            "app_name": SESSION_APP_NAME,
            "user_id": user_id,
            "state": initial_state,
        }
        # Vertex sessions currently auto-generate server-side IDs.
        if not uses_vertex_sessions:
            create_kwargs["session_id"] = resolved_session_id
        created_session = await session_service.create_session(
            **create_kwargs,
        )
        if (
            uses_vertex_sessions
            and isinstance(getattr(created_session, "id", None), str)
            and created_session.id
        ):
            resolved_session_id = created_session.id

    # Collect the final session state for voice + canonical fields.
    _ss: dict[str, object] = session.state if session else initial_state

    # Use locked session aliases when available.
    locked_industry = _ss.get("app:industry") if isinstance(_ss.get("app:industry"), str) else None
    session_industry = (locked_industry or industry).strip().lower() if isinstance((locked_industry or industry), str) else industry

    # Voice: prefer state override, fall back to industry map.
    _voice = _ss.get("app:voice") if isinstance(_ss.get("app:voice"), str) else None
    session_voice = _voice or _voice_for_industry(session_industry)

    if is_native_audio:
        run_config_kwargs: dict[str, object] = {
            "streaming_mode": StreamingMode.BIDI,
            **_native_audio_live_config(industry, voice_override=_voice),
        }
        if REALTIME_INPUT_CONFIG is not None:
            run_config_kwargs["realtime_input_config"] = REALTIME_INPUT_CONFIG
        run_config = RunConfig(**run_config_kwargs)
    else:
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["TEXT"],
            session_resumption=types.SessionResumptionConfig(),
        )

    logger.debug(
        "Model: %s, native_audio=%s, industry=%s, company_id=%s, voice=%s",
        model_name,
        is_native_audio,
        _sanitize_log(industry),
        _sanitize_log(company_id),
        _voice_for_industry(industry),
    )

    manual_vad_active = MANUAL_VAD_ACTIVE and is_native_audio

    # Notify client with the canonical session ID.
    await websocket.send_text(json.dumps(_build_session_started_message(
        session_id=resolved_session_id,
        industry=session_industry,
        company_id=company_id,
        voice=session_voice,
        manual_vad_active=manual_vad_active,
        session_state=_ss,
    )))

    live_request_queue = LiveRequestQueue()
    session_alive = asyncio.Event()
    session_alive.set()

    def _reset_silence_nudge_schedule(now: float) -> tuple[float, float]:
        base_interval = max(1.0, float(SILENCE_NUDGE_SECONDS))
        return now + base_interval, base_interval

    def _next_silence_nudge_interval(current_interval: float) -> float:
        multiplier = max(1.0, float(SILENCE_NUDGE_BACKOFF_MULTIPLIER))
        grown = max(current_interval + 1.0, current_interval * multiplier)
        max_interval = max(1.0, float(SILENCE_NUDGE_MAX_INTERVAL_SECONDS))
        return min(grown, max_interval)

    # Silence nudge state — shared between upstream_task and silence_nudge_task.
    last_client_activity = time.monotonic()
    silence_nudge_count = 0
    agent_busy = False
    silence_nudge_due_at, silence_nudge_interval = _reset_silence_nudge_schedule(
        last_client_activity
    )

    # ═══ Bidi-Streaming Tasks ═══

    async def keepalive_task() -> None:
        """Send periodic pings to detect dead connections and prevent proxy timeouts."""
        while session_alive.is_set():
            try:
                await asyncio.sleep(25)
                if not session_alive.is_set():
                    break
                await websocket.send_text(json.dumps({
                    "type": "ping",
                    "ts": int(time.time() * 1000),
                }))
            except Exception:
                break  # WebSocket closed; stop keepalive

    async def upstream_task() -> None:
        """Receives from WebSocket, sends to LiveRequestQueue."""
        nonlocal last_client_activity
        nonlocal silence_nudge_count
        nonlocal silence_nudge_due_at
        nonlocal silence_nudge_interval
        nonlocal agent_busy
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                raise WebSocketDisconnect(code=message.get("code", 1000))

            audio_data = message.get("bytes")
            text_data = message.get("text")

            # Binary frames: audio data
            if audio_data is not None:
                now = time.monotonic()
                last_client_activity = now
                silence_nudge_count = 0
                agent_busy = True
                silence_nudge_due_at, silence_nudge_interval = _reset_silence_nudge_schedule(
                    now
                )
                audio_blob = types.Blob(
                    mime_type="audio/pcm;rate=16000", data=audio_data
                )
                live_request_queue.send_realtime(audio_blob)

            # Text frames: JSON messages
            elif text_data is not None:
                try:
                    json_message = json.loads(text_data)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "code": "INVALID_JSON",
                                "message": "Malformed JSON payload",
                            }
                        )
                    )
                    continue

                # Any valid client JSON message counts as activity.
                msg_type = json_message.get("type", "")
                if msg_type in ("text", "image", "negotiate") or (
                    msg_type == "activity_start" and manual_vad_active
                ):
                    now = time.monotonic()
                    last_client_activity = now
                    silence_nudge_count = 0
                    if msg_type in ("text", "image", "negotiate"):
                        agent_busy = True
                    silence_nudge_due_at, silence_nudge_interval = _reset_silence_nudge_schedule(
                        now
                    )

                if msg_type == "text":
                    content = types.Content(
                        parts=[types.Part(text=json_message["text"])]
                    )
                    live_request_queue.send_content(content)

                elif msg_type == "image":
                    mime_type = json_message.get("mimeType", "image/jpeg")
                    if not _check_rate_limit(client_ip, "upload", UPLOAD_RATE_LIMIT):
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "code": "RATE_LIMIT_EXCEEDED",
                            "message": "Upload rate limit exceeded",
                        }))
                        continue

                    image_b64 = json_message.get("data")
                    if not isinstance(image_b64, str):
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "code": "INVALID_IMAGE_PAYLOAD",
                            "message": "Image payload must be base64 string",
                        }))
                        continue

                    try:
                        image_data = base64.b64decode(image_b64, validate=True)
                    except (binascii.Error, ValueError):
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "code": "INVALID_BASE64_IMAGE",
                            "message": "Image payload is not valid base64",
                        }))
                        continue

                    try:
                        _validate_upload_bytes(mime_type, image_data)
                    except ValueError as exc:
                        code = str(exc)
                        if code == "UPLOAD_TOO_LARGE":
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "code": "UPLOAD_TOO_LARGE",
                                "message": f"Image exceeds {MAX_UPLOAD_BYTES} bytes",
                            }))
                            continue
                        if code == "MIME_TYPE_NOT_ALLOWED":
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "code": "MIME_TYPE_NOT_ALLOWED",
                                "message": "Unsupported image MIME type",
                            }))
                            continue
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "code": "INVALID_UPLOAD",
                            "message": "Invalid upload payload",
                        }))
                        continue

                    cache_latest_image(
                        user_id=user_id,
                        session_id=resolved_session_id,
                        image_data=image_data,
                        mime_type=mime_type,
                    )
                    await websocket.send_text(json.dumps({
                        "type": "image_received",
                        "status": "analyzing",
                    }))

                    image_blob = types.Blob(
                        mime_type=mime_type, data=image_data
                    )
                    live_request_queue.send_realtime(image_blob)
                    live_request_queue.send_content(
                        types.Content(
                            parts=[types.Part(
                                text=(
                                    "Customer uploaded a device photo. "
                                    "Transfer to vision_agent and call "
                                    "analyze_device_image_tool now."
                                )
                            )]
                        )
                    )

                elif msg_type == "config":
                    requested_industry = json_message.get("industry", industry)
                    if not isinstance(requested_industry, str):
                        requested_industry = industry
                    requested_industry = requested_industry.strip().lower() or industry

                    requested_company = _normalize_company_id(
                        json_message.get(
                            "companyId",
                            json_message.get("company_id", company_id),
                        )
                    )

                    if requested_industry != session_industry:
                        await websocket.send_text(json.dumps(_append_canonical_lock_fields({
                            "type": "error",
                            "code": "INDUSTRY_LOCKED",
                            "message": (
                                "Industry is set during onboarding and cannot be changed "
                                "during an active session."
                            ),
                            "industry": session_industry,
                            "companyId": company_id,
                            "requestedIndustry": requested_industry,
                        }, _ss)))
                    elif requested_company != company_id:
                        await websocket.send_text(json.dumps(_append_canonical_lock_fields({
                            "type": "error",
                            "code": "COMPANY_LOCKED",
                            "message": (
                                "Company profile is selected during onboarding and cannot "
                                "be changed during an active session."
                            ),
                            "companyId": company_id,
                            "requestedCompanyId": requested_company,
                            "industry": session_industry,
                        }, _ss)))
                    else:
                        current_voice = (
                            _ss.get("app:voice")
                            if isinstance(_ss.get("app:voice"), str)
                            else None
                        ) or _voice_for_industry(session_industry)
                        await websocket.send_text(json.dumps(_build_session_started_message(
                            session_id=resolved_session_id,
                            industry=session_industry,
                            company_id=company_id,
                            voice=current_voice,
                            manual_vad_active=manual_vad_active,
                            session_state=_ss,
                        )))

                elif msg_type == "negotiate":
                    action = json_message.get("action", "counter")
                    amount = json_message.get("counterOffer", 0)
                    content = types.Content(
                        parts=[types.Part(
                            text=f"Customer negotiation: {action}. Counter-offer amount: {amount}"
                        )]
                    )
                    live_request_queue.send_content(content)

                elif msg_type == "activity_start":
                    if manual_vad_active and hasattr(live_request_queue, "send_activity_start"):
                        live_request_queue.send_activity_start()

                elif msg_type == "activity_end":
                    if manual_vad_active and hasattr(live_request_queue, "send_activity_end"):
                        live_request_queue.send_activity_end()

                elif msg_type == "client_ping":
                    client_ts = json_message.get("clientTs")
                    seq = json_message.get("seq")
                    await websocket.send_text(json.dumps({
                        "type": "client_pong",
                        "seq": seq,
                        "clientTs": client_ts,
                        "serverTs": int(time.time() * 1000),
                    }))

                else:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "code": "UNSUPPORTED_MESSAGE_TYPE",
                        "message": "Unsupported client message type",
                    }))

    async def downstream_task() -> None:
        """Receives Events from run_live(), transforms to ServerMessages.

        Turn-state tracking: the server owns transcription lifecycle.
        Input partials stream while the user speaks; a final (partial=false)
        is emitted when the agent begins responding or the turn completes.
        This keeps the protocol correct regardless of frontend implementation.
        """
        nonlocal last_client_activity
        nonlocal silence_nudge_due_at
        nonlocal silence_nudge_interval
        nonlocal agent_busy
        current_agent = "ekaette_router"
        last_input_text = ""
        last_output_text = ""
        receiving_input = False
        input_finalized = False   # late-partial suppression
        output_finalized = False  # late-partial suppression
        last_structured_message_id = 0
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0
        session_cost_usd = 0.0

        async def _finalize_input() -> None:
            """Send a non-partial input transcription to close the user's turn."""
            nonlocal last_input_text, receiving_input, input_finalized
            if receiving_input and last_input_text:
                await websocket.send_text(json.dumps({
                    "type": "transcription",
                    "role": "user",
                    "text": last_input_text,
                    "partial": False,
                }))
            last_input_text = ""
            receiving_input = False
            input_finalized = True

        async def _finalize_output() -> None:
            """Send a non-partial output transcription to close the agent's turn."""
            nonlocal last_output_text, output_finalized
            if last_output_text:
                await websocket.send_text(json.dumps({
                    "type": "transcription",
                    "role": "agent",
                    "text": last_output_text,
                    "partial": False,
                }))
            last_output_text = ""
            output_finalized = True

        async for event in runner.run_live(
            user_id=user_id,
            session_id=resolved_session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            try:
                # ─── Audio + Text content ───
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        # Audio → binary WebSocket frame (lowest latency)
                        if (
                            part.inline_data
                            and part.inline_data.data
                            and part.inline_data.mime_type
                            and "audio" in part.inline_data.mime_type
                        ):
                            agent_busy = True
                            audio_bytes = part.inline_data.data
                            if isinstance(audio_bytes, str):
                                audio_bytes = base64.b64decode(audio_bytes)
                            await websocket.send_bytes(audio_bytes)

                        # Text → transcription (text-mode fallback only)
                        elif part.text and not is_native_audio:
                            agent_busy = True
                            await websocket.send_text(json.dumps({
                                "type": "transcription",
                                "role": "agent",
                                "text": part.text,
                                "partial": not bool(event.turn_complete),
                            }))

                # ─── Input transcription (user's speech → text) ───
                if event.input_transcription:
                    text = getattr(event.input_transcription, "text", None)
                    finished = getattr(event.input_transcription, "finished", False)
                    if text:
                        if input_finalized and not finished:
                            # Suppress late partials after input was already finalized
                            pass
                        else:
                            if input_finalized:
                                # New final after prior finalization → new utterance
                                input_finalized = False
                            last_input_text = text
                            receiving_input = True
                            is_partial = not finished
                            await websocket.send_text(json.dumps({
                                "type": "transcription",
                                "role": "user",
                                "text": text,
                                "partial": is_partial,
                            }))
                            if finished:
                                last_input_text = ""
                                receiving_input = False
                                input_finalized = True

                # ─── Output transcription (agent's speech → text) ───
                if event.output_transcription:
                    text = getattr(event.output_transcription, "text", None)
                    finished = getattr(event.output_transcription, "finished", False)
                    if text:
                        agent_busy = True
                        if output_finalized and not finished:
                            # Suppress late partials after output was already finalized
                            pass
                        else:
                            if output_finalized:
                                output_finalized = False
                            # Agent started responding → finalize user's input
                            if receiving_input:
                                await _finalize_input()
                            last_output_text = text
                            is_partial = not finished
                            await websocket.send_text(json.dumps({
                                "type": "transcription",
                                "role": "agent",
                                "text": text,
                                "partial": is_partial,
                            }))
                            if finished:
                                last_output_text = ""
                                output_finalized = True

                # ─── Interrupted → finalize + clear playback ───
                if event.interrupted:
                    await _finalize_input()
                    await _finalize_output()
                    agent_busy = False
                    await websocket.send_text(json.dumps({
                        "type": "interrupted",
                        "interrupted": True,
                    }))

                # ─── Agent transfer ───
                if event.actions and event.actions.transfer_to_agent:
                    new_agent = event.actions.transfer_to_agent
                    if not isinstance(new_agent, str) or not new_agent.strip():
                        logger.debug("Ignoring invalid transfer target: %r", new_agent)
                    elif new_agent == current_agent:
                        logger.debug(
                            "Suppressing no-op agent_transfer (already on %s)", new_agent
                        )
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "agent_transfer",
                            "from": current_agent,
                            "to": new_agent,
                        }))
                        current_agent = new_agent
                        await websocket.send_text(json.dumps({
                            "type": "agent_status",
                            "agent": new_agent,
                            "status": "active",
                        }))

                # ─── Structured ServerMessages from callbacks/state delta ───
                state_delta = event.actions.state_delta if event.actions else None
                structured = _extract_server_message_from_state_delta(state_delta)
                if structured:
                    raw_id = structured.get("id", 0)
                    try:
                        structured_id = int(raw_id)
                    except (TypeError, ValueError):
                        structured_id = 0

                    if structured_id > last_structured_message_id:
                        payload = {k: v for k, v in structured.items() if k != "id"}
                        await websocket.send_text(json.dumps(payload))
                        last_structured_message_id = structured_id

                # ─── Turn complete → finalize output + status ───
                if event.turn_complete:
                    await _finalize_input()
                    await _finalize_output()
                    # Anchor silence nudges to when the agent actually finishes,
                    # not when the user last spoke. This avoids check-in nudges
                    # racing right after a long agent response.
                    now = time.monotonic()
                    agent_busy = False
                    if now >= last_client_activity:
                        silence_nudge_due_at = now + max(1.0, float(silence_nudge_interval))
                    # Reset suppression flags for the next turn
                    input_finalized = False
                    output_finalized = False
                    await websocket.send_text(json.dumps({
                        "type": "agent_status",
                        "agent": event.author or current_agent,
                        "status": "idle",
                    }))

                # ─── Usage metadata ───
                if event.usage_metadata:
                    logger.debug("Token usage: %s", event.usage_metadata)
                    prompt_tokens = _usage_int(
                        event.usage_metadata, "prompt_token_count", "prompt_tokens"
                    )
                    completion_tokens = _usage_int(
                        event.usage_metadata,
                        "candidates_token_count",
                        "completion_token_count",
                        "completion_tokens",
                    )
                    total_tokens = _usage_int(
                        event.usage_metadata, "total_token_count", "total_tokens"
                    )
                    if total_tokens <= 0:
                        total_tokens = prompt_tokens + completion_tokens

                    if total_tokens > 0:
                        session_prompt_tokens += prompt_tokens
                        session_completion_tokens += completion_tokens
                        session_total_tokens += total_tokens
                        session_cost_usd += (
                            (prompt_tokens / 1_000_000) * TOKEN_PRICE_PROMPT_PER_MILLION
                            + (completion_tokens / 1_000_000) * TOKEN_PRICE_COMPLETION_PER_MILLION
                        )

                        if DEBUG_TELEMETRY:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "telemetry",
                                        "promptTokens": prompt_tokens,
                                        "completionTokens": completion_tokens,
                                        "totalTokens": total_tokens,
                                        "sessionPromptTokens": session_prompt_tokens,
                                        "sessionCompletionTokens": session_completion_tokens,
                                        "sessionTotalTokens": session_total_tokens,
                                        "sessionCostUsd": round(session_cost_usd, 6),
                                    }
                                )
                            )

                # ─── Session resumption token ───
                if event.live_session_resumption_update:
                    logger.debug("Session resumption token received")
                    token_val = getattr(
                        event.live_session_resumption_update, "token", None
                    )
                    if isinstance(token_val, str) and token_val:
                        await websocket.send_text(json.dumps({
                            "type": "session_ending",
                            "reason": "session_resumption",
                            "resumptionToken": token_val,
                        }))

                # ─── GoAway ───
                go_away = getattr(event, "go_away", None)
                if go_away is not None:
                    time_left = getattr(go_away, "time_left", None)
                    logger.warning("GoAway received, timeLeft=%s", time_left)
                    await websocket.send_text(json.dumps({
                        "type": "session_ending",
                        "reason": "go_away",
                        "timeLeftMs": int(time_left.total_seconds() * 1000)
                        if time_left is not None
                        else None,
                    }))

            except Exception as e:
                logger.error("Error processing downstream event: %s", e, exc_info=True)

        # Live API session ended naturally (timeout / GoAway completion).
        # Notify client so it can decide to reconnect gracefully.
        logger.info("downstream_task: run_live loop ended for session %s", _sanitize_log(resolved_session_id))
        try:
            await websocket.send_text(json.dumps({
                "type": "session_ending",
                "reason": "live_session_ended",
            }))
        except Exception:
            pass  # Client already disconnected; safe to ignore

    async def silence_nudge_task() -> None:
        """Nudge the model when the customer has been silent too long.

        Checks roughly every second. After ``SILENCE_NUDGE_SECONDS`` of no
        client audio/text activity, injects a system hint so the model can
        proactively check in. Each later nudge waits longer than the previous
        one (progressive backoff), up to ``SILENCE_NUDGE_MAX_INTERVAL_SECONDS``.
        Stops after ``SILENCE_NUDGE_MAX`` nudges per silence period (reset when
        client speaks again).
        """
        nonlocal silence_nudge_count, silence_nudge_due_at, silence_nudge_interval, agent_busy
        if SILENCE_NUDGE_SECONDS <= 0:
            return  # disabled
        while session_alive.is_set():
            await asyncio.sleep(1)
            if not session_alive.is_set():
                break
            now = time.monotonic()
            if now < silence_nudge_due_at:
                continue
            if agent_busy:
                continue
            if silence_nudge_count >= SILENCE_NUDGE_MAX:
                continue
            silence_nudge_count += 1
            ordinal = silence_nudge_count
            if ordinal == 1:
                hint = (
                    "[System: the customer has been silent for several seconds. "
                    "Gently check if they are still there. Keep it brief and natural.]"
                )
            else:
                hint = (
                    "[System: the customer is still silent after your last check-in. "
                    "Say something like 'I'll be right here whenever you're ready' "
                    "and then wait quietly. Do not check in again.]"
                )
            try:
                live_request_queue.send_content(
                    types.Content(parts=[types.Part(text=hint)])
                )
            except Exception:
                break  # queue closed
            silence_nudge_interval = _next_silence_nudge_interval(silence_nudge_interval)
            silence_nudge_due_at = now + silence_nudge_interval

    try:
        upstream = asyncio.create_task(upstream_task(), name="upstream_task")
        downstream = asyncio.create_task(downstream_task(), name="downstream_task")
        keepalive = asyncio.create_task(keepalive_task(), name="keepalive_task")
        nudge = asyncio.create_task(silence_nudge_task(), name="silence_nudge_task")
        streaming_tasks = {upstream, downstream}

        done, pending = await asyncio.wait(
            streaming_tasks, return_when=asyncio.FIRST_EXCEPTION
        )

        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc

        for task in pending | {keepalive, nudge}:
            task.cancel()
        remaining = pending | {keepalive, nudge}
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
    except WebSocketDisconnect:
        logger.debug("Client disconnected normally")
    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)
    finally:
        session_alive.clear()
        live_request_queue.close()


# ═══ Static File Serving (built frontend) ═══
_frontend_dist = Path(__file__).parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
    logger.info("Serving frontend from %s", _frontend_dist)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
