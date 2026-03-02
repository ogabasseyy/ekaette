"""Gemini Live WebSocket client for SIP bridge.

Connects to Gemini Live API for real-time voice conversation.
Receives config via env vars — no app.* imports.
"""

from __future__ import annotations

import logging

from .config import BridgeConfig

logger = logging.getLogger(__name__)


class GeminiLiveClient:
    """Gemini Live bidi-streaming client for voice bridge.

    Placeholder — full implementation requires google-genai Live API
    WebSocket connection adapted from sip-to-ai.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._session = None
        self._connected = False

    async def connect(self) -> None:
        """Establish Gemini Live WebSocket session.

        TODO: Use ``client.aio.live.connect()`` with LiveConnectConfig
        for real-time audio modality.
        """
        logger.info(
            "Connecting to Gemini Live",
            extra={
                "model": self.config.live_model_id,
                "voice": self.config.gemini_voice,
                "company_id": self.config.company_id,
            },
        )
        self._connected = True

    async def send_audio(self, pcm16_data: bytes) -> None:
        """Send PCM16 audio chunk to Gemini Live.

        TODO: Use ``session.send(input=LiveClientRealtimeInput(...))``
        with ``audio/pcm`` Blob.
        """
        if not self._connected:
            return

    async def receive_audio(self) -> bytes | None:
        """Receive PCM16 audio chunk from Gemini Live response.

        TODO: Iterate ``session.receive()`` and extract ``inline_data``
        from model turn parts.
        """
        if not self._connected:
            return None
        return None

    async def close(self) -> None:
        """Close the Gemini Live session."""
        if self._session:
            pass  # TODO: await session.close()
        self._connected = False
        logger.info("Gemini Live session closed")
