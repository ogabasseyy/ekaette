"""Gemini Live WebSocket client for SIP bridge.

Connects to Gemini Live API for real-time voice conversation.
Receives config via env vars — no app.* imports.
"""

from __future__ import annotations

import asyncio
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
        """Establish Gemini Live WebSocket session."""
        logger.info(
            "Connecting to Gemini Live",
            extra={
                "model": self.config.live_model_id,
                "voice": self.config.gemini_voice,
                "company_id": self.config.company_id,
            },
        )
        # TODO: Establish connection using google.genai Live API
        # session = client.aio.live.connect(
        #     model=config.live_model_id,
        #     config=types.LiveConnectConfig(
        #         response_modalities=["AUDIO"],
        #         speech_config=types.SpeechConfig(
        #             voice_config=types.VoiceConfig(
        #                 prebuilt_voice_config=types.PrebuiltVoiceConfig(
        #                     voice_name=config.gemini_voice,
        #                 )
        #             )
        #         ),
        #         system_instruction=config.system_instruction,
        #     ),
        # )
        self._connected = True

    async def send_audio(self, pcm16_data: bytes) -> None:
        """Send PCM16 audio chunk to Gemini Live."""
        if not self._connected:
            return
        # TODO: session.send(input=types.LiveClientRealtimeInput(
        #     media_chunks=[types.Blob(data=pcm16_data, mime_type="audio/pcm")]
        # ))

    async def receive_audio(self) -> bytes | None:
        """Receive PCM16 audio chunk from Gemini Live response."""
        if not self._connected:
            return None
        # TODO: async for response in session.receive():
        #     for part in response.server_content.model_turn.parts:
        #         if part.inline_data:
        #             return part.inline_data.data
        return None

    async def close(self) -> None:
        """Close the Gemini Live session."""
        if self._session:
            # TODO: await session.close()
            pass
        self._connected = False
        logger.info("Gemini Live session closed")
