"""Amazon Nova provider clients backed by Bedrock Runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any, AsyncIterator

import boto3

from .interfaces import (
    ReasoningClient,
    VisionClient,
    VoiceErrorEvent,
    VoiceEvent,
    VoiceSession,
)

logger = logging.getLogger(__name__)


def _build_bedrock_runtime_client(*, region: str | None = None):
    return boto3.client(
        "bedrock-runtime",
        region_name=region or os.getenv("AWS_REGION", "us-east-1"),
    )


def _image_format_from_mime(mime_type: str) -> str:
    mapping = {
        "image/jpeg": "jpeg",
        "image/jpg": "jpeg",
        "image/png": "png",
        "image/webp": "webp",
        "image/heic": "heic",
        "image/heif": "heif",
    }
    return mapping.get((mime_type or "").lower(), "jpeg")


class NovaBedrockReasoningClient(ReasoningClient):
    """Reasoning/text client via Bedrock Converse API."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        runtime_client=None,
    ) -> None:
        self.model_id = model_id or os.getenv("NOVA_REASONING_MODEL_ID", "amazon.nova-2-lite-v1:0")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.runtime_client = runtime_client or _build_bedrock_runtime_client(region=self.region)

    async def generate_text(
        self,
        *,
        user_message: str,
        system_instruction: str | None = None,
        max_tokens: int = 256,
        temperature: float = 0.2,
    ) -> str:
        loop = asyncio.get_running_loop()

        def _invoke() -> str:
            kwargs: dict[str, Any] = {
                "modelId": self.model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": user_message}],
                    }
                ],
                "inferenceConfig": {
                    "maxTokens": int(max_tokens),
                    "temperature": float(temperature),
                },
            }
            if system_instruction:
                kwargs["system"] = [{"text": system_instruction}]
            response = self.runtime_client.converse(**kwargs)
            output = response.get("output", {})
            message = output.get("message", {})
            content = message.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text = part["text"].strip()
                        if text:
                            return text
            return ""

        return await loop.run_in_executor(None, _invoke)


class NovaBedrockVisionClient(VisionClient):
    """Multimodal vision client via Bedrock Converse API."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        runtime_client=None,
    ) -> None:
        self.model_id = model_id or os.getenv("NOVA_VISION_MODEL_ID", "amazon.nova-pro-v1:0")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.runtime_client = runtime_client or _build_bedrock_runtime_client(region=self.region)

    async def analyze_image(
        self,
        *,
        image_data: bytes,
        mime_type: str,
        prompt: str,
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        image_format = _image_format_from_mime(mime_type)

        def _invoke() -> dict[str, Any]:
            response = self.runtime_client.converse(
                modelId=self.model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "image": {
                                    "format": image_format,
                                    "source": {"bytes": image_data},
                                }
                            },
                            {"text": prompt},
                        ],
                    }
                ],
                inferenceConfig={"maxTokens": int(max_tokens), "temperature": 0.2},
            )
            output = response.get("output", {})
            message = output.get("message", {})
            content = message.get("content", [])
            text = ""
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text = part["text"].strip()
                        if text:
                            break
            if not text:
                return {}
            # Providers often return a JSON blob as text. Parse when possible.
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw_analysis": text}

        return await loop.run_in_executor(None, _invoke)


class NovaBedrockVoiceSession(VoiceSession):
    """Best-effort placeholder for Bedrock bidirectional voice sessions.

    The bidirectional stream API is supported by Bedrock Runtime, but local
    SDK/runtime support can differ by environment. This class provides a
    normalized session surface and emits explicit runtime errors until a full
    streaming transport is enabled.
    """

    def __init__(self, *, model_id: str, region: str | None = None) -> None:
        self.model_id = model_id
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._queue: asyncio.Queue[VoiceEvent] = asyncio.Queue()
        self._closed = False
        self._warning_emitted = False

    async def _emit_unavailable_once(self) -> None:
        if self._warning_emitted:
            return
        self._warning_emitted = True
        await self._queue.put(
            VoiceErrorEvent(
                code="VOICE_STREAM_UNAVAILABLE",
                message=(
                    "Bedrock bidirectional stream transport is not wired in this runtime build. "
                    "Use backend websocket proxy path with provider adapters enabled."
                ),
            )
        )

    async def send_audio(self, audio_data: bytes, mime_type: str = "audio/pcm;rate=16000") -> None:
        if self._closed:
            return
        _ = audio_data, mime_type
        await self._emit_unavailable_once()

    async def send_text(self, text: str) -> None:
        if self._closed:
            return
        _ = text
        await self._emit_unavailable_once()

    async def send_image(self, image_data: bytes, mime_type: str) -> None:
        if self._closed:
            return
        _ = image_data, mime_type
        await self._emit_unavailable_once()

    async def send_activity_start(self) -> None:
        if self._closed:
            return
        await self._emit_unavailable_once()

    async def send_activity_end(self) -> None:
        if self._closed:
            return
        await self._emit_unavailable_once()

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while not self._closed:
            event = await self._queue.get()
            yield event

    async def close(self) -> None:
        self._closed = True


def base64_audio_to_bytes(data: str) -> bytes:
    """Decode base64 payload safely."""
    try:
        return base64.b64decode(data, validate=True)
    except Exception:
        logger.warning("Failed to decode provider audio payload")
        return b""

