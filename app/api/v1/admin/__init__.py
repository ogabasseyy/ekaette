"""Admin API router composition."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin.routes.companies import router as companies_router
from app.api.v1.admin.routes.connectors import router as connectors_router
from app.api.v1.admin.routes.data import router as data_router
from app.api.v1.admin.routes.knowledge import router as knowledge_router

admin_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
)
admin_router.include_router(companies_router)
admin_router.include_router(knowledge_router)
admin_router.include_router(connectors_router)
admin_router.include_router(data_router)
