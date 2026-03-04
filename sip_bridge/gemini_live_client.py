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

    Placeholder implementation: until real Live session wiring is added, this
    client only transitions to connected when a session object is present.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._session = None
        self._connected = False

    async def connect(self) -> None:
        """Establish Gemini Live WebSocket session when available."""
        logger.info(
            "Connecting to Gemini Live",
            extra={
                "model": self.config.live_model_id,
                "voice": self.config.gemini_voice,
                "company_id": self.config.company_id,
            },
        )
        # Live API session wiring is intentionally deferred in this placeholder.
        self._connected = self._session is not None

    async def send_audio(self, pcm16_data: bytes) -> None:
        """Send PCM16 audio chunk to Gemini Live when connected."""
        if not self._connected or self._session is None:
            return
        # TODO: Forward PCM16 chunk to active Live session.

    async def receive_audio(self) -> bytes | None:
        """Receive PCM16 audio chunk from Gemini Live response when connected."""
        if not self._connected or self._session is None:
            return None
        # TODO: Read next audio chunk from active Live session.
        return None

    async def close(self) -> None:
        """Close the Gemini Live session."""
        if self._connected and self._session is not None:
            # TODO: Close the active Live session when implemented.
            self._session = None
        self._connected = False
        logger.info("Gemini Live session closed")
