"""Realtime websocket session initialization."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

from app.api.v1.realtime.models import SessionInitContext
from app.api.v1.realtime.runtime_cache import (
    bind_runtime_values,
    configure_runtime as configure_runtime_cache,
    get_runtime_value_safe,
)

logger = logging.getLogger(__name__)


def configure_runtime(**kwargs: Any) -> None:
    """Inject runtime dependencies from main module."""
    globals().update(kwargs)
    configure_runtime_cache(**kwargs)


async def initialize_session(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> SessionInitContext | None:
    """Validate websocket request and prepare live streaming session context."""
    (
        ws_path_id_re,
        is_websocket_origin_allowed_fn,
        sanitize_log_fn,
        router_obj,
        normalize_template_id_fn,
        normalize_tenant_id_fn,
        tenant_allowed_fn,
        registry_log_context_fn,
        registry_enabled_fn,
        normalize_company_id_fn,
        default_company_id,
        session_service_obj,
        session_app_name,
        load_industry_config_fn,
        industry_config_client_obj,
        build_session_state_fn,
        load_company_profile_fn,
        company_config_client_obj,
        load_company_knowledge_fn,
        build_company_session_state_fn,
        resolve_registry_runtime_config_fn,
        registry_schema_version_error_cls,
        canonical_state_updates_from_registry_fn,
        async_save_session_state_fn,
        registry_mismatch_response_fn,
        voice_for_industry_fn,
        manual_vad_active_setting,
        realtime_input_config,
        native_audio_live_config_fn,
        run_config_cls,
        streaming_mode_cls,
        types_mod,
        build_session_started_message_fn,
    ) = bind_runtime_values(
        "_WS_PATH_ID_RE",
        "_is_websocket_origin_allowed",
        "_sanitize_log",
        "ekaette_router",
        "_normalize_template_id",
        "_normalize_tenant_id",
        "_tenant_allowed",
        "registry_log_context",
        "_registry_enabled",
        "_normalize_company_id",
        "DEFAULT_COMPANY_ID",
        "session_service",
        "SESSION_APP_NAME",
        "load_industry_config",
        "industry_config_client",
        "build_session_state",
        "load_company_profile",
        "company_config_client",
        "load_company_knowledge",
        "build_company_session_state",
        "_resolve_registry_runtime_config",
        "RegistrySchemaVersionError",
        "_canonical_state_updates_from_registry",
        "async_save_session_state",
        "_registry_mismatch_response",
        "_voice_for_industry",
        "MANUAL_VAD_ACTIVE",
        "REALTIME_INPUT_CONFIG",
        "_native_audio_live_config",
        "RunConfig",
        "StreamingMode",
        "types",
        "_build_session_started_message",
    )

    if not ws_path_id_re.fullmatch(user_id or "") or not ws_path_id_re.fullmatch(session_id or ""):
        await websocket.close(code=1008, reason="Invalid path parameter")
        return None

    # ── WS token authentication (when WS_TOKEN_SECRET is configured) ──
    ws_secret = get_runtime_value_safe("WS_TOKEN_SECRET", "")
    if ws_secret:
        token_param = websocket.query_params.get("token")
        if not token_param:
            await websocket.close(code=4401, reason="Missing authentication token")
            return None
        from app.api.v1.public.ws_auth import validate_ws_token

        claims = validate_ws_token(token_param, expected_user_id=user_id)
        if claims is None:
            await websocket.close(code=4401, reason="Invalid or expired token")
            return None

    origin = websocket.headers.get("origin")
    ws_origin_allowed = is_websocket_origin_allowed_fn(origin)
    if origin is None:
        if ws_origin_allowed:
            logger.debug("WebSocket accepted without Origin header by policy")
        else:
            logger.debug("WebSocket missing Origin header rejected by policy")
    if not ws_origin_allowed:
        logger.warning("Rejected WebSocket origin: %s", sanitize_log_fn(origin))
        await websocket.close(code=1008, reason="Origin not allowed")
        return None

    logger.debug(
        "WebSocket connection request: user_id=%s, session_id=%s",
        sanitize_log_fn(user_id),
        sanitize_log_fn(session_id),
    )
    await websocket.accept()
    logger.debug("WebSocket connection accepted")
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Session init
    model_name = router_obj.model
    is_native_audio = "native-audio" in model_name.lower()

    # Parse onboarding context from query params.
    requested_industry = websocket.query_params.get("industry", "electronics")
    if not isinstance(requested_industry, str):
        requested_industry = "electronics"
    industry = requested_industry.strip().lower() or "electronics"
    requested_template_id = normalize_template_id_fn(
        websocket.query_params.get("industry_template_id")
        or websocket.query_params.get("industryTemplateId")
    )
    tenant_id = normalize_tenant_id_fn(
        websocket.query_params.get("tenant_id")
        or websocket.query_params.get("tenantId"),
        default="public",
    )
    if not tenant_allowed_fn(tenant_id):
        logger.warning(
            "WebSocket startup rejected (tenant forbidden) %s",
            registry_log_context_fn(
                tenant_id=tenant_id,
                registry_mode=registry_enabled_fn(),
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
        return None

    requested_company = websocket.query_params.get(
        "company_id",
        websocket.query_params.get("companyId", default_company_id),
    )
    company_id = normalize_company_id_fn(requested_company)

    uses_vertex_sessions = session_service_obj.__class__.__name__ == "VertexAiSessionService"
    resolved_session_id = session_id

    session = await session_service_obj.get_session(
        app_name=session_app_name, user_id=user_id, session_id=resolved_session_id
    )
    registry_config = None
    initial_state: dict[str, object] = {}

    if session:
        if isinstance(getattr(session, "id", None), str) and session.id:
            resolved_session_id = session.id
        # Session resumption should preserve prior selected industry.
        resumed_industry = session.state.get("app:industry")
        if isinstance(resumed_industry, str) and resumed_industry.strip():
            industry = resumed_industry.strip().lower()

        resumed_company = session.state.get("app:company_id")
        if isinstance(resumed_company, str) and resumed_company.strip():
            company_id = normalize_company_id_fn(resumed_company)

        resumed_tenant = session.state.get("app:tenant_id")
        if isinstance(resumed_tenant, str) and resumed_tenant.strip():
            tenant_id = normalize_tenant_id_fn(resumed_tenant, default=tenant_id)

        state_updates: dict[str, object] = {}
        if "app:industry_config" not in session.state:
            industry_config = await load_industry_config_fn(industry_config_client_obj, industry)
            state_updates.update(build_session_state_fn(industry_config, industry))

        if (
            "app:company_profile" not in session.state
            or "app:company_knowledge" not in session.state
            or "app:company_id" not in session.state
        ):
            if registry_enabled_fn():
                company_profile = await load_company_profile_fn(
                    company_config_client_obj, company_id, tenant_id=tenant_id
                )
                company_knowledge = await load_company_knowledge_fn(
                    company_config_client_obj, company_id, tenant_id=tenant_id
                )
            else:
                company_profile = await load_company_profile_fn(company_config_client_obj, company_id)
                company_knowledge = await load_company_knowledge_fn(
                    company_config_client_obj, company_id
                )
            state_updates.update(
                build_company_session_state_fn(
                    company_id=company_id,
                    profile=company_profile,
                    knowledge=company_knowledge,
                )
            )

        if registry_enabled_fn() and (
            "app:tenant_id" not in session.state
            or "app:industry_template_id" not in session.state
            or "app:capabilities" not in session.state
            or "app:registry_version" not in session.state
        ):
            try:
                registry_config = await resolve_registry_runtime_config_fn(
                    tenant_id=tenant_id,
                    company_id=company_id,
                )
            except registry_schema_version_error_cls as exc:
                logger.warning(
                    "WebSocket startup rejected (registry schema version, resumed session) %s details=%s",
                    registry_log_context_fn(
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
                return None
            if registry_config is not None:
                state_updates.update(canonical_state_updates_from_registry_fn(registry_config))

        if state_updates:
            await async_save_session_state_fn(
                session_service_obj,
                app_name=session_app_name,
                user_id=user_id,
                session_id=resolved_session_id,
                state_updates=state_updates,
            )
    else:
        if registry_enabled_fn():
            try:
                registry_config = await resolve_registry_runtime_config_fn(
                    tenant_id=tenant_id,
                    company_id=company_id,
                )
            except registry_schema_version_error_cls as exc:
                logger.warning(
                    "WebSocket startup rejected (registry schema version, fresh session) %s details=%s",
                    registry_log_context_fn(
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
                return None
            if registry_config is not None:
                # In strict mode, reject explicit template/company mismatches.
                if requested_template_id is not None:
                    mismatch_response = registry_mismatch_response_fn(
                        requested_template_id=requested_template_id,
                        resolved_template_id=getattr(registry_config, "industry_template_id", None),
                    )
                    if mismatch_response is not None:
                        logger.warning(
                            "WebSocket startup rejected (template/company mismatch) %s requested_template_id=%s resolved_template_id=%s",
                            registry_log_context_fn(
                                tenant_id=tenant_id,
                                company_id=company_id,
                                industry_template_id=getattr(registry_config, "industry_template_id", None),
                                registry_version=getattr(registry_config, "registry_version", None),
                                registry_mode=True,
                                source="ws_startup",
                            ),
                            sanitize_log_fn(requested_template_id),
                            sanitize_log_fn(getattr(registry_config, "industry_template_id", None)),
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
                        return None

                resolved_template_id = getattr(registry_config, "industry_template_id", None)
                if isinstance(resolved_template_id, str) and resolved_template_id:
                    industry = resolved_template_id
            else:
                logger.warning(
                    "WebSocket startup rejected (registry config missing) %s",
                    registry_log_context_fn(
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
                return None

        # Load onboarding context and build initial state
        industry_config = await load_industry_config_fn(
            industry_config_client_obj,
            industry,
        )
        if registry_enabled_fn():
            company_profile = await load_company_profile_fn(
                company_config_client_obj,
                company_id,
                tenant_id=tenant_id,
            )
            company_knowledge = await load_company_knowledge_fn(
                company_config_client_obj,
                company_id,
                tenant_id=tenant_id,
            )
        else:
            company_profile = await load_company_profile_fn(company_config_client_obj, company_id)
            company_knowledge = await load_company_knowledge_fn(
                company_config_client_obj, company_id
            )

        initial_state = build_session_state_fn(industry_config, industry)
        initial_state.update(
            build_company_session_state_fn(
                company_id=company_id,
                profile=company_profile,
                knowledge=company_knowledge,
            )
        )
        if registry_config is not None:
            initial_state.update(canonical_state_updates_from_registry_fn(registry_config))

        # Load global lessons (Tier 2 learning — cross-session behavioral rules)
        try:
            from app.tools.global_lessons import load_global_lessons

            db = company_config_client_obj or industry_config_client_obj
            if db is not None:
                global_lessons = load_global_lessons(
                    db, tenant_id=tenant_id, company_id=company_id,
                )
                if global_lessons:
                    initial_state["app:global_lessons"] = global_lessons
        except Exception as exc:
            logger.debug("Global lessons load skipped: %s", exc)

        create_kwargs: dict[str, object] = {
            "app_name": session_app_name,
            "user_id": user_id,
            "state": initial_state,
        }
        # Vertex sessions currently auto-generate server-side IDs.
        if not uses_vertex_sessions:
            create_kwargs["session_id"] = resolved_session_id
        created_session = await session_service_obj.create_session(
            **create_kwargs,
        )
        if (
            uses_vertex_sessions
            and isinstance(getattr(created_session, "id", None), str)
            and created_session.id
        ):
            resolved_session_id = created_session.id

    # Collect the final session state for voice + canonical fields.
    session_state: dict[str, object] = session.state if session else initial_state

    # Use locked session aliases when available.
    locked_industry = session_state.get("app:industry") if isinstance(session_state.get("app:industry"), str) else None
    session_industry = (locked_industry or industry).strip().lower() if isinstance((locked_industry or industry), str) else industry

    # Voice: prefer state override, fall back to industry map.
    voice_override = session_state.get("app:voice") if isinstance(session_state.get("app:voice"), str) else None
    session_voice = voice_override or voice_for_industry_fn(session_industry)

    if is_native_audio:
        run_config_kwargs: dict[str, object] = {
            "streaming_mode": streaming_mode_cls.BIDI,
            **native_audio_live_config_fn(industry, voice_override=voice_override),
        }
        if realtime_input_config is not None:
            run_config_kwargs["realtime_input_config"] = realtime_input_config
        run_config = run_config_cls(**run_config_kwargs)
    else:
        run_config = run_config_cls(
            streaming_mode=streaming_mode_cls.BIDI,
            response_modalities=["TEXT"],
            session_resumption=types_mod.SessionResumptionConfig(),
        )

    logger.debug(
        "Model: %s, native_audio=%s, industry=%s, company_id=%s, voice=%s",
        model_name,
        is_native_audio,
        sanitize_log_fn(industry),
        sanitize_log_fn(company_id),
        voice_for_industry_fn(industry),
    )

    manual_vad_active = manual_vad_active_setting and is_native_audio

    # Notify client with the canonical session ID.
    await websocket.send_text(json.dumps(build_session_started_message_fn(
        session_id=resolved_session_id,
        industry=session_industry,
        company_id=company_id,
        voice=session_voice,
        manual_vad_active=manual_vad_active,
        session_state=session_state,
    )))

    return SessionInitContext(
        websocket=websocket,
        user_id=user_id,
        resolved_session_id=resolved_session_id,
        client_ip=client_ip,
        model_name=model_name,
        is_native_audio=is_native_audio,
        industry=industry,
        session_industry=session_industry,
        company_id=company_id,
        tenant_id=tenant_id,
        requested_template_id=requested_template_id,
        session_state=session_state,
        session_voice=session_voice,
        manual_vad_active=manual_vad_active,
        run_config=run_config,
    )
