"""Internal API router composition."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.internal.routes.jobs import router as jobs_router

internal_router = APIRouter(tags=["internal"])
internal_router.include_router(jobs_router)
