"""Explicit GenAI client construction for Vertex and API-key backends."""

from __future__ import annotations

import os

from google import genai
from google.genai import types


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def use_vertex_ai_backend() -> bool:
    """Return True when runtime should talk to Vertex AI explicitly."""
    return _env_flag("GOOGLE_GENAI_USE_VERTEXAI", "false")


def can_build_genai_client(*, prefer_vertex: bool | None = None, api_key: str | None = None) -> bool:
    """Return True when the current env can construct a usable GenAI client."""
    # Explicit api_key means we can always build a Gemini Developer client.
    if api_key and api_key.strip():
        return True
    use_vertex = use_vertex_ai_backend() if prefer_vertex is None else prefer_vertex
    if use_vertex:
        return True
    resolved_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    return bool(resolved_api_key)


def build_genai_client(
    *,
    api_version: str | None = None,
    prefer_vertex: bool | None = None,
    api_key: str | None = None,
) -> genai.Client:
    """Build an explicit backend-aware GenAI client.

    When Vertex is enabled, do not rely on implicit environment precedence.
    When Vertex is disabled, require an API key.
    """
    if api_key is not None and not api_key.strip():
        raise ValueError("api_key must be non-empty if provided")
    use_vertex = use_vertex_ai_backend() if prefer_vertex is None else prefer_vertex
    http_options = types.HttpOptions(api_version=api_version) if api_version else None
    # Explicit api_key overrides Vertex preference — caller wants Gemini Developer API
    # (e.g. TOKEN_CLIENT for auth_tokens.create which is Gemini-API-only).
    if api_key and api_key.strip():
        return genai.Client(
            api_key=api_key.strip(),
            vertexai=False,
            http_options=http_options,
        )
    if use_vertex:
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip() or None
        return genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=http_options,
        )

    resolved_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not resolved_api_key:
        raise ValueError("GOOGLE_API_KEY is required when Vertex AI backend is disabled")
    return genai.Client(
        api_key=resolved_api_key,
        http_options=http_options,
    )
