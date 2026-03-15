from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.image_preview_tools import generate_case_preview_via_whatsapp


class _FakeInlineData:
    def __init__(self, data: bytes, mime_type: str = "image/png") -> None:
        self.data = data
        self.mime_type = mime_type


class _FakePart:
    def __init__(self, *, inline_data=None, text: str | None = None) -> None:
        self.inline_data = inline_data
        self.text = text


class _FakeContent:
    def __init__(self, parts) -> None:
        self.parts = parts


class _FakeCandidate:
    def __init__(self, parts) -> None:
        self.content = _FakeContent(parts)


class _FakeResponse:
    def __init__(self, parts) -> None:
        self.candidates = [_FakeCandidate(parts)]


def _tool_context() -> SimpleNamespace:
    return SimpleNamespace(
        state={
            "user:caller_phone": "+2348012345678",
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
        },
        function_call_id="fc-image-preview",
    )


@pytest.mark.asyncio
async def test_generate_case_preview_requires_feature_flag() -> None:
    with patch("app.tools.image_preview_tools.image_preview_enabled", return_value=False):
        result = await generate_case_preview_via_whatsapp(
            "iPhone 14",
            "white",
            _tool_context(),
        )
    assert result["status"] == "error"
    assert "not enabled" in result["detail"].lower()


@pytest.mark.asyncio
async def test_generate_case_preview_sends_generated_image() -> None:
    fake_client = MagicMock()
    fake_client.aio = SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(
        return_value=_FakeResponse([
            _FakePart(inline_data=_FakeInlineData(b"png-bytes", "image/png")),
        ])
    )))

    with (
        patch("app.tools.image_preview_tools.image_preview_enabled", return_value=True),
        patch("app.tools.image_preview_tools._get_image_client", return_value=fake_client),
        patch("app.tools.image_preview_tools._image_model_candidates", return_value=["gemini-3.1-flash-image-preview"]),
        patch("app.tools.image_preview_tools.send_whatsapp_image_message", new_callable=AsyncMock) as mock_send,
    ):
        mock_send.return_value = {"status": "sent", "message_id": "wamid.preview1"}
        result = await generate_case_preview_via_whatsapp(
            "iPhone 14",
            "white",
            _tool_context(),
            case_style="case",
        )

    assert result["status"] == "sent"
    assert result["message_id"] == "wamid.preview1"
    assert result["model"] == "gemini-3.1-flash-image-preview"
    call_kwargs = fake_client.aio.models.generate_content.await_args.kwargs
    assert call_kwargs["model"] == "gemini-3.1-flash-image-preview"
    assert isinstance(call_kwargs["contents"], str)
    assert "iPhone 14" in call_kwargs["contents"]
    assert "white" in call_kwargs["contents"]
    mock_send.assert_awaited_once()
    send_kwargs = mock_send.await_args.kwargs
    assert send_kwargs["media_bytes"] == b"png-bytes"
    assert send_kwargs["mime_type"] == "image/png"
    assert "white case" in send_kwargs["caption"].lower()


@pytest.mark.asyncio
async def test_generate_case_preview_handles_missing_image_output() -> None:
    fake_client = MagicMock()
    fake_client.aio = SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(
        return_value=_FakeResponse([
            _FakePart(text="I could not produce an image."),
        ])
    )))

    with (
        patch("app.tools.image_preview_tools.image_preview_enabled", return_value=True),
        patch("app.tools.image_preview_tools._get_image_client", return_value=fake_client),
        patch("app.tools.image_preview_tools.send_whatsapp_image_message", new_callable=AsyncMock) as mock_send,
    ):
        result = await generate_case_preview_via_whatsapp(
            "iPhone 14",
            "white",
            _tool_context(),
        )

    assert result["status"] == "error"
    assert "no image" in result["detail"].lower()
    mock_send.assert_not_awaited()
