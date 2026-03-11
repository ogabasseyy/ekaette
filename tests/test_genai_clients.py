from __future__ import annotations

from app.genai_clients import build_genai_client, can_build_genai_client, use_vertex_ai_backend


def test_use_vertex_ai_backend_reads_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    assert use_vertex_ai_backend() is True


def test_can_build_genai_client_without_api_key_when_vertex_enabled(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert can_build_genai_client() is True


def test_can_build_genai_client_requires_api_key_when_vertex_disabled(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert can_build_genai_client() is False


def test_build_genai_client_uses_vertex_explicitly(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ekaette")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GOOGLE_API_KEY", "should-not-be-used")

    client = build_genai_client(api_version="v1alpha")

    assert client._api_client.vertexai is True


def test_build_genai_client_uses_api_key_when_vertex_disabled(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    client = build_genai_client(api_version="v1alpha")

    assert client._api_client.vertexai is None
