"""AT campaign analytics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import campaign_analytics
from .models import CampaignAnalyticsEventRequest

router = APIRouter()


@router.get("/analytics/overview")
async def analytics_overview(
    tenant_id: str = Query(default="public", alias="tenantId"),
    company_id: str = Query(default="ekaette-electronics", alias="companyId"),
    days: int = Query(default=30, ge=1, le=365),
    campaigns_limit: int = Query(default=20, alias="campaignsLimit", ge=1, le=200),
) -> dict:
    """Campaign-level KPI overview for SMS/voice/payment outcomes."""
    summary = campaign_analytics.overview_snapshot(
        tenant_id=tenant_id,
        company_id=company_id,
        days=days,
    )
    campaigns = campaign_analytics.list_campaign_snapshots(
        tenant_id=tenant_id,
        company_id=company_id,
        limit=campaigns_limit,
    )
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "company_id": company_id,
        "summary": summary,
        "campaigns": campaigns,
    }


@router.get("/analytics/campaigns")
async def analytics_campaigns(
    tenant_id: str = Query(default="public", alias="tenantId"),
    company_id: str = Query(default="ekaette-electronics", alias="companyId"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """List campaign snapshots for one tenant/company."""
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "company_id": company_id,
        "campaigns": campaign_analytics.list_campaign_snapshots(
            tenant_id=tenant_id,
            company_id=company_id,
            limit=limit,
        ),
    }


@router.get("/analytics/campaigns/{campaign_id}")
async def analytics_campaign(campaign_id: str) -> dict:
    """Get one campaign snapshot."""
    snapshot = campaign_analytics.campaign_snapshot(campaign_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {
        "status": "ok",
        "campaign": snapshot,
    }


@router.get("/analytics/contacts")
async def analytics_contacts(
    tenant_id: str = Query(default="public", alias="tenantId"),
    company_id: str = Query(default="ekaette-electronics", alias="companyId"),
) -> dict:
    """List known recipient contacts from campaign history."""
    contacts = campaign_analytics.list_known_contacts(
        tenant_id=tenant_id,
        company_id=company_id,
    )
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "company_id": company_id,
        "contacts": contacts,
        "count": len(contacts),
    }


@router.post("/analytics/events")
async def analytics_event(req: CampaignAnalyticsEventRequest) -> dict:
    """Ingest a manual campaign event (conversion, reply, delivery, etc.)."""
    resolved_campaign_id = campaign_analytics.record_event(
        event_type=req.event_type,
        channel=req.channel,
        tenant_id=req.tenant_id,
        company_id=req.company_id,
        campaign_id=req.campaign_id,
        campaign_name=req.campaign_name,
        recipient=req.recipient,
        amount_kobo=req.amount_kobo,
        reference=req.reference,
        event_id=req.event_id,
        metadata=req.metadata,
    )
    return {
        "status": "ok",
        "campaign_id": resolved_campaign_id,
        "campaign": campaign_analytics.campaign_snapshot(resolved_campaign_id),
    }
