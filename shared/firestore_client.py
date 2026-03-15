"""Shared Firestore client helpers with explicit ADC transport selection."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.cloud import firestore

_FIRESTORE_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_GOOGLE_AUTH_REQUEST = None
_GOOGLE_AUTH_REQUEST_LOCK = threading.Lock()


def _get_google_auth_request():
    global _GOOGLE_AUTH_REQUEST
    with _GOOGLE_AUTH_REQUEST_LOCK:
        if _GOOGLE_AUTH_REQUEST is None:
            from google.auth.transport.requests import Request as GoogleAuthRequest

            _GOOGLE_AUTH_REQUEST = GoogleAuthRequest()
    return _GOOGLE_AUTH_REQUEST


def create_firestore_client(*, project: str | None = None) -> "firestore.Client":
    from google.cloud import firestore

    resolved_project = (project or "").strip() or None
    if os.getenv("FIRESTORE_EMULATOR_HOST", "").strip():
        return firestore.Client(project=resolved_project)

    import google.auth

    credentials, detected_project = google.auth.default(
        scopes=_FIRESTORE_SCOPES,
        request=_get_google_auth_request(),
    )
    return firestore.Client(
        project=resolved_project or detected_project,
        credentials=credentials,
    )
