"""Africa's Talking channel router composition.

Assembles voice and SMS sub-routers under /api/v1/at prefix.
Initializes AT SDK on import.
Includes health + readiness endpoints (V2 observability).
"""

from __future__ import annotations

from fastapi import APIRouter

from .voice import router as voice_router
from .sms import router as sms_router
from .health import router as health_router
from .deps import init_at_sdk

# Initialize AT SDK once at import time
init_at_sdk()

at_router = APIRouter(prefix="/api/v1/at", tags=["africastalking"])
at_router.include_router(voice_router)
at_router.include_router(sms_router)
at_router.include_router(health_router)
