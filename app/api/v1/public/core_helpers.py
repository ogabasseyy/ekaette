"""Pure/shared helpers for public HTTP and realtime boundaries.

Designed to be imported from main.py wrappers to preserve monkeypatch behavior.
"""

from __future__ import annotations

import re
import time

from fastapi import Request
from fastapi.responses import JSONResponse


def parse_allowlist(raw_origins: str) -> list[str]:
    """Parse comma-delimited origins into a clean list."""
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


def is_origin_allowed(origin: str | None, allowed_origin_set: set[str]) -> bool:
    if origin is None:
        return True
    return origin in allowed_origin_set


def is_websocket_origin_allowed(
    origin: str | None,
    allowed_origin_set: set[str],
    *,
    allow_missing_ws_origin: bool,
) -> bool:
    if origin is None:
        return bool(allow_missing_ws_origin)
    return origin in allowed_origin_set


def sanitize_log(value: str | None, unsafe_pattern: re.Pattern[str]) -> str:
    if value is None:
        return "<none>"
    return unsafe_pattern.sub("", value)[:200]


def usage_int(usage: object, *names: str) -> int:
    """Extract positive integer token counts from usage metadata shapes."""
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def voice_for_industry(industry: str) -> str:
    voice_map = {
        "electronics": "Kore",
        "hotel": "Puck",
        "automotive": "Charon",
        "fashion": "Aoede",
    }
    key = (industry or "").strip().lower()
    return voice_map.get(key, "Aoede")


def native_audio_live_config(
    *,
    industry: str,
    voice_override: str | None,
    speech_language_code: str | None,
    types_module: object,
    voice_for_industry_fn,
) -> dict[str, object]:
    """Shared native-audio config used by token and websocket paths."""
    voice = voice_override if voice_override else voice_for_industry_fn(industry)
    return {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": types_module.AudioTranscriptionConfig(),
        "output_audio_transcription": types_module.AudioTranscriptionConfig(),
        "session_resumption": types_module.SessionResumptionConfig(),
        "context_window_compression": types_module.ContextWindowCompressionConfig(
            trigger_tokens=80000,
            sliding_window=types_module.SlidingWindow(target_tokens=40000),
        ),
        "enable_affective_dialog": True,
        "proactivity": types_module.ProactivityConfig(proactive_audio=True),
        "speech_config": types_module.SpeechConfig(
            language_code=speech_language_code or None,
            voice_config=types_module.VoiceConfig(
                prebuilt_voice_config=types_module.PrebuiltVoiceConfig(
                    voice_name=voice,
                )
            ),
        ),
    }


def legacy_industry_alias_from_registry_config(
    config: object | None,
    *,
    fallback: str,
) -> str:
    """Derive a backward-compatible legacy industry alias from registry config."""
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
        if normalized_category in {"telecom", "aviation"}:
            return normalized_category

    return fallback


def canonical_state_updates_from_registry(config: object) -> dict[str, object]:
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
        "app:enabled_agents",
        "app:ui_theme",
        "app:connector_manifest",
        "app:registry_version",
    )

    from app.configs.registry_loader import build_session_state_from_registry

    registry_state = build_session_state_from_registry(config)
    updates = {k: registry_state[k] for k in keys if k in registry_state}
    fallback_industry = (
        str(getattr(config, "industry_template_id", "") or "").strip().lower() or "electronics"
    )
    updates["app:industry"] = legacy_industry_alias_from_registry_config(
        config,
        fallback=fallback_industry,
    )
    return updates


def build_session_started_message(
    *,
    session_id: str,
    industry: str,
    company_id: str,
    voice: str,
    manual_vad_active: bool,
    session_state: dict[str, object] | None,
) -> dict[str, object]:
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


def append_canonical_lock_fields(
    payload: dict[str, object],
    session_state: dict[str, object] | None,
) -> dict[str, object]:
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


def registry_mismatch_response(
    *,
    requested_template_id: str | None,
    resolved_template_id: str | None,
    require_company_template_match: bool,
) -> JSONResponse | None:
    if not require_company_template_match:
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


def check_rate_limit(
    *,
    client_ip: str,
    bucket: str,
    limit: int,
    window_seconds: int,
    max_buckets: int,
    buckets: dict[str, list[float]],
    last_global_prune: float,
) -> tuple[bool, float]:
    """Return (allowed, updated_last_global_prune) for in-memory rate limiting."""
    now = time.time()
    key = f"{bucket}:{client_ip}"
    updated_last = last_global_prune

    if now - updated_last >= window_seconds:
        stale_keys = [
            existing_key
            for existing_key, values in buckets.items()
            if not values or (now - values[-1]) >= window_seconds
        ]
        for stale_key in stale_keys:
            buckets.pop(stale_key, None)
        updated_last = now

    if key not in buckets and len(buckets) >= max_buckets:
        oldest_key = min(
            buckets.keys(),
            key=lambda existing_key: buckets[existing_key][-1] if buckets[existing_key] else 0.0,
            default=None,
        )
        if oldest_key is not None:
            buckets.pop(oldest_key, None)

    timestamps = buckets.get(key, [])
    timestamps = [t for t in timestamps if now - t < window_seconds]
    if len(timestamps) >= limit:
        buckets[key] = timestamps
        return False, updated_last
    timestamps.append(now)
    buckets[key] = timestamps
    return True, updated_last


def client_ip_from_request(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if isinstance(forwarded_for, str) and forwarded_for.strip():
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    return request.client.host if request.client else "unknown"
