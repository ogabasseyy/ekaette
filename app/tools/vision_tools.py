"""Vision tools — image analysis via Gemini Standard API + Cloud Storage upload.

These tools use a standard multimodal model for detailed vision analysis, not
the Live API audio model. Complex grading and video inspection are handled here
with stable model fallback logic so media analysis survives preview retirement
or access drift.
"""

import asyncio
import base64
import binascii
import copy
import hashlib
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
from app.configs.model_resolver import get_vision_model_candidates, resolve_vision_model_id
from app.genai_clients import build_genai_client

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
    device_color: str = Field(
        default="unknown",
        description=(
            "Dominant visible device color when it can be determined confidently, "
            "otherwise unknown"
        ),
    )
    color_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that the visible device color assessment is correct",
    )
    condition: str = Field(default="Unknown", description="Excellent|Good|Fair|Poor")
    condition_justification: str = Field(default="")
    power_state: str = Field(
        default="unknown",
        description=(
            "Visible power state based only on what can be seen in the media: "
            "on|off|unknown"
        ),
    )
    details: DeviceDetails = Field(default_factory=DeviceDetails)
    accessories_detected: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Overall assessment confidence")


_IMAGE_ANALYSIS_PROMPT = """Analyze this device image for trade-in valuation.
Identify the device model and brand. Assess condition as Excellent, Good, Fair, or Poor.
Be specific about scratches, dents, cracks, and defect locations (top-left, center, bottom-right, etc.).
Note any visible accessories (case, charger, box).
Set device_color to a grounded visible color such as red, black, white, blue, gold, silver,
green, purple, pink, gray, or unknown.
Set color_confidence between 0.0 and 1.0 based only on how clearly the device color is visible.
If lighting, reflections, cases, or framing make the color uncertain, set device_color to
"unknown" or use a low color_confidence.
Set power_state to:
- "on" only when the display, boot screen, or visible content clearly proves the device is powered on
- "off" only when the media clearly shows the device is off or not powering up
- "unknown" when power state is not visually provable from the media."""

_VIDEO_ANALYSIS_PROMPT = """Analyze this video walkthrough of a device for trade-in valuation.
Identify the device model and brand. Assess condition as Excellent, Good, Fair, or Poor.
Examine multiple frames throughout the video for scratches, dents, cracks, and defect locations
(top-left, center, bottom-right, etc.) visible from different angles and movement.
Note any visible accessories (case, charger, box).
Set device_color to a grounded visible color such as red, black, white, blue, gold, silver,
green, purple, pink, gray, or unknown.
Set color_confidence between 0.0 and 1.0 based only on how clearly the device color is visible.
If lighting, reflections, cases, motion blur, or framing make the color uncertain, set
device_color to "unknown" or use a low color_confidence.
Set power_state to:
- "on" only when the display, boot screen, or visible content clearly proves the device is powered on
- "off" only when the video clearly shows the device is off or not powering up
- "unknown" when power state is not visually provable from the media."""

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
        vision_location = os.getenv("VISION_MODEL_LOCATION", "").strip() or None
        _genai_client = build_genai_client(
            api_version="v1alpha",
            location=vision_location,
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


def _vision_model_candidates() -> list[str]:
    candidates = [VISION_MODEL]
    for candidate in get_vision_model_candidates():
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _resolved_model_candidates(model_candidates: list[str] | None = None) -> list[str]:
    if not model_candidates:
        return _vision_model_candidates()
    candidates: list[str] = []
    for candidate in model_candidates:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates or _vision_model_candidates()


def _is_model_unavailable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {403, 404, 429}:
        return True
    message = f"{exc}".lower()
    return (
        (
            "publisher model" in message
            and ("not found" in message or "does not have access" in message)
        )
        or "resource exhausted" in message
        or "quota exceeded" in message
        or "rate limit" in message
        or "too many requests" in message
    )


def _unknown_analysis_result(error: str) -> dict[str, Any]:
    return normalize_analysis_result({
        "device_name": "Unknown",
        "condition": "Unknown",
        "details": {},
        "error": error,
    })


def _parse_vision_response_text(raw_text: str) -> dict[str, Any]:
    normalized = (raw_text or "").strip()
    if not normalized:
        return _unknown_analysis_result("Empty response from vision model")

    if normalized.startswith("```"):
        lines = normalized.split("\n")
        normalized = "\n".join(lines[1:-1]) if len(lines) > 2 else normalized

    try:
        return normalize_analysis_result(json.loads(normalized))
    except json.JSONDecodeError:
        return normalize_analysis_result({
            "device_name": "Unknown",
            "condition": "Unknown",
            "details": {},
            "raw_analysis": normalized,
        })


def _build_user_media_content(
    *,
    media_data: bytes,
    mime_type: str,
    prompt: str,
) -> list[types.Content]:
    """Build a Gemini 3 compatible user turn with inline media + prompt text."""
    return [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type=mime_type,
                        data=media_data,
                    )
                ),
                types.Part(text=prompt),
            ],
        )
    ]


def _media_fingerprint(media_data: bytes, mime_type: str) -> str:
    digest = hashlib.sha256()
    digest.update((mime_type or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(media_data)
    return digest.hexdigest()


def _persist_tool_state(tool_context: ToolContext, key: str, value: Any) -> None:
    try:
        tool_context.state[key] = value
    except Exception:
        logger.debug("Failed to persist tool state key %s", key, exc_info=True)
    session_state = getattr(getattr(tool_context, "session", None), "state", None)
    if session_state is not None:
        try:
            session_state[key] = value
        except Exception:
            logger.debug("Failed to persist session tool state key %s", key, exc_info=True)


def _cached_analysis_for_media(
    tool_context: ToolContext,
    *,
    media_fingerprint: str,
) -> dict[str, Any] | None:
    cached_fingerprint = _tool_state_value(
        tool_context,
        "temp:last_analyzed_media_fingerprint",
    )
    cached_result = _tool_state_value(
        tool_context,
        "temp:last_analyzed_media_result",
    )
    if cached_fingerprint != media_fingerprint or not isinstance(cached_result, dict):
        return None
    return copy.deepcopy(cached_result)


def _cache_analysis_for_media(
    tool_context: ToolContext,
    *,
    media_fingerprint: str,
    analysis: dict[str, Any],
) -> None:
    _persist_tool_state(tool_context, "temp:last_analyzed_media_fingerprint", media_fingerprint)
    _persist_tool_state(
        tool_context,
        "temp:last_analyzed_media_result",
        copy.deepcopy(analysis),
    )


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
    legacy_device_name = result.get("device_model")
    if (
        result["device_name"] == "Unknown"
        and isinstance(legacy_device_name, str)
        and legacy_device_name.strip()
    ):
        result["device_name"] = legacy_device_name.strip()
    result.setdefault("brand", "Unknown")
    legacy_brand = result.get("device_brand")
    if result["brand"] == "Unknown" and isinstance(legacy_brand, str) and legacy_brand.strip():
        result["brand"] = legacy_brand.strip()
    result.setdefault("category", "Unknown")
    color_raw = result.get("device_color", "unknown")
    if isinstance(color_raw, str) and color_raw.strip():
        result["device_color"] = color_raw.strip().lower()
    else:
        result["device_color"] = "unknown"
    color_confidence_raw = result.get("color_confidence", 0.0)
    try:
        color_confidence = float(color_confidence_raw)
    except (TypeError, ValueError):
        color_confidence = 0.0
    result["color_confidence"] = max(0.0, min(1.0, color_confidence))
    result.setdefault("condition", "Unknown")
    result.setdefault("condition_justification", "")
    power_state_raw = result.get("power_state", "unknown")
    if isinstance(power_state_raw, str) and power_state_raw.strip().lower() in {"on", "off", "unknown"}:
        result["power_state"] = power_state_raw.strip().lower()
    else:
        result["power_state"] = "unknown"
    result.setdefault("accessories_detected", [])
    legacy_accessories = result.get("accessories")
    if (
        not result["accessories_detected"]
        and isinstance(legacy_accessories, list)
    ):
        result["accessories_detected"] = [
            str(item).strip()
            for item in legacy_accessories
            if isinstance(item, str) and item.strip()
        ]
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
    *,
    model_candidates: list[str] | None = None,
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
    contents = _build_user_media_content(
        media_data=media_data,
        mime_type=mime_type,
        prompt=prompt,
    )

    last_unavailable_error: Exception | None = None

    for model_id in _resolved_model_candidates(model_candidates):
        try:
            structured_config_kwargs: dict[str, Any] = {
                "response_mime_type": "application/json",
                "response_schema": DeviceAnalysis.model_json_schema(),
            }
            if not mime_type.startswith("video/"):
                structured_config_kwargs["media_resolution"] = (
                    types.MediaResolution.MEDIA_RESOLUTION_HIGH
                )
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=types.GenerateContentConfig(**structured_config_kwargs),
            )
            if not response.text:
                raise ValueError("Empty response from structured output")
            return normalize_analysis_result(json.loads(response.text))

        except Exception as structured_exc:
            if _is_model_unavailable_error(structured_exc):
                last_unavailable_error = structured_exc
                logger.warning(
                    "Structured vision model unavailable model=%s error=%s",
                    model_id,
                    sanitize_log(str(structured_exc)),
                )
                continue
            logger.warning(
                "Structured output failed for model=%s, falling back: %s",
                model_id,
                sanitize_log(str(structured_exc)),
            )

        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
            )
            return _parse_vision_response_text(response.text or "")

        except Exception as exc:
            if _is_model_unavailable_error(exc):
                last_unavailable_error = exc
                logger.warning(
                    "Vision model unavailable model=%s error=%s",
                    model_id,
                    sanitize_log(str(exc)),
                )
                continue
            logger.error(
                "Vision analysis failed model=%s: %s",
                model_id,
                sanitize_log(str(exc)),
                exc_info=True,
            )
            return _unknown_analysis_result("Vision analysis failed")

    if last_unavailable_error is not None:
        logger.error(
            "All configured vision models were unavailable: %s",
            sanitize_log(str(last_unavailable_error)),
        )
        return _unknown_analysis_result("Vision model unavailable")

    return _unknown_analysis_result("Vision analysis failed")


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


def _tool_state_value(tool_context: ToolContext, key: str, default: Any = None) -> Any:
    value = tool_context.state.get(key, default)
    if value != default:
        return value
    session_state = getattr(getattr(tool_context, "session", None), "state", None)
    if isinstance(session_state, dict):
        return session_state.get(key, default)
    return default


async def _download_media_blob(blob_path: str) -> bytes | None:
    storage_client = _get_storage_client()
    if storage_client is None or not MEDIA_BUCKET or not blob_path:
        return None
    try:
        bucket = storage_client.bucket(MEDIA_BUCKET)
        blob = bucket.blob(blob_path)
        return await asyncio.to_thread(blob.download_as_bytes)
    except Exception as exc:
        logger.warning("Cloud Storage download failed (%s): %s", blob_path, exc)
        return None


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
    persisted_blob_path = ""
    persisted_gcs_uri = ""
    persisted_mime_type = ""

    if tool_context is not None:
        user_id = tool_context.user_id or ""
        session = getattr(tool_context, "session", None)
        session_id = getattr(session, "id", "") if session else ""
        persisted_blob_path = str(
            _tool_state_value(tool_context, "temp:last_media_blob_path", "") or ""
        ).strip()
        persisted_gcs_uri = str(
            _tool_state_value(tool_context, "temp:last_media_gcs_uri", "") or ""
        ).strip()
        persisted_mime_type = str(
            _tool_state_value(tool_context, "temp:last_media_mime_type", "") or ""
        ).strip()
        if persisted_mime_type:
            mime_type = persisted_mime_type

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

    if image_data is None and persisted_blob_path:
        image_data = await _download_media_blob(persisted_blob_path)

    if image_data is None:
        return {
            "device_name": "Unknown",
            "condition": "Unknown",
            "details": {},
            "error": "No image payload available for analysis",
        }

    media_fingerprint = _media_fingerprint(image_data, mime_type)

    if tool_context is not None:
        cached_analysis = _cached_analysis_for_media(
            tool_context,
            media_fingerprint=media_fingerprint,
        )
        if cached_analysis is not None:
            logger.info(
                "Reusing cached vision analysis session=%s fingerprint=%s mime=%s",
                session_id or "unknown-session",
                media_fingerprint[:12],
                mime_type,
            )
            return cached_analysis

    analysis = await analyze_device_media(image_data, mime_type)

    if tool_context is None:
        return analysis

    effective_user_id = user_id or "anonymous"
    effective_session_id = session_id or "unknown-session"

    if persisted_gcs_uri:
        analysis["gcs_uri"] = persisted_gcs_uri
        if persisted_blob_path:
            analysis["blob_path"] = persisted_blob_path
    else:
        upload_result = await upload_to_cloud_storage(
            image_data=image_data,
            mime_type=mime_type,
            user_id=effective_user_id,
            session_id=effective_session_id,
        )
        if "gcs_uri" in upload_result:
            analysis["gcs_uri"] = upload_result["gcs_uri"]
            blob_path = upload_result.get("blob_path")
            if isinstance(blob_path, str) and blob_path:
                analysis["blob_path"] = blob_path
                _persist_tool_state(tool_context, "temp:last_media_blob_path", blob_path)
            _persist_tool_state(tool_context, "temp:last_media_gcs_uri", upload_result["gcs_uri"])
            _persist_tool_state(tool_context, "temp:last_media_mime_type", mime_type)
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

    if not analysis.get("error"):
        _cache_analysis_for_media(
            tool_context,
            media_fingerprint=media_fingerprint,
            analysis=analysis,
        )

    return analysis
