"""Tests for WhatsApp provider URL validation helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.api.v1.at import providers


def test_allowed_download_url_requires_https() -> None:
    assert providers._is_allowed_download_url("https://lookaside.fbsbx.com/media.bin") is True
    assert providers._is_allowed_download_url("http://lookaside.fbsbx.com/media.bin") is False


def test_allowed_download_url_rejects_unknown_host() -> None:
    assert providers._is_allowed_download_url("https://example.com/media.bin") is False


def test_allowed_download_url_rejects_missing_host() -> None:
    assert providers._is_allowed_download_url("https:///media.bin") is False


class _MockResponse:
    def __init__(
        self,
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
        json_payload=None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._json_payload = json_payload if json_payload is not None else {}
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_payload


class _AsyncClientStub:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        event = self._events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event

    async def get(self, *args, **kwargs):
        event = self._events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, events: list[object]) -> None:
    stub = _AsyncClientStub(events)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda *args, **kwargs: stub)


async def test_wa_graph_request_429_retry_after_caps_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS", 5)

    sleep_mock = AsyncMock()
    jitter_mock = MagicMock(return_value=1.0)
    monkeypatch.setattr(providers.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(providers, "_jitter_backoff", jitter_mock)
    _patch_async_client(monkeypatch, [
        _MockResponse(status_code=429, headers={"Retry-After": "99"}),
        _MockResponse(status_code=200, json_payload={"ok": True}),
    ])

    status, body = await providers._wa_graph_request(
        "POST",
        path_segments=("v25.0", "123", "messages"),
        headers={},
        json={},
    )

    assert status == 200
    assert body == {"ok": True}
    sleep_mock.assert_awaited_once_with(5.0)
    jitter_mock.assert_not_called()


async def test_wa_graph_request_429_invalid_retry_after_falls_back_to_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS", 8)

    sleep_mock = AsyncMock()
    monkeypatch.setattr(providers.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(providers, "_jitter_backoff", lambda attempt: 1.25)
    _patch_async_client(monkeypatch, [
        _MockResponse(status_code=429, headers={"Retry-After": "not-a-number"}),
        _MockResponse(status_code=200, json_payload={"ok": True}),
    ])

    status, body = await providers._wa_graph_request(
        "POST",
        path_segments=("v25.0", "123", "messages"),
        headers={},
        json={},
    )

    assert status == 200
    assert body == {"ok": True}
    sleep_mock.assert_awaited_once_with(1.25)


async def test_wa_graph_request_429_without_retry_after_falls_back_to_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_ATTEMPTS", 3)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(providers.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(providers, "_jitter_backoff", lambda attempt: 0.75)
    _patch_async_client(monkeypatch, [
        _MockResponse(status_code=429, headers={}),
        _MockResponse(status_code=200, json_payload={"ok": True}),
    ])

    status, body = await providers._wa_graph_request(
        "POST",
        path_segments=("v25.0", "123", "messages"),
        headers={},
        json={},
    )

    assert status == 200
    assert body == {"ok": True}
    sleep_mock.assert_awaited_once_with(0.75)


async def test_wa_graph_request_retries_5xx_until_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_ATTEMPTS", 3)

    sleep_mock = AsyncMock()
    monkeypatch.setattr(providers.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(providers, "_jitter_backoff", lambda attempt: 0.1 * (attempt + 1))
    _patch_async_client(monkeypatch, [
        _MockResponse(status_code=500, json_payload={"error": "e1"}),
        _MockResponse(status_code=502, json_payload={"error": "e2"}),
        _MockResponse(status_code=503, json_payload={"error": "e3"}),
    ])

    status, body = await providers._wa_graph_request(
        "POST",
        path_segments=("v25.0", "123", "messages"),
        headers={},
        json={},
    )

    assert status == 503
    assert body == {"error": "e3"}
    assert sleep_mock.await_count == 2


@pytest.mark.parametrize(
    "exc",
    [
        httpx.TimeoutException("timeout"),
        httpx.ConnectError("connect", request=httpx.Request("POST", "https://example.com")),
    ],
)
async def test_wa_graph_request_retries_network_errors_then_raises(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_ATTEMPTS", 3)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(providers.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(providers, "_jitter_backoff", lambda attempt: 0.2)
    _patch_async_client(monkeypatch, [exc, exc, exc])

    with pytest.raises(type(exc)):
        await providers._wa_graph_request(
            "POST",
            path_segments=("v25.0", "123", "messages"),
            headers={},
            json={},
        )

    assert sleep_mock.await_count == 2


async def test_wa_graph_request_non_dict_json_is_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_ATTEMPTS", 1)
    _patch_async_client(monkeypatch, [_MockResponse(status_code=200, json_payload=["x"])])

    status, body = await providers._wa_graph_request(
        "GET",
        path_segments=("v25.0", "123", "messages"),
        headers={},
    )
    assert status == 200
    assert body == {}


async def test_wa_graph_request_json_exception_returns_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(providers, "WA_GRAPH_RETRY_MAX_ATTEMPTS", 1)
    _patch_async_client(monkeypatch, [
        _MockResponse(status_code=200, json_error=ValueError("bad json")),
    ])

    status, body = await providers._wa_graph_request(
        "GET",
        path_segments=("v25.0", "123", "messages"),
        headers={},
    )
    assert status == 200
    assert body == {}
