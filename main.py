"""Ekaette — FastAPI Backend with ADK Bidi-Streaming."""

import asyncio
import base64
import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
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
from app.api.v1.admin import admin_router  # noqa: E402
from app.api.v1.at import at_router  # noqa: E402
from app.api.v1.internal import internal_router  # noqa: E402
from app.api.v1.admin import settings as admin_settings  # noqa: E402
from app.api.v1.admin.auth import _extract_admin_auth_context  # noqa: E402
from app.api.v1.admin.service_companies import _resolve_company_for_bootstrap  # noqa: E402
from app.api.v1.admin.shared import (  # noqa: E402
    build_admin_observability_fields,
    format_observability_fields,
    sync_runtime_clients as sync_admin_runtime_clients,
)
from app.api.v1.public import core_helpers as public_core  # noqa: E402
from app.api.v1.public import http_endpoints as public_http  # noqa: E402
from app.api.v1.public import settings as public_settings  # noqa: E402
from app.api.v1.realtime import ws_stream as realtime_ws  # noqa: E402
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
from app.configs.compaction_factory import create_app as create_adk_app  # noqa: E402
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

session_service = None
industry_config_client = None
company_config_client = None
memory_service = None
runner = None
TOKEN_CLIENT = None
_PUBLIC_RUNTIME_WIRED = False
_REALTIME_RUNTIME_WIRED = False


def _ensure_singletons_initialized() -> None:
    """Initialize session, config, memory, and runner singletons if not already set.

    Called both at module level (test-safe fallback) and in the ASGI lifespan.
    """
    global session_service, industry_config_client, company_config_client, memory_service, runner

    if session_service is None:
        session_service = create_session_service()
    if industry_config_client is None:
        industry_config_client = create_industry_config_client()
    if company_config_client is None:
        company_config_client = create_company_config_client()
    if memory_service is None:
        memory_service = create_memory_service()
    if runner is None:
        adk_app = create_adk_app(name=SESSION_APP_NAME, root_agent=ekaette_router)
        runner = Runner(
            app=adk_app,
            session_service=session_service,
            memory_service=memory_service,
        )
    sync_admin_runtime_clients(
        industry_client=industry_config_client,
        company_client=company_config_client,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_service, industry_config_client, company_config_client, memory_service, runner, TOKEN_CLIENT

    _ensure_singletons_initialized()
    app.state.session_service = session_service
    app.state.industry_config_client = industry_config_client
    app.state.company_config_client = company_config_client
    app.state.memory_service = memory_service
    app.state.runner = runner
    app.state.token_client = TOKEN_CLIENT
    # Prime runtime wiring at startup when sync mode resolves to startup.
    _sync_public_runtime()
    _sync_realtime_runtime()
    yield


app = FastAPI(title="Ekaette", lifespan=lifespan)


def _parse_allowlist(raw_origins: str) -> list[str]:
    return public_core.parse_allowlist(raw_origins)


# ═══ CORS Middleware — explicit allowlist, no wildcard ═══
ALLOWED_ORIGINS = list(public_settings.ALLOWED_ORIGINS)
ALLOWED_ORIGIN_SET = set(public_settings.ALLOWED_ORIGIN_SET)


def _is_origin_allowed(origin: str | None) -> bool:
    return public_core.is_origin_allowed(origin, ALLOWED_ORIGIN_SET)


def _is_websocket_origin_allowed(origin: str | None) -> bool:
    return public_core.is_websocket_origin_allowed(
        origin,
        ALLOWED_ORIGIN_SET,
        allow_missing_ws_origin=bool(globals().get("ALLOW_MISSING_WS_ORIGIN", False)),
    )


# Regex pattern for characters that enable log injection (newlines + control chars).
_LOG_UNSAFE_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")
_WS_PATH_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _sanitize_log(value: str | None) -> str:
    return public_core.sanitize_log(value, _LOG_UNSAFE_RE)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def admin_auth_middleware(request: Request, call_next):
    is_admin_path = request.url.path.startswith("/api/v1/admin/")
    tenant_id = ""
    path_parts: list[str] = []
    company_id = ""
    if is_admin_path:
        tenant_id = _normalize_tenant_id(
            request.query_params.get("tenantId"),
            default=_normalize_tenant_id(request.headers.get("x-tenant-id"), default="public"),
        )
        path_parts = request.url.path.strip("/").split("/")
        company_id = path_parts[4] if len(path_parts) >= 5 and path_parts[3] == "companies" else ""
    if is_admin_path:
        context, error_response = _extract_admin_auth_context(request)
        if error_response:
            result_code = ""
            try:
                raw_body = getattr(error_response, "body", b"")
                if isinstance(raw_body, (bytes, bytearray)) and raw_body:
                    parsed = json.loads(raw_body.decode("utf-8"))
                    if isinstance(parsed, dict) and isinstance(parsed.get("code"), str):
                        result_code = parsed["code"]
            except Exception:
                result_code = ""
            fields = build_admin_observability_fields(
                tenant_id=tenant_id,
                company_id=company_id or None,
                industry_template_id=None,
                route=request.url.path,
                method=request.method,
                auth_mode=admin_settings.ADMIN_AUTH_MODE,
                idempotency_scope=f"{request.method.lower()}:{request.url.path}",
                idempotency_state="fresh",
                result_code=result_code,
                status_code=error_response.status_code,
            )
            logger.info("admin_request %s", format_observability_fields(fields))
            return error_response
        if context is not None:
            request.state.admin_auth_context = context

    response = await call_next(request)

    if is_admin_path:
        idempotency_state = (
            "replayed" if response.headers.get("Idempotency-Replayed") == "true" else "fresh"
        )
        idempotency_scope = f"{request.method.lower()}:{request.url.path}"
        result_code = ""
        try:
            raw_body = getattr(response, "body", b"")
            if isinstance(raw_body, (bytes, bytearray)) and raw_body:
                parsed = json.loads(raw_body.decode("utf-8"))
                if isinstance(parsed, dict) and isinstance(parsed.get("code"), str):
                    result_code = parsed["code"]
        except Exception:
            result_code = ""
        fields = build_admin_observability_fields(
            tenant_id=tenant_id,
            company_id=company_id or None,
            industry_template_id=None,
            route=request.url.path,
            method=request.method,
            auth_mode=admin_settings.ADMIN_AUTH_MODE,
            idempotency_scope=idempotency_scope,
            idempotency_state=idempotency_state,
            result_code=result_code,
            status_code=response.status_code,
        )
        logger.info("admin_request %s", format_observability_fields(fields))

    return response


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
MAX_UPLOAD_BYTES = public_settings.MAX_UPLOAD_BYTES
ALLOWED_UPLOAD_MIME_TYPES = set(public_settings.ALLOWED_UPLOAD_MIME_TYPES)


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
    return public_core.usage_int(usage, *names)


def _voice_for_industry(industry: str) -> str:
    return public_core.voice_for_industry(industry)


DEFAULT_COMPANY_ID = (
    public_settings.DEFAULT_COMPANY_ID
)
_COMPANY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_TEMPLATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,127}$")
_CONNECTOR_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
KNOWLEDGE_IMPORT_MAX_BYTES = public_settings.KNOWLEDGE_IMPORT_MAX_BYTES


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


def _normalize_company_id_strict(raw_value: object) -> str | None:
    """Sanitize company ID without default fallback (for strict API paths)."""
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if not _COMPANY_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _normalize_connector_id(raw_value: object) -> str | None:
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if not _CONNECTOR_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _native_audio_live_config(
    industry: str,
    voice_override: str | None = None,
) -> dict[str, object]:
    return public_core.native_audio_live_config(
        industry=industry,
        voice_override=voice_override,
        speech_language_code=SPEECH_LANGUAGE_CODE,
        types_module=types,
        voice_for_industry_fn=_voice_for_industry,
    )


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
    return public_core.registry_mismatch_response(
        requested_template_id=requested_template_id,
        resolved_template_id=resolved_template_id,
        require_company_template_match=_registry_require_company_template_match(),
    )


def _legacy_industry_alias_from_registry_config(
    config: object | None,
    *,
    fallback: str,
) -> str:
    return public_core.legacy_industry_alias_from_registry_config(
        config,
        fallback=fallback,
    )


def _canonical_state_updates_from_registry(config: object) -> dict[str, object]:
    try:
        return public_core.canonical_state_updates_from_registry(config)
    except Exception:
        return {}


def _build_session_started_message(
    *,
    session_id: str,
    industry: str,
    company_id: str,
    voice: str,
    manual_vad_active: bool,
    session_state: dict[str, object] | None,
) -> dict[str, object]:
    return public_core.build_session_started_message(
        session_id=session_id,
        industry=industry,
        company_id=company_id,
        voice=voice,
        manual_vad_active=manual_vad_active,
        session_state=session_state,
    )


def _append_canonical_lock_fields(
    payload: dict[str, object],
    session_state: dict[str, object] | None,
) -> dict[str, object]:
    return public_core.append_canonical_lock_fields(payload, session_state)


# ═══ Session/Runner Singletons (initialized in lifespan, with test-safe fallback) ═══
_ensure_singletons_initialized()


# ═══ HTTP Endpoints ═══

@app.get("/health")
async def health():
    """Health check endpoint for Cloud Run and monitoring."""
    return {"status": "ok", "app": APP_NAME}


# ═══ Rate Limiting (simple in-memory, per-IP+endpoint) ═══
_rate_limit_buckets: dict[str, list[float]] = {}
TOKEN_RATE_LIMIT = public_settings.TOKEN_RATE_LIMIT  # requests/minute
UPLOAD_RATE_LIMIT = public_settings.UPLOAD_RATE_LIMIT  # requests/minute
RATE_LIMIT_WINDOW = public_settings.RATE_LIMIT_WINDOW  # seconds
RATE_LIMIT_MAX_BUCKETS = public_settings.RATE_LIMIT_MAX_BUCKETS
_rate_limit_last_global_prune = 0.0


def _check_rate_limit(client_ip: str, bucket: str, limit: int) -> bool:
    global _rate_limit_last_global_prune
    allowed, updated_last_prune = public_core.check_rate_limit(
        client_ip=client_ip,
        bucket=bucket,
        limit=limit,
        window_seconds=RATE_LIMIT_WINDOW,
        max_buckets=RATE_LIMIT_MAX_BUCKETS,
        buckets=_rate_limit_buckets,
        last_global_prune=_rate_limit_last_global_prune,
    )
    _rate_limit_last_global_prune = updated_last_prune
    return allowed


def _client_ip_from_request(request: Request) -> str:
    return public_core.client_ip_from_request(request)


def _origin_or_reject(origin: str | None, *, endpoint: str) -> JSONResponse | None:
    """Return a 403 response when HTTP origin is invalid.

    Missing Origin is accepted for same-origin proxy/server-to-server traffic
    and logged at debug level for observability.
    """
    if origin is None:
        logger.debug(
            "HTTP request accepted without Origin header endpoint=%s",
            _sanitize_log(endpoint),
        )
        return None
    if not _is_origin_allowed(origin):
        return JSONResponse(
            status_code=403,
            content={"error": "Origin not allowed"},
        )
    return None


def _tenant_allowed(tenant_id: str) -> bool:
    """Check tenant against allowed list used for external-facing endpoints."""
    return not TOKEN_ALLOWED_TENANTS or tenant_id in TOKEN_ALLOWED_TENANTS


ALLOW_MISSING_WS_ORIGIN = public_settings.ALLOW_MISSING_WS_ORIGIN



# ═══ Ephemeral Token Endpoint ═══
GEMINI_API_KEY = public_settings.GEMINI_API_KEY
TOKEN_MAX_USES = public_settings.TOKEN_MAX_USES
TOKEN_TTL_SECONDS = public_settings.TOKEN_TTL_SECONDS
TOKEN_NEW_SESSION_TTL_SECONDS = public_settings.TOKEN_NEW_SESSION_TTL_SECONDS
TOKEN_ALLOWED_TENANTS = set(public_settings.TOKEN_ALLOWED_TENANTS)
MANUAL_VAD = public_settings.MANUAL_VAD
SPEECH_LANGUAGE_CODE = public_settings.SPEECH_LANGUAGE_CODE
AUTO_VAD_PREFIX_PADDING_MS = public_settings.AUTO_VAD_PREFIX_PADDING_MS
AUTO_VAD_SILENCE_DURATION_MS = public_settings.AUTO_VAD_SILENCE_DURATION_MS
SILENCE_NUDGE_SECONDS = public_settings.SILENCE_NUDGE_SECONDS
SILENCE_NUDGE_MAX = public_settings.SILENCE_NUDGE_MAX
SILENCE_NUDGE_BACKOFF_MULTIPLIER = public_settings.SILENCE_NUDGE_BACKOFF_MULTIPLIER
SILENCE_NUDGE_MAX_INTERVAL_SECONDS = public_settings.SILENCE_NUDGE_MAX_INTERVAL_SECONDS
DEBUG_TELEMETRY = public_settings.DEBUG_TELEMETRY
TOKEN_PRICE_PROMPT_PER_MILLION = public_settings.TOKEN_PRICE_PROMPT_PER_MILLION
TOKEN_PRICE_COMPLETION_PER_MILLION = public_settings.TOKEN_PRICE_COMPLETION_PER_MILLION
WS_TOKEN_SECRET = public_settings.WS_TOKEN_SECRET
WS_TOKEN_TTL_SECONDS = public_settings.WS_TOKEN_TTL_SECONDS
REGISTRY_ENABLED = public_settings.REGISTRY_ENABLED
REGISTRY_REQUIRE_COMPANY_TEMPLATE_MATCH = public_settings.REGISTRY_REQUIRE_COMPANY_TEMPLATE_MATCH
LIVE_MODEL_CANDIDATES = public_settings.build_live_model_candidates(
    ekaette_router.model,
    get_live_model_candidates(),
)


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


def _effective_runtime_sync_mode() -> str:
    """Resolve runtime sync mode with test-safe defaults.

    Modes:
    - request: configure runtime wiring on every request/connection.
    - startup: configure once at startup (or first request fallback).
    - auto: request mode under pytest, startup mode otherwise.
    """
    raw = os.getenv("RUNTIME_SYNC_MODE", "auto").strip().lower()
    if raw not in {"auto", "request", "startup"}:
        raw = "auto"
    if raw == "auto":
        return "request" if "pytest" in sys.modules else "startup"
    return raw


def _sync_public_runtime() -> None:
    """Sync public HTTP runtime dependencies into extracted module handlers."""
    global _PUBLIC_RUNTIME_WIRED
    mode = _effective_runtime_sync_mode()
    if mode == "startup" and _PUBLIC_RUNTIME_WIRED:
        return
    public_http.configure_runtime(
        logger=logger,
        _origin_or_reject=_origin_or_reject,
        _client_ip_from_request=_client_ip_from_request,
        _check_rate_limit=_check_rate_limit,
        TOKEN_RATE_LIMIT=TOKEN_RATE_LIMIT,
        RATE_LIMIT_WINDOW=RATE_LIMIT_WINDOW,
        TOKEN_ALLOWED_TENANTS=TOKEN_ALLOWED_TENANTS,
        registry_log_context=registry_log_context,
        _registry_enabled=_registry_enabled,
        TOKEN_CLIENT=TOKEN_CLIENT,
        TOKEN_TTL_SECONDS=TOKEN_TTL_SECONDS,
        TOKEN_NEW_SESSION_TTL_SECONDS=TOKEN_NEW_SESSION_TTL_SECONDS,
        _normalize_tenant_id=_normalize_tenant_id,
        _normalize_company_id=_normalize_company_id,
        _normalize_template_id=_normalize_template_id,
        _resolve_registry_runtime_config=_resolve_registry_runtime_config,
        RegistrySchemaVersionError=RegistrySchemaVersionError,
        _registry_mismatch_response=_registry_mismatch_response,
        _sanitize_log=_sanitize_log,
        _native_audio_live_config=_native_audio_live_config,
        REALTIME_INPUT_CONFIG=REALTIME_INPUT_CONFIG,
        LIVE_MODEL_CANDIDATES=LIVE_MODEL_CANDIDATES,
        types=types,
        TOKEN_MAX_USES=TOKEN_MAX_USES,
        _legacy_industry_alias_from_registry_config=_legacy_industry_alias_from_registry_config,
        MANUAL_VAD_ACTIVE=MANUAL_VAD_ACTIVE,
        _voice_for_industry=_voice_for_industry,
        industry_config_client=industry_config_client,
        _tenant_allowed=_tenant_allowed,
        _resolve_company_for_bootstrap=_resolve_company_for_bootstrap,
        MAX_UPLOAD_BYTES=MAX_UPLOAD_BYTES,
        ALLOWED_UPLOAD_MIME_TYPES=ALLOWED_UPLOAD_MIME_TYPES,
        UPLOAD_RATE_LIMIT=UPLOAD_RATE_LIMIT,
        _validate_upload_bytes=_validate_upload_bytes,
    )
    _PUBLIC_RUNTIME_WIRED = True


@app.post("/api/token")
async def create_ephemeral_token(
    payload: TokenRequestPayload,
    request: Request,
):
    _sync_public_runtime()
    return await public_http.create_ephemeral_token(payload, request)


# ═══ Onboarding Config Endpoint ═══


@app.get("/api/onboarding/config")
async def get_onboarding_config(request: Request):
    _sync_public_runtime()
    return await public_http.get_onboarding_config(request)


@app.get("/api/v1/runtime/bootstrap")
async def get_runtime_bootstrap(request: Request):
    _sync_public_runtime()
    return await public_http.get_runtime_bootstrap(request)


app.include_router(admin_router)
app.include_router(at_router)
app.include_router(internal_router)



@app.post("/api/upload/validate")
async def validate_upload(
    request: Request,
    file: UploadFile = File(...),
):
    _sync_public_runtime()
    return await public_http.validate_upload(request, file)


# ═══ WebSocket Endpoint ═══


def _sync_realtime_runtime() -> None:
    """Sync websocket runtime dependencies into the extracted realtime module."""
    global _REALTIME_RUNTIME_WIRED
    mode = _effective_runtime_sync_mode()
    if mode == "startup" and _REALTIME_RUNTIME_WIRED:
        return
    realtime_ws.configure_runtime(
        _WS_PATH_ID_RE=_WS_PATH_ID_RE,
        _is_websocket_origin_allowed=_is_websocket_origin_allowed,
        _sanitize_log=_sanitize_log,
        logger=logger,
        ekaette_router=ekaette_router,
        _normalize_template_id=_normalize_template_id,
        _normalize_tenant_id=_normalize_tenant_id,
        _tenant_allowed=_tenant_allowed,
        registry_log_context=registry_log_context,
        _registry_enabled=_registry_enabled,
        _normalize_company_id=_normalize_company_id,
        DEFAULT_COMPANY_ID=DEFAULT_COMPANY_ID,
        session_service=session_service,
        SESSION_APP_NAME=SESSION_APP_NAME,
        load_industry_config=load_industry_config,
        industry_config_client=industry_config_client,
        build_session_state=build_session_state,
        load_company_profile=load_company_profile,
        company_config_client=company_config_client,
        load_company_knowledge=load_company_knowledge,
        build_company_session_state=build_company_session_state,
        _resolve_registry_runtime_config=_resolve_registry_runtime_config,
        RegistrySchemaVersionError=RegistrySchemaVersionError,
        _canonical_state_updates_from_registry=_canonical_state_updates_from_registry,
        async_save_session_state=async_save_session_state,
        _registry_mismatch_response=_registry_mismatch_response,
        _voice_for_industry=_voice_for_industry,
        MANUAL_VAD_ACTIVE=MANUAL_VAD_ACTIVE,
        REALTIME_INPUT_CONFIG=REALTIME_INPUT_CONFIG,
        _native_audio_live_config=_native_audio_live_config,
        RunConfig=RunConfig,
        StreamingMode=StreamingMode,
        types=types,
        _build_session_started_message=_build_session_started_message,
        WS_TOKEN_SECRET=WS_TOKEN_SECRET,
        LiveRequestQueue=LiveRequestQueue,
        SILENCE_NUDGE_SECONDS=SILENCE_NUDGE_SECONDS,
        SILENCE_NUDGE_BACKOFF_MULTIPLIER=SILENCE_NUDGE_BACKOFF_MULTIPLIER,
        SILENCE_NUDGE_MAX_INTERVAL_SECONDS=SILENCE_NUDGE_MAX_INTERVAL_SECONDS,
        SILENCE_NUDGE_MAX=SILENCE_NUDGE_MAX,
        WebSocketDisconnect=WebSocketDisconnect,
        _append_canonical_lock_fields=_append_canonical_lock_fields,
        _extract_server_message_from_state_delta=_extract_server_message_from_state_delta,
        TOKEN_PRICE_PROMPT_PER_MILLION=TOKEN_PRICE_PROMPT_PER_MILLION,
        TOKEN_PRICE_COMPLETION_PER_MILLION=TOKEN_PRICE_COMPLETION_PER_MILLION,
        DEBUG_TELEMETRY=DEBUG_TELEMETRY,
        _usage_int=_usage_int,
        _check_rate_limit=_check_rate_limit,
        _validate_upload_bytes=_validate_upload_bytes,
        MAX_UPLOAD_BYTES=MAX_UPLOAD_BYTES,
        UPLOAD_RATE_LIMIT=UPLOAD_RATE_LIMIT,
        cache_latest_image=cache_latest_image,
        runner=runner,
    )
    _REALTIME_RUNTIME_WIRED = True


@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """WebSocket endpoint for bidirectional streaming with ADK."""
    _sync_realtime_runtime()
    await realtime_ws.websocket_endpoint(websocket, user_id, session_id)


# ═══ Static File Serving (built frontend) ═══
_frontend_dist = Path(__file__).parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
    logger.info("Serving frontend from %s", _frontend_dist)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
