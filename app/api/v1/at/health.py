"""AT channel health and readiness endpoints.

- /health: liveness check (always 200 if process is running)
- /readiness: feature flag status + SIP bridge reachability
"""

from __future__ import annotations

from fastapi import APIRouter

from .settings import AT_VOICE_ENABLED, AT_SMS_ENABLED, SIP_BRIDGE_ENDPOINT

router = APIRouter()


@router.get("/health")
async def at_health() -> dict:
    """Liveness probe for AT channel endpoints."""
    return {"status": "ok", "channel": "africastalking"}


@router.get("/readiness")
async def at_readiness() -> dict:
    """Readiness check: feature flags + bridge availability."""
    sip_configured = bool(SIP_BRIDGE_ENDPOINT)
    ready = AT_VOICE_ENABLED or AT_SMS_ENABLED

    return {
        "ready": ready,
        "voice_enabled": AT_VOICE_ENABLED,
        "sms_enabled": AT_SMS_ENABLED,
        "sip_bridge_configured": sip_configured,
        "sip_bridge_endpoint": SIP_BRIDGE_ENDPOINT if sip_configured else None,
    }
