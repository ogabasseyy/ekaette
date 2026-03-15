from __future__ import annotations

from google.auth.transport.requests import Request as GoogleAuthRequest

from shared.firestore_client import create_firestore_client


def test_create_firestore_client_uses_emulator_without_adc(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ekaette")

    captured: dict[str, object] = {}

    class _FakeFirestoreClient:
        def __init__(self, *, project=None, credentials=None):
            captured["project"] = project
            captured["credentials"] = credentials

    monkeypatch.setattr("google.cloud.firestore.Client", _FakeFirestoreClient)

    create_firestore_client(project="ekaette")

    assert captured == {"project": "ekaette", "credentials": None}


def test_create_firestore_client_uses_explicit_google_auth_request(monkeypatch):
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)

    captured: dict[str, object] = {}
    credentials = object()

    def _fake_default(*, scopes, request, **kwargs):
        captured["scopes"] = scopes
        captured["request"] = request
        return credentials, "detected-project"

    class _FakeFirestoreClient:
        def __init__(self, *, project=None, credentials=None):
            captured["project"] = project
            captured["credentials"] = credentials

    monkeypatch.setattr("google.auth.default", _fake_default)
    monkeypatch.setattr("google.cloud.firestore.Client", _FakeFirestoreClient)

    create_firestore_client(project=None)

    assert captured["scopes"] == ("https://www.googleapis.com/auth/cloud-platform",)
    assert captured["project"] == "detected-project"
    assert captured["credentials"] is credentials
    assert isinstance(captured["request"], GoogleAuthRequest)
