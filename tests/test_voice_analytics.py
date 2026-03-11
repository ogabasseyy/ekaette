"""Tests for voice operations analytics endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


def _build_analytics_app() -> FastAPI:
    from app.api.v1.at.analytics_routes import router as analytics_router

    app = FastAPI()
    app.include_router(analytics_router, prefix="/api/v1/at")
    return app


@pytest.fixture()
def voice_analytics_client():
    from app.api.v1.at import campaign_analytics, voice_analytics

    campaign_analytics.reset_state()
    voice_analytics.reset_state()
    app = _build_analytics_app()
    yield TestClient(app)
    campaign_analytics.reset_state()
    voice_analytics.reset_state()


class TestVoiceAnalytics:
    def test_voice_overview_returns_call_metrics_and_recent_calls(
        self,
        voice_analytics_client: TestClient,
    ) -> None:
        from app.api.v1.at import voice_analytics

        voice_analytics.start_session(
            session_id="sess-voice-001",
            tenant_id="public",
            company_id="ekaette-electronics",
            channel="voice",
            started_at=1_700_000_000.0,
            caller_phone="+2348011111111",
        )
        voice_analytics.record_transcript(
            session_id="sess-voice-001",
            role="user",
            text="I want to buy an iPhone 14.",
            partial=False,
        )
        voice_analytics.record_transcript(
            session_id="sess-voice-001",
            role="agent",
            text="Certainly, let me check what is available.",
            partial=False,
        )
        voice_analytics.record_transfer(
            session_id="sess-voice-001",
            target_agent="catalog_agent",
        )
        voice_analytics.mark_callback_requested(
            session_id="sess-voice-001",
            phone="+2348011111111",
        )
        voice_analytics.mark_callback_triggered(
            tenant_id="public",
            company_id="ekaette-electronics",
            phone="+2348011111111",
        )
        voice_analytics.end_session(
            session_id="sess-voice-001",
            ended_at=1_700_000_045.0,
        )

        voice_analytics.start_session(
            session_id="sess-voice-002",
            tenant_id="public",
            company_id="ekaette-electronics",
            channel="web_voice",
            started_at=1_700_000_100.0,
        )
        voice_analytics.end_session(
            session_id="sess-voice-002",
            ended_at=1_700_000_130.0,
        )

        overview = voice_analytics_client.get(
            "/api/v1/at/analytics/voice/overview",
            params={"tenantId": "public", "companyId": "ekaette-electronics", "days": 30},
        )

        assert overview.status_code == 200
        payload = overview.json()
        summary = payload["summary"]
        recent_calls = payload["recent_calls"]

        assert summary["calls_total"] == 2
        assert summary["calls_completed"] == 2
        assert summary["avg_duration_seconds"] == 37.5
        assert summary["transfers_total"] == 1
        assert summary["transfer_rate"] == 0.5
        assert summary["callback_requests_total"] == 1
        assert summary["callback_triggered_total"] == 1
        assert summary["transcript_coverage_rate"] == 0.5

        assert len(recent_calls) == 2
        assert recent_calls[0]["session_id"] == "sess-voice-002"
        assert recent_calls[1]["session_id"] == "sess-voice-001"
        assert recent_calls[1]["transcript_preview"].startswith("Customer: I want to buy")
        assert recent_calls[1]["agent_path"] == ["ekaette_router", "catalog_agent"]

    def test_voice_overview_is_scoped_by_company(
        self,
        voice_analytics_client: TestClient,
    ) -> None:
        from app.api.v1.at import voice_analytics

        voice_analytics.start_session(
            session_id="sess-a",
            tenant_id="public",
            company_id="ekaette-electronics",
            channel="voice",
            started_at=1_700_000_000.0,
        )
        voice_analytics.end_session(session_id="sess-a", ended_at=1_700_000_010.0)

        voice_analytics.start_session(
            session_id="sess-b",
            tenant_id="public",
            company_id="another-company",
            channel="voice",
            started_at=1_700_000_000.0,
        )
        voice_analytics.end_session(session_id="sess-b", ended_at=1_700_000_010.0)

        overview = voice_analytics_client.get(
            "/api/v1/at/analytics/voice/overview",
            params={"tenantId": "public", "companyId": "ekaette-electronics"},
        )
        assert overview.status_code == 200
        summary = overview.json()["summary"]
        assert summary["calls_total"] == 1
