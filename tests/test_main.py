"""Tests for the FastAPI application (main.py)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    """Import the FastAPI app."""
    from main import app
    return app


@pytest.fixture
def main_module():
    """Import main module for helper-level assertions."""
    import main
    return main


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client for testing FastAPI endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthEndpoint:
    """Test the health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        response = await client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_returns_json(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_includes_app_name(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "app" in data
        assert data["app"] == "ekaette"


class TestCORSHeaders:
    """Test CORS middleware is properly configured."""

    @pytest.mark.asyncio
    async def test_cors_allows_configured_origin(self, client):
        response = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    @pytest.mark.asyncio
    async def test_cors_blocks_unknown_origin(self, client):
        response = await client.options(
            "/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = response.headers.get("access-control-allow-origin")
        assert allow_origin != "http://evil.example.com"


class TestWebSocketEndpoint:
    """Test WebSocket endpoint is registered."""

    def test_ws_route_registered(self, app):
        """The /ws/{user_id}/{session_id} WebSocket route exists in the app."""
        ws_paths = [
            route.path for route in app.routes
            if hasattr(route, "path") and "/ws/" in route.path
        ]
        assert "/ws/{user_id}/{session_id}" in ws_paths


class TestSecurityHelpers:
    """Test allowlist parsing and origin validation helpers."""

    def test_parse_allowlist_strips_whitespace_and_empty(self, main_module):
        parsed = main_module._parse_allowlist(" http://a.com, ,http://b.com ,,")
        assert parsed == ["http://a.com", "http://b.com"]

    def test_is_origin_allowed_true_for_known_origin(self, main_module):
        assert main_module._is_origin_allowed("http://localhost:5173") is True

    def test_is_origin_allowed_false_for_unknown_origin(self, main_module):
        assert main_module._is_origin_allowed("http://evil.example.com") is False

    def test_is_origin_allowed_true_when_origin_missing(self, main_module):
        """None origin is allowed (same-origin / server-to-server requests omit Origin header)."""
        assert main_module._is_origin_allowed(None) is True
