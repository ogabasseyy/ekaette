"""Vision tools — image analysis via Gemini Standard API + Cloud Storage upload.

These tools use the Standard API (gemini-3-flash) for detailed vision analysis,
NOT the Live API model. This is because the Live API model handles real-time
audio; complex vision grading/analysis is better served by the standard model
with Visual Thinking capabilities.
"""

import base64
import binascii
import copy
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from google import genai
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import BaseModel, Field

from app.configs import sanitize_log
from app.configs.model_resolver import resolve_vision_model_id

logger = logging.getLogger(__name__)

VISION_MODEL = resolve_vision_model_id()
MEDIA_BUCKET = os.getenv("MEDIA_BUCKET", "")

_genai_client: genai.Client | None = None
_storage_client: Any = None
_latest_images: dict[str, dict[str, Any]] = {}
_PATH_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")

try:
    _MAX_LATEST_IMAGES = max(1, int(os.getenv("VISION_LATEST_IMAGE_CACHE_SIZE", "500")))
except (TypeError, ValueError):
    _MAX_LATEST_IMAGES = 500

try:
    _LATEST_IMAGE_TTL = timedelta(
        seconds=max(1, int(os.getenv("VISION_LATEST_IMAGE_TTL_SECONDS", "900")))
    )
except (TypeError, ValueError):
    _LATEST_IMAGE_TTL = timedelta(seconds=900)

# ─── Pydantic schemas for structured output ──────────────────


class ScreenCondition(BaseModel):
    description: str = Field(default="Not assessed")
    scratches: str = Field(default="unknown", description="none|light|moderate|heavy")
    cracks: str = Field(default="none", description="none|hairline|visible|shattered")
    defect_locations: list[str] = Field(default_factory=list)


class BodyCondition(BaseModel):
    description: str = Field(default="Not assessed")
    dents: str = Field(default="unknown", description="none|minor|moderate|severe")
    scratches: str = Field(default="unknown", description="none|light|moderate|heavy")
    defect_locations: list[str] = Field(default_factory=list)


class DeviceDetails(BaseModel):
    screen: ScreenCondition = Field(default_factory=ScreenCondition)
    body: BodyCondition = Field(default_factory=BodyCondition)
    battery: str = Field(default="Not visible")
    functionality: str = Field(default="No visible damage")


class DeviceAnalysis(BaseModel):
    device_name: str = Field(default="Unknown", description="Identified device model")
    brand: str = Field(default="Unknown")
    category: str = Field(default="Unknown")
    condition: str = Field(default="Unknown", description="Excellent|Good|Fair|Poor")
    condition_justification: str = Field(default="")
    details: DeviceDetails = Field(default_factory=DeviceDetails)
    accessories_detected: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Overall assessment confidence")


_IMAGE_ANALYSIS_PROMPT = """Analyze this device image for trade-in valuation.
Identify the device model and brand. Assess condition as Excellent, Good, Fair, or Poor.
Be specific about scratches, dents, cracks, and defect locations (top-left, center, bottom-right, etc.).
Note any visible accessories (case, charger, box)."""

_VIDEO_ANALYSIS_PROMPT = """Analyze this video walkthrough of a device for trade-in valuation.
Identify the device model and brand. Assess condition as Excellent, Good, Fair, or Poor.
Examine multiple frames throughout the video for scratches, dents, cracks, and defect locations
(top-left, center, bottom-right, etc.) visible from different angles and movement.
Note any visible accessories (case, charger, box)."""

# Backward compat alias
ANALYSIS_PROMPT = _IMAGE_ANALYSIS_PROMPT


def get_analysis_prompt(mime_type: str) -> str:
    """Return the appropriate analysis prompt based on media MIME type."""
    media_category = mime_type.split("/")[0] if mime_type else "image"
    if media_category == "video":
        return _VIDEO_ANALYSIS_PROMPT
    return _IMAGE_ANALYSIS_PROMPT


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


def _prune_latest_images(now: datetime) -> None:
    cutoff = now - _LATEST_IMAGE_TTL
    stale_keys: list[str] = []
    for key, payload in list(_latest_images.items()):
        if not isinstance(payload, dict):
            stale_keys.append(key)
            continue
        cached_at_raw = payload.get("cached_at")
        if not isinstance(cached_at_raw, str) or not cached_at_raw:
            stale_keys.append(key)
            continue
        try:
            cached_at = datetime.fromisoformat(cached_at_raw)
        except ValueError:
            stale_keys.append(key)
            continue
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if cached_at < cutoff:
            stale_keys.append(key)
    for key in stale_keys:
        _latest_images.pop(key, None)


def cache_latest_image(
    user_id: str,
    session_id: str,
    image_data: bytes,
    mime_type: str,
) -> None:
    """Cache the most recent client image for this live websocket session."""
    now = datetime.now(timezone.utc)
    _prune_latest_images(now)
    _latest_images[_cache_key(user_id, session_id)] = {
        "image_data": image_data,
        "mime_type": mime_type,
        "cached_at": now.isoformat(),
    }
    while len(_latest_images) > _MAX_LATEST_IMAGES:
        oldest_key = next(iter(_latest_images))
        _latest_images.pop(oldest_key, None)


def get_latest_image(user_id: str, session_id: str) -> dict[str, Any] | None:
    """Read cached image payload for this session, if available."""
    _prune_latest_images(datetime.now(timezone.utc))
    return _latest_images.get(_cache_key(user_id, session_id))


EXTENSION_MAP: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "video/3gpp": "3gp",
}


def _artifact_filename(mime_type: str) -> str:
    if not mime_type or mime_type not in EXTENSION_MAP:
        raise ValueError(f"Unsupported MIME type for artifact: {mime_type!r}")
    raw_category = mime_type.split("/")[0]  # Always "image" or "video" per EXTENSION_MAP
    ext = EXTENSION_MAP[mime_type]
    return f"customer_{raw_category}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"


def _sanitize_path_segment(value: str, *, fallback: str) -> str:
    candidate = _PATH_SEGMENT_RE.sub("_", (value or "").strip())
    candidate = candidate.strip("._")
    return candidate or fallback


def normalize_analysis_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Ensure consistent shape regardless of old flat-string or new nested-dict format.

    - If details.screen/body is a string → wrap as {"description": value}
    - Fill missing optional fields with schema defaults
    """
    result = copy.deepcopy(raw)
    result.setdefault("device_name", "Unknown")
    result.setdefault("brand", "Unknown")
    result.setdefault("category", "Unknown")
    result.setdefault("condition", "Unknown")
    result.setdefault("condition_justification", "")
    result.setdefault("accessories_detected", [])
    result.setdefault("confidence", 0.5)

    details = result.get("details")
    if not isinstance(details, dict):
        details = {}
    result["details"] = details

    # Normalize screen
    screen = details.get("screen")
    if isinstance(screen, str):
        details["screen"] = {
            "description": screen,
            "scratches": "unknown",
            "cracks": "none",
            "defect_locations": [],
        }
    elif not isinstance(screen, dict):
        details["screen"] = {
            "description": "Not assessed",
            "scratches": "unknown",
            "cracks": "none",
            "defect_locations": [],
        }

    # Normalize body
    body = details.get("body")
    if isinstance(body, str):
        details["body"] = {
            "description": body,
            "dents": "unknown",
            "scratches": "unknown",
            "defect_locations": [],
        }
    elif not isinstance(body, dict):
        details["body"] = {
            "description": "Not assessed",
            "dents": "unknown",
            "scratches": "unknown",
            "defect_locations": [],
        }

    details.setdefault("battery", "Not visible")
    details.setdefault("functionality", "No visible damage")

    return result


async def analyze_device_media(
    media_data: bytes,
    mime_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Analyze device media (image or video) using Gemini Standard API with structured output.

    Args:
        media_data: Raw media bytes (image or video).
        mime_type: MIME type of the media.

    Returns:
        Normalized analysis dict with device_name, brand, condition, nested details.
        On failure, returns a dict with device_name="Unknown" and an error key.
    """
    prompt = get_analysis_prompt(mime_type)
    client = _get_genai_client()
    contents = [
        types.Content(
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type=mime_type,
                        data=media_data,
                    )
                ),
                types.Part(text=prompt),
            ]
        )
    ]

    # Try structured output first
    try:
        response = await client.aio.models.generate_content(
            model=VISION_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DeviceAnalysis.model_json_schema(),
                media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            ),
        )
        if not response.text:
            raise ValueError("Empty response from structured output")
        result = json.loads(response.text)
        return normalize_analysis_result(result)

    except Exception as structured_exc:
        logger.warning("Structured output failed, falling back: %s", structured_exc)

    # Fallback: manual JSON parse without structured output
    try:
        response = await client.aio.models.generate_content(
            model=VISION_MODEL,
            contents=contents,
        )

        raw_text = (response.text or "").strip()
        if not raw_text:
            return normalize_analysis_result({
                "device_name": "Unknown",
                "condition": "Unknown",
                "details": {},
                "error": "Empty response from vision model",
            })

        try:
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                raw_text = "\n".join(lines[1:-1]) if len(lines) > 2 else raw_text
            result = json.loads(raw_text)
            return normalize_analysis_result(result)
        except json.JSONDecodeError:
            return normalize_analysis_result({
                "device_name": "Unknown",
                "condition": "Unknown",
                "details": {},
                "raw_analysis": raw_text,
            })

    except Exception as exc:
        logger.error("Vision analysis failed: %s", sanitize_log(str(exc)), exc_info=True)
        return normalize_analysis_result({
            "device_name": "Unknown",
            "condition": "Unknown",
            "details": {},
            "error": "Vision analysis failed",
        })


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

    ext = EXTENSION_MAP.get(mime_type, "bin")
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

    except Exception as exc:
        logger.error("Cloud Storage upload failed: %s", sanitize_log(str(exc)), exc_info=True)
        return {"error": "Cloud Storage upload failed"}


# Backward-compat alias
async def analyze_device_image(
    image_data: bytes,
    mime_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Backward-compat alias for analyze_device_media."""
    return await analyze_device_media(media_data=image_data, mime_type=mime_type)


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

    analysis = await analyze_device_media(image_data, mime_type)

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
