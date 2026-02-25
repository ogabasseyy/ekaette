"""Ekaette — FastAPI Backend with ADK Bidi-Streaming."""

import asyncio
import base64
import json
import logging
import os
import re
import warnings

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

load_dotenv()

from app.agents.ekaette_router.agent import ekaette_router  # noqa: E402

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, LOG_LEVEL, logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Regex pattern for characters that enable log injection (newlines + control chars).
_LOG_UNSAFE_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def _sanitize_log(value: str | None) -> str:
    """Strip newlines and control characters from user-supplied values before logging."""
    if value is None:
        return "<none>"
    return _LOG_UNSAFE_RE.sub("", value)[:200]


# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# ═══ Application Init ═══
APP_NAME = os.getenv("APP_NAME", "ekaette")

app = FastAPI(title="Ekaette")


def _parse_allowlist(raw_origins: str) -> list[str]:
    """Parse comma-delimited origins into a clean list."""
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


# ═══ CORS Middleware — explicit allowlist, no wildcard ═══
ALLOWED_ORIGINS = _parse_allowlist(
    os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000")
)
ALLOWED_ORIGIN_SET = set(ALLOWED_ORIGINS)


def _is_origin_allowed(origin: str | None) -> bool:
    """Validate browser Origin against explicit allowlist."""
    return origin in ALLOWED_ORIGIN_SET

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══ Session Service ═══
session_service = InMemorySessionService()  # → DatabaseSessionService in S6

# ═══ Runner ═══
runner = Runner(
    app_name=APP_NAME,
    agent=ekaette_router,
    session_service=session_service,
)


# ═══ HTTP Endpoints ═══

@app.get("/health")
async def health():
    """Health check endpoint for Cloud Run and monitoring."""
    return {"status": "ok", "app": APP_NAME}


# ═══ WebSocket Endpoint ═══

@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """WebSocket endpoint for bidirectional streaming with ADK."""
    origin = websocket.headers.get("origin")
    if not _is_origin_allowed(origin):
        logger.warning("Rejected WebSocket origin: %s", _sanitize_log(origin))
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    logger.debug("WebSocket connection request: user_id=%s, session_id=%s", _sanitize_log(user_id), _sanitize_log(session_id))
    await websocket.accept()
    logger.debug("WebSocket connection accepted")

    # ═══ Session Init ═══
    model_name = ekaette_router.model
    is_native_audio = "native-audio" in model_name.lower()

    if is_native_audio:
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=80000,
                sliding_window=types.SlidingWindow(target_tokens=40000),
            ),
        )
    else:
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["TEXT"],
            session_resumption=types.SessionResumptionConfig(),
        )

    logger.debug("Model: %s, native_audio=%s", model_name, is_native_audio)

    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if not session:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )

    live_request_queue = LiveRequestQueue()

    # ═══ Bidi-Streaming Tasks ═══

    async def upstream_task() -> None:
        """Receives from WebSocket, sends to LiveRequestQueue."""
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                raise WebSocketDisconnect(code=message.get("code", 1000))

            audio_data = message.get("bytes")
            text_data = message.get("text")

            # Binary frames: audio data
            if audio_data is not None:
                audio_blob = types.Blob(
                    mime_type="audio/pcm;rate=16000", data=audio_data
                )
                live_request_queue.send_realtime(audio_blob)

            # Text frames: JSON messages
            elif text_data is not None:
                try:
                    json_message = json.loads(text_data)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "code": "INVALID_JSON",
                                "message": "Malformed JSON payload",
                            }
                        )
                    )
                    continue

                if json_message.get("type") == "text":
                    content = types.Content(
                        parts=[types.Part(text=json_message["text"])]
                    )
                    live_request_queue.send_content(content)

                elif json_message.get("type") == "image":
                    image_data = base64.b64decode(json_message["data"])
                    mime_type = json_message.get("mimeType", "image/jpeg")
                    image_blob = types.Blob(
                        mime_type=mime_type, data=image_data
                    )
                    live_request_queue.send_realtime(image_blob)
                else:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "code": "UNSUPPORTED_MESSAGE_TYPE",
                                "message": "Unsupported client message type",
                            }
                        )
                    )

    async def downstream_task() -> None:
        """Receives Events from run_live(), sends to WebSocket."""
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            event_json = event.model_dump_json(exclude_none=True, by_alias=True)
            await websocket.send_text(event_json)

    try:
        upstream = asyncio.create_task(upstream_task(), name="upstream_task")
        downstream = asyncio.create_task(downstream_task(), name="downstream_task")
        tasks = {upstream, downstream}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        logger.debug("Client disconnected normally")
    except Exception as e:
        logger.error("Streaming error: %s", e, exc_info=True)
    finally:
        live_request_queue.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
