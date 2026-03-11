"""Tests for the dedicated realtime websocket Cloud Run entrypoint."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def live_app():
    from main_live import app

    return app


def test_live_health_returns_200(live_app):
    with TestClient(live_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "ekaette-live", "mode": "realtime"}


def test_live_ws_route_registered(live_app):
    ws_paths = [
        route.path for route in live_app.routes
        if hasattr(route, "path") and "/ws/" in route.path
    ]
    assert "/ws/{user_id}/{session_id}" in ws_paths


def test_live_ws_uses_main_runtime(monkeypatch, live_app):
    import main as main_module
    from app.api.v1.public import ws_auth

    class _FakeSessionService:
        async def get_session(self, *, app_name, user_id, session_id):
            return SimpleNamespace(
                id=session_id,
                state={
                    "app:industry": "electronics",
                    "app:industry_config": {"name": "Electronics & Gadgets", "voice": "Aoede"},
                    "app:company_id": "ekaette-electronics",
                    "app:company_profile": {},
                    "app:company_knowledge": [],
                },
            )

    async def _fake_run_live(**kwargs):
        yield SimpleNamespace(
            content=None,
            input_transcription=None,
            output_transcription=None,
            interrupted=False,
            actions=None,
            turn_complete=False,
            usage_metadata=None,
            live_session_resumption_update=None,
            author="ekaette_router",
        )

    monkeypatch.setattr(main_module, "session_service", _FakeSessionService())
    monkeypatch.setattr(main_module, "runner", SimpleNamespace(run_live=_fake_run_live))
    token = ""
    if getattr(main_module, "WS_TOKEN_SECRET", ""):
        monkeypatch.setattr(ws_auth, "_WS_TOKEN_SECRET", main_module.WS_TOKEN_SECRET)
        token = ws_auth.create_ws_token(
            "user_123",
            "public",
            "ekaette-electronics",
            ttl_seconds=60,
        )
    query = "/ws/user_123/session_abc?industry=electronics&companyId=ekaette-electronics"
    if token:
        query = f"{query}&token={token}"

    with TestClient(live_app) as tc:
        with tc.websocket_connect(
            query,
            headers={"origin": "http://localhost:5173"},
        ) as ws:
            payload = json.loads(ws.receive_text())
            assert payload["type"] == "session_started"
            assert payload["companyId"] == "ekaette-electronics"
            ws.close(code=1000)
