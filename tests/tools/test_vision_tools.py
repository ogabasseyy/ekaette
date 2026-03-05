"""Tests for vision tools — TDD for S8."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAnalyzeDeviceImage:
    """Test image analysis via Gemini vision model."""

    @pytest.mark.asyncio
    async def test_returns_structured_analysis(self):
        """Should return device_name, condition, and details from image."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = (
            '{"device_name": "iPhone 14 Pro", "condition": "Good", '
            '"details": {"screen": "Minor scratches", "body": "Small dent on corner", '
            '"battery": "85% health", "functionality": "All features working"}}'
        )

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            result = await analyze_device_image(
                image_data=b"fake-image-bytes",
                mime_type="image/jpeg",
            )

        assert result["device_name"] == "iPhone 14 Pro"
        assert result["condition"] == "Good"
        assert "screen" in result["details"]
        assert "body" in result["details"]

    @pytest.mark.asyncio
    async def test_handles_non_json_response_gracefully(self):
        """Should wrap plain text response when model doesn't return JSON."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = "This appears to be an iPhone 14 Pro in good condition."

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            result = await analyze_device_image(
                image_data=b"fake-image-bytes",
                mime_type="image/jpeg",
            )

        assert result["device_name"] == "Unknown"
        assert result["condition"] == "Unknown"
        assert "raw_analysis" in result

    @pytest.mark.asyncio
    async def test_handles_api_error_gracefully(self):
        """Should return error result when API call fails."""
        from app.tools.vision_tools import analyze_device_image

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=Exception("API unavailable")
        )

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            result = await analyze_device_image(
                image_data=b"fake-image-bytes",
                mime_type="image/jpeg",
            )

        assert result["device_name"] == "Unknown"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_uses_correct_vision_model(self):
        """Should call gemini-3-flash-preview (standard API, not live)."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = '{"device_name": "Test", "condition": "Good", "details": {}}'

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client), \
             patch("app.tools.vision_tools.VISION_MODEL", "gemini-3-flash-preview"):
            await analyze_device_image(
                image_data=b"fake-image-bytes",
                mime_type="image/jpeg",
            )

        call_kwargs = mock_client.aio.models.generate_content.call_args
        assert "gemini-3-flash-preview" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_sends_image_as_inline_data(self):
        """Should send image bytes as inline_data Part to the model."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = '{"device_name": "Test", "condition": "Good", "details": {}}'

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        image_bytes = b"\xff\xd8\xff\xe0fake-jpeg-data"

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            await analyze_device_image(
                image_data=image_bytes,
                mime_type="image/jpeg",
            )

        mock_client.aio.models.generate_content.assert_awaited_once()


class TestUploadToCloudStorage:
    """Test Cloud Storage upload for customer media."""

    def test_artifact_filename_uses_heic_extension(self):
        """HEIC uploads should persist with a stable HEIC extension."""
        from app.tools.vision_tools import _artifact_filename

        filename = _artifact_filename("image/heic")
        assert filename.endswith(".heic")

    @pytest.mark.asyncio
    async def test_uploads_with_correct_bucket_and_path(self):
        """Should upload to configured bucket with structured path."""
        from app.tools.vision_tools import upload_to_cloud_storage

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with patch("app.tools.vision_tools._get_storage_client", return_value=mock_storage_client), \
             patch("app.tools.vision_tools.MEDIA_BUCKET", "test-bucket"):
            result = await upload_to_cloud_storage(
                image_data=b"fake-image-bytes",
                mime_type="image/jpeg",
                user_id="test-user",
                session_id="test-session",
            )

        mock_storage_client.bucket.assert_called_once_with("test-bucket")
        mock_blob.upload_from_string.assert_called_once_with(
            b"fake-image-bytes", content_type="image/jpeg"
        )
        assert "gcs_uri" in result
        assert "test-user" in result["gcs_uri"]

    @pytest.mark.asyncio
    async def test_uploads_heif_with_correct_extension(self):
        """HEIF uploads should keep .heif extension in blob path."""
        from app.tools.vision_tools import upload_to_cloud_storage

        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with patch("app.tools.vision_tools._get_storage_client", return_value=mock_storage_client), \
             patch("app.tools.vision_tools.MEDIA_BUCKET", "test-bucket"):
            result = await upload_to_cloud_storage(
                image_data=b"fake-image-bytes",
                mime_type="image/heif",
                user_id="test-user",
                session_id="test-session",
            )

        assert "blob_path" in result
        assert result["blob_path"].endswith(".heif")

    @pytest.mark.asyncio
    async def test_returns_error_when_storage_unavailable(self):
        """Should return error dict when Cloud Storage fails."""
        from app.tools.vision_tools import upload_to_cloud_storage

        with patch("app.tools.vision_tools._get_storage_client", return_value=None):
            result = await upload_to_cloud_storage(
                image_data=b"fake-image-bytes",
                mime_type="image/jpeg",
                user_id="test-user",
                session_id="test-session",
            )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_upload_exception(self):
        """Should handle upload exceptions gracefully."""
        from app.tools.vision_tools import upload_to_cloud_storage

        mock_blob = MagicMock()
        mock_blob.upload_from_string.side_effect = Exception("Storage error")

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with patch("app.tools.vision_tools._get_storage_client", return_value=mock_storage_client), \
             patch("app.tools.vision_tools.MEDIA_BUCKET", "test-bucket"):
            result = await upload_to_cloud_storage(
                image_data=b"fake-image-bytes",
                mime_type="image/jpeg",
                user_id="test-user",
                session_id="test-session",
            )

        assert "error" in result


class TestAnalyzeDeviceImageTool:
    """Test the ADK-compatible tool wrapper for vision analysis."""

    @pytest.mark.asyncio
    async def test_tool_returns_dict(self):
        """The ADK tool function should return a dict for the agent."""
        from app.tools.vision_tools import analyze_device_image_tool

        mock_response = MagicMock()
        mock_response.text = '{"device_name": "Samsung S24", "condition": "Excellent", "details": {}}'

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            result = await analyze_device_image_tool(
                image_base64="ZmFrZS1pbWFnZQ==",  # base64 of "fake-image"
                mime_type="image/jpeg",
            )

        assert isinstance(result, dict)
        assert result["device_name"] == "Samsung S24"

    @pytest.mark.asyncio
    async def test_tool_handles_invalid_base64(self):
        """Should return error for invalid base64 input."""
        from app.tools.vision_tools import analyze_device_image_tool

        result = await analyze_device_image_tool(
            image_base64="not-valid-base64!!!",
            mime_type="image/jpeg",
        )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_uses_cached_websocket_image_when_no_base64(self):
        """Should analyze the latest cached websocket image via ToolContext."""
        from app.tools.vision_tools import analyze_device_image_tool, cache_latest_image

        cache_latest_image(
            user_id="test-user",
            session_id="session-1",
            image_data=b"cached-image-bytes",
            mime_type="image/jpeg",
        )

        fake_context = SimpleNamespace(
            user_id="test-user",
            session=SimpleNamespace(id="session-1"),
            state={},
            save_artifact=AsyncMock(return_value=1),
            load_artifact=AsyncMock(return_value=None),
        )

        with patch(
            "app.tools.vision_tools.analyze_device_image",
            new=AsyncMock(
                return_value={"device_name": "iPhone 14 Pro", "condition": "Good", "details": {}}
            ),
        ) as analyze_mock, patch(
            "app.tools.vision_tools.upload_to_cloud_storage",
            new=AsyncMock(return_value={"gcs_uri": "gs://test-bucket/path.jpg"}),
        ):
            result = await analyze_device_image_tool(
                image_base64=None,
                mime_type="image/jpeg",
                tool_context=fake_context,
            )

        analyze_mock.assert_awaited_once()
        assert result["device_name"] == "iPhone 14 Pro"
        assert result["gcs_uri"].startswith("gs://")
        assert "artifact_id" in result
        assert fake_context.state["temp:last_image_artifact_id"] == result["artifact_id"]

    @pytest.mark.asyncio
    async def test_tool_returns_error_when_no_image_available(self):
        """Should fail gracefully when neither base64 nor cached image exists."""
        from app.tools.vision_tools import analyze_device_image_tool

        fake_context = SimpleNamespace(
            user_id="test-user",
            session=SimpleNamespace(id="missing-session"),
            state={},
            save_artifact=AsyncMock(return_value=1),
            load_artifact=AsyncMock(return_value=None),
        )
        result = await analyze_device_image_tool(
            image_base64=None,
            mime_type="image/jpeg",
            tool_context=fake_context,
        )
        assert "error" in result


class TestEnhancedAnalysisOutput:
    """Test structured output with brand, sub-scored details, defect_locations."""

    @pytest.mark.asyncio
    async def test_structured_output_returns_brand_and_nested_details(self):
        """Structured output should include brand and nested screen/body details."""
        from app.tools.vision_tools import analyze_device_image

        structured_json = json.dumps({
            "device_name": "iPhone 15 Pro",
            "brand": "Apple",
            "category": "phone",
            "condition": "Good",
            "condition_justification": "Minor scratches on screen",
            "details": {
                "screen": {
                    "description": "Light scratches near edges",
                    "scratches": "light",
                    "cracks": "none",
                    "defect_locations": ["top-left"],
                },
                "body": {
                    "description": "Small dent on corner",
                    "dents": "minor",
                    "scratches": "none",
                    "defect_locations": ["bottom-right"],
                },
                "battery": "Not visible",
                "functionality": "No visible damage",
            },
            "accessories_detected": ["case"],
            "confidence": 0.85,
        })

        mock_response = MagicMock()
        mock_response.text = structured_json
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            result = await analyze_device_image(b"fake-image", "image/jpeg")

        assert result["brand"] == "Apple"
        assert result["device_name"] == "iPhone 15 Pro"
        assert result["condition"] == "Good"
        assert isinstance(result["details"]["screen"], dict)
        assert result["details"]["screen"]["scratches"] == "light"
        assert result["details"]["screen"]["defect_locations"] == ["top-left"]
        assert result["confidence"] == 0.85
        assert result["accessories_detected"] == ["case"]

    @pytest.mark.asyncio
    async def test_structured_output_backward_compat_has_device_name_and_condition(self):
        """Enhanced output still includes legacy device_name + condition fields."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "device_name": "Samsung S24",
            "brand": "Samsung",
            "condition": "Fair",
            "details": {
                "screen": {"description": "Visible scratches", "scratches": "moderate", "cracks": "none", "defect_locations": []},
                "body": {"description": "Dent on back", "dents": "moderate", "scratches": "none", "defect_locations": []},
                "battery": "Not visible",
                "functionality": "No visible damage",
            },
            "confidence": 0.7,
        })
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            result = await analyze_device_image(b"fake-image", "image/jpeg")

        assert "device_name" in result
        assert "condition" in result
        assert result["device_name"] == "Samsung S24"
        assert result["condition"] == "Fair"

    @pytest.mark.asyncio
    async def test_structured_output_uses_response_schema(self):
        """Should pass response_schema and response_mime_type to the API call."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "device_name": "Test",
            "brand": "Unknown",
            "condition": "Good",
            "details": {},
            "confidence": 0.5,
        })
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            await analyze_device_image(b"fake-image", "image/jpeg")

        call_kwargs = mock_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config is not None
        assert config.response_mime_type == "application/json"
        assert config.response_schema is not None

    @pytest.mark.asyncio
    async def test_structured_output_uses_high_media_resolution(self):
        """Should use MEDIA_RESOLUTION_HIGH for better device grading."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "device_name": "Test",
            "brand": "Unknown",
            "condition": "Good",
            "details": {},
            "confidence": 0.5,
        })
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            await analyze_device_image(b"fake-image", "image/jpeg")

        call_kwargs = mock_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config is not None
        assert config.media_resolution == "MEDIA_RESOLUTION_HIGH"

    @pytest.mark.asyncio
    async def test_fallback_on_structured_output_failure(self):
        """If structured output raises, fallback to manual JSON parse."""
        from app.tools.vision_tools import analyze_device_image

        mock_response = MagicMock()
        mock_response.text = '{"device_name": "iPhone 14", "condition": "Good", "details": {}}'

        mock_client = MagicMock()
        # First call (structured output) fails, second call (fallback) succeeds
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[Exception("Structured output not supported"), mock_response]
        )

        with patch("app.tools.vision_tools._get_genai_client", return_value=mock_client):
            result = await analyze_device_image(b"fake-image", "image/jpeg")

        assert mock_client.aio.models.generate_content.call_count == 2
        assert result["device_name"] == "iPhone 14"
        assert result["condition"] == "Good"


class TestNormalizeAnalysisResult:
    """Test normalize_analysis_result() for consistent output shape."""

    def test_normalizes_old_flat_string_details(self):
        """Old-style flat string details should be wrapped as {description: value}."""
        from app.tools.vision_tools import normalize_analysis_result

        raw = {
            "device_name": "iPhone 14",
            "condition": "Good",
            "details": {
                "screen": "Minor scratches",
                "body": "Small dent on corner",
                "battery": "85% health",
                "functionality": "All features working",
            },
        }
        result = normalize_analysis_result(raw)
        assert isinstance(result["details"]["screen"], dict)
        assert result["details"]["screen"]["description"] == "Minor scratches"
        assert isinstance(result["details"]["body"], dict)
        assert result["details"]["body"]["description"] == "Small dent on corner"

    def test_normalizes_new_nested_dict_passthrough(self):
        """New-style nested dict details should pass through unchanged."""
        from app.tools.vision_tools import normalize_analysis_result

        raw = {
            "device_name": "iPhone 15",
            "brand": "Apple",
            "condition": "Excellent",
            "details": {
                "screen": {
                    "description": "Pristine",
                    "scratches": "none",
                    "cracks": "none",
                    "defect_locations": [],
                },
                "body": {
                    "description": "No damage",
                    "dents": "none",
                    "scratches": "none",
                    "defect_locations": [],
                },
                "battery": "Not visible",
                "functionality": "No visible damage",
            },
            "confidence": 0.9,
        }
        result = normalize_analysis_result(raw)
        assert result["details"]["screen"]["scratches"] == "none"
        assert result["confidence"] == 0.9

    def test_fills_defaults_for_missing_optional_fields(self):
        """Missing optional fields should be filled with schema defaults."""
        from app.tools.vision_tools import normalize_analysis_result

        raw = {
            "device_name": "Unknown Device",
            "condition": "Fair",
        }
        result = normalize_analysis_result(raw)
        assert "brand" in result
        assert "details" in result
        assert isinstance(result["details"], dict)
        assert "screen" in result["details"]
        assert isinstance(result["details"]["screen"], dict)
        assert "confidence" in result
        assert isinstance(result["confidence"], (int, float))
