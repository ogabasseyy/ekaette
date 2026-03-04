"""Provider interfaces and normalized event types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol


@dataclass(slots=True, frozen=True)
class ProviderCapabilities:
    """Resolved provider runtime capabilities."""

    provider: str
    region: str
    voice_model_id: str | None
    reasoning_model_id: str | None
    vision_model_id: str | None
    voice_model_fallbacks: tuple[str, ...]
    reasoning_model_fallbacks: tuple[str, ...]
    supports_bidirectional_voice: bool
    supports_reasoning: bool
    supports_vision: bool
    probe_warnings: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class VoiceAudioEvent:
    """Model audio chunk event."""

    event_type: str = "audio"
    audio_data: bytes = b""
    mime_type: str = "audio/pcm;rate=24000"


@dataclass(slots=True, frozen=True)
class VoiceTranscriptionEvent:
    """Model/user transcription event."""

    event_type: str = "transcription"
    role: str = "agent"
    text: str = ""
    partial: bool = False


@dataclass(slots=True, frozen=True)
class VoiceTurnCompleteEvent:
    """Turn completion marker."""

    event_type: str = "turn_complete"


@dataclass(slots=True, frozen=True)
class VoiceSessionEndingEvent:
    """Session ending marker."""

    event_type: str = "session_ending"
    reason: str = "provider_session_ending"
    time_left_ms: int | None = None


@dataclass(slots=True, frozen=True)
class VoiceErrorEvent:
    """Voice stream error event."""

    event_type: str = "error"
    code: str = "VOICE_STREAM_ERROR"
    message: str = ""


VoiceEvent = (
    VoiceAudioEvent
    | VoiceTranscriptionEvent
    | VoiceTurnCompleteEvent
    | VoiceSessionEndingEvent
    | VoiceErrorEvent
)


class VoiceSession(Protocol):
    """Bidirectional voice session transport contract."""

    async def send_audio(self, audio_data: bytes, mime_type: str = "audio/pcm;rate=16000") -> None:
        """Send client audio chunk to provider."""

    async def send_text(self, text: str) -> None:
        """Send text content/event into the provider session."""

    async def send_image(self, image_data: bytes, mime_type: str) -> None:
        """Send image payload into the provider session."""

    async def send_activity_start(self) -> None:
        """Signal explicit user speech start when manual VAD is active."""

    async def send_activity_end(self) -> None:
        """Signal explicit user speech end when manual VAD is active."""

    async def events(self) -> AsyncIterator[VoiceEvent]:
        """Yield normalized provider events."""

    async def close(self) -> None:
        """Close provider session resources."""


class ReasoningClient(Protocol):
    """Text/reasoning generation contract."""

    async def generate_text(
        self,
        *,
        user_message: str,
        system_instruction: str | None = None,
        max_tokens: int = 256,
        temperature: float = 0.2,
    ) -> str:
        """Generate text output from a user prompt."""


class VisionClient(Protocol):
    """Image analysis contract."""

    async def analyze_image(
        self,
        *,
        image_data: bytes,
        mime_type: str,
        prompt: str,
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        """Run multimodal analysis and return normalized payload."""

