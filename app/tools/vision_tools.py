"""Vision tools — image analysis via Gemini Standard API + Cloud Storage upload.

These tools use the Standard API (gemini-3-flash) for detailed vision analysis,
NOT the Live API model. This is because the Live API model handles real-time
audio; complex vision grading/analysis is better served by the standard model
with Visual Thinking capabilities.
"""

import base64
import binascii
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from google import genai
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3-flash-preview")
MEDIA_BUCKET = os.getenv("MEDIA_BUCKET", "")

_genai_client: genai.Client | None = None
_storage_client: Any = None
_latest_images: dict[str, dict[str, Any]] = {}
_PATH_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")

try:
    _MAX_LATEST_IMAGES = max(1, int(os.getenv("VISION_LATEST_IMAGE_CACHE_SIZE", "500")))
except (TypeError, ValueError):
    _MAX_LATEST_IMAGES = 500

ANALYSIS_PROMPT = """Analyze this device image for a trade-in valuation.
Return a JSON object with exactly this structure:
{
  "device_name": "<identified device model, e.g. 'iPhone 14 Pro'>",
  "condition": "<one of: Excellent, Good, Fair, Poor>",
  "details": {
    "screen": "<description of screen condition>",
    "body": "<description of body/chassis condition>",
    "battery": "<estimated battery health if visible, else 'Not visible'>",
    "functionality": "<any visible damage affecting function>"
  }
}
Be specific about scratches, dents, cracks, discoloration, and wear.
If you cannot identify the device, set device_name to "Unknown".
Return ONLY the JSON object, no markdown or extra text."""


def _get_genai_client() -> genai.Client:
    """Get or create the GenAI client for Standard API calls."""
    global _genai_client
    if _genai_client is None:
        api_key = os.getenv("GOOGLE_API_KEY", "")
        _genai_client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version="v1alpha"),
        )
    return _genai_client


def _get_storage_client() -> Any | None:
    """Get or create Cloud Storage client. Returns None if unavailable."""
    global _storage_client
    if _storage_client is not None:
        return _storage_client
    try:
        from google.cloud import storage
        _storage_client = storage.Client()
        return _storage_client
    except Exception as exc:
        logger.warning("Cloud Storage client unavailable: %s", exc)
        return None


def _cache_key(user_id: str, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def cache_latest_image(
    user_id: str,
    session_id: str,
    image_data: bytes,
    mime_type: str,
) -> None:
    """Cache the most recent client image for this live websocket session."""
    _latest_images[_cache_key(user_id, session_id)] = {
        "image_data": image_data,
        "mime_type": mime_type,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    while len(_latest_images) > _MAX_LATEST_IMAGES:
        oldest_key = next(iter(_latest_images))
        _latest_images.pop(oldest_key, None)


def get_latest_image(user_id: str, session_id: str) -> dict[str, Any] | None:
    """Read cached image payload for this session, if available."""
    return _latest_images.get(_cache_key(user_id, session_id))


def _artifact_filename(mime_type: str) -> str:
    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/heic": "heic",
        "image/heif": "heif",
    }
    ext = ext_map.get(mime_type, "bin")
    return f"customer_image_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"


def _sanitize_path_segment(value: str, *, fallback: str) -> str:
    candidate = _PATH_SEGMENT_RE.sub("_", (value or "").strip())
    candidate = candidate.strip("._")
    return candidate or fallback


async def analyze_device_image(
    image_data: bytes,
    mime_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Analyze a device image using Gemini Standard API.

    Args:
        image_data: Raw image bytes.
        mime_type: MIME type of the image.

    Returns:
        Structured analysis dict with device_name, condition, details.
        On failure, returns a dict with device_name="Unknown" and an error key.
    """
    client = _get_genai_client()

    try:
        response = await client.aio.models.generate_content(
            model=VISION_MODEL,
            contents=[
                types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(
                                mime_type=mime_type,
                                data=image_data,
                            )
                        ),
                        types.Part(text=ANALYSIS_PROMPT),
                    ]
                )
            ],
        )

        raw_text = response.text.strip()

        # Try to parse as JSON
        try:
            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                raw_text = "\n".join(lines[1:-1]) if len(lines) > 2 else raw_text
            result = json.loads(raw_text)
            # Ensure required keys
            result.setdefault("device_name", "Unknown")
            result.setdefault("condition", "Unknown")
            result.setdefault("details", {})
            return result
        except json.JSONDecodeError:
            return {
                "device_name": "Unknown",
                "condition": "Unknown",
                "details": {},
                "raw_analysis": raw_text,
            }

    except Exception:
        logger.exception("Vision analysis failed")
        return {
            "device_name": "Unknown",
            "condition": "Unknown",
            "details": {},
            "error": "Vision analysis failed",
        }


async def upload_to_cloud_storage(
    image_data: bytes,
    mime_type: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Upload customer image to Cloud Storage.

    Args:
        image_data: Raw image bytes.
        mime_type: MIME type (e.g. image/jpeg).
        user_id: Customer user ID.
        session_id: Current session ID.

    Returns:
        Dict with gcs_uri on success, or error key on failure.
    """
    storage_client = _get_storage_client()
    if storage_client is None:
        return {"error": "Cloud Storage unavailable"}

    if not MEDIA_BUCKET:
        return {"error": "MEDIA_BUCKET not configured"}

    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/heic": "heic",
        "image/heif": "heif",
    }
    ext = ext_map.get(mime_type, "bin")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    safe_user_id = _sanitize_path_segment(user_id, fallback="anonymous")
    safe_session_id = _sanitize_path_segment(session_id, fallback="unknown-session")
    blob_path = f"uploads/{safe_user_id}/{safe_session_id}/{timestamp}_{unique_id}.{ext}"

    try:
        bucket = storage_client.bucket(MEDIA_BUCKET)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(image_data, content_type=mime_type)

        gcs_uri = f"gs://{MEDIA_BUCKET}/{blob_path}"
        logger.info("Uploaded image: %s", gcs_uri)
        return {"gcs_uri": gcs_uri, "blob_path": blob_path}

    except Exception:
        logger.exception("Cloud Storage upload failed")
        return {"error": "Cloud Storage upload failed"}


async def analyze_device_image_tool(
    image_base64: str | None = None,
    mime_type: str = "image/jpeg",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """ADK tool wrapper for live image analysis.

    Tool call priority:
      1) Explicit base64 payload from model arguments.
      2) Latest websocket image cached by `main.py` for this user/session.
      3) Last saved artifact (for resumed sessions).
    """
    image_data: bytes | None = None
    session_id = ""
    user_id = ""

    if tool_context is not None:
        user_id = tool_context.user_id or ""
        session = getattr(tool_context, "session", None)
        session_id = getattr(session, "id", "") if session else ""

    if image_base64:
        try:
            image_data = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            return {
                "device_name": "Unknown",
                "condition": "Unknown",
                "details": {},
                "error": f"Invalid base64 image data: {exc}",
            }
    elif user_id and session_id:
        cached = get_latest_image(user_id, session_id)
        if cached and isinstance(cached.get("image_data"), bytes):
            image_data = cached["image_data"]
            cached_mime = cached.get("mime_type")
            if isinstance(cached_mime, str) and cached_mime:
                mime_type = cached_mime

    if image_data is None and tool_context is not None:
        artifact_id = tool_context.state.get("temp:last_image_artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            try:
                artifact = await tool_context.load_artifact(artifact_id)
                inline_data = getattr(artifact, "inline_data", None)
                if inline_data and inline_data.data:
                    image_data = inline_data.data
                    if inline_data.mime_type:
                        mime_type = inline_data.mime_type
            except Exception as exc:
                logger.warning("Artifact load failed (%s): %s", artifact_id, exc)

    if image_data is None:
        return {
            "device_name": "Unknown",
            "condition": "Unknown",
            "details": {},
            "error": "No image payload available for analysis",
        }

    analysis = await analyze_device_image(image_data, mime_type)

    if tool_context is None:
        return analysis

    effective_user_id = user_id or "anonymous"
    effective_session_id = session_id or "unknown-session"

    upload_result = await upload_to_cloud_storage(
        image_data=image_data,
        mime_type=mime_type,
        user_id=effective_user_id,
        session_id=effective_session_id,
    )
    if "gcs_uri" in upload_result:
        analysis["gcs_uri"] = upload_result["gcs_uri"]
    elif "error" in upload_result:
        analysis["upload_error"] = upload_result["error"]

    try:
        artifact_id = _artifact_filename(mime_type)
        artifact_part = types.Part(
            inline_data=types.Blob(mime_type=mime_type, data=image_data)
        )
        artifact_version = await tool_context.save_artifact(
            artifact_id,
            artifact_part,
            custom_metadata={
                "mime_type": mime_type,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        tool_context.state["temp:last_image_artifact_id"] = artifact_id
        analysis["artifact_id"] = artifact_id
        analysis["artifact_version"] = artifact_version
    except Exception as exc:
        logger.warning("Artifact save failed: %s", exc)
        analysis["artifact_error"] = str(exc)

    return analysis
