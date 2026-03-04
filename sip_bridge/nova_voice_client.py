"""Amazon Nova Bedrock voice client for SIP/WA bridges.

This module defines the transport surface used by SIP bridge sessions.
The session loops own codec/SRTP behavior; this client owns provider I/O.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class NovaVoiceClient:
    """Bridge wrapper around NovaBedrockVoiceSession."""

    _AUDIO_QUEUE_MAXSIZE = 256

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        system_instruction: str = "",
        voice_name: str = "",
    ) -> None:
        self.model_id = model_id or os.getenv("NOVA_VOICE_MODEL_ID", "amazon.nova-2-sonic-v1:0")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.system_instruction = system_instruction
        self.voice_name = voice_name
        self._session: Any | None = None
        self._recv_task: asyncio.Task | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self._AUDIO_QUEUE_MAXSIZE)
        self._lifecycle_lock = asyncio.Lock()
        self._dropped_audio_count = 0

    async def connect(self) -> None:
        async with self._lifecycle_lock:
            if (
                self._session is not None
                and self._recv_task is not None
                and not self._recv_task.done()
            ):
                logger.debug("Nova voice client connect() called while already connected")
                return

            # Defensive cleanup for partially-closed states from prior failures.
            if self._session is not None or self._recv_task is not None:
                await self._close_locked()

            module = importlib.import_module("app.runtime.providers.nova_bedrock")
            session_cls = getattr(module, "NovaBedrockVoiceSession")
            self._session = session_cls(model_id=self.model_id, region=self.region)
            self._recv_task = asyncio.create_task(self._recv_loop(), name="nova_voice_recv_loop")
            logger.info(
                "Nova voice client connected",
                extra={"model_id": self.model_id, "region": self.region},
            )

    async def _recv_loop(self) -> None:
        if self._session is None:
            return
        async for event in self._session.events():
            if getattr(event, "event_type", "") == "audio":
                audio = getattr(event, "audio_data", b"")
                if isinstance(audio, bytes) and audio:
                    try:
                        self._audio_queue.put_nowait(audio)
                    except asyncio.QueueFull:
                        self._dropped_audio_count += 1
                        logger.warning(
                            "Nova voice audio dropped due to full queue",
                            extra={
                                "connection_id": f"{self.region}:{self.model_id}",
                                "dropped_audio_count": self._dropped_audio_count,
                                "queue_size": self._audio_queue.qsize(),
                            },
                        )
            elif getattr(event, "event_type", "") == "error":
                logger.warning(
                    "Nova voice event error",
                    extra={"code": getattr(event, "code", "VOICE_STREAM_ERROR")},
                )

    async def send_audio(self, pcm16_data: bytes) -> None:
        if self._session is None:
            return
        await self._session.send_audio(pcm16_data, mime_type="audio/pcm;rate=16000")

    async def send_text(self, text: str) -> None:
        if self._session is None:
            return
        await self._session.send_text(text)

    async def receive_audio(self, timeout_s: float = 0.05) -> bytes | None:
        try:
            return await asyncio.wait_for(self._audio_queue.get(), timeout=timeout_s)
        except TimeoutError:
            return None

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            await asyncio.gather(self._recv_task, return_exceptions=True)
            self._recv_task = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        try:
            while True:
                self._audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        self._audio_queue = asyncio.Queue(maxsize=self._AUDIO_QUEUE_MAXSIZE)
