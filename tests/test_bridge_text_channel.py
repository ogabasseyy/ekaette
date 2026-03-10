"""TDD tests for bridge_text.py WhatsApp channel support."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from app.api.v1.at.bridge_text import _CHANNEL_CONFIG, query_text


@pytest.fixture(autouse=True)
def _mock_genai():
    """Mock the GenAI client so no real API calls are made."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Test AI response"
    mock_client.models.generate_content.return_value = mock_response
    with patch("app.api.v1.at.bridge_text._get_genai_client", return_value=mock_client):
        yield mock_client


class TestChannelConfig:
    """Channel configuration correctness."""

    def test_sms_config_exists(self) -> None:
        assert "sms" in _CHANNEL_CONFIG
        assert _CHANNEL_CONFIG["sms"]["max_chars"] == 160

    def test_whatsapp_config_exists(self) -> None:
        assert "whatsapp" in _CHANNEL_CONFIG
        assert _CHANNEL_CONFIG["whatsapp"]["max_chars"] == 4096

    def test_whatsapp_has_business_task_framing(self) -> None:
        suffix = _CHANNEL_CONFIG["whatsapp"]["system_suffix"]
        assert "business" in suffix.lower() or "task" in suffix.lower()


class TestQueryText:
    """query_text with channel parameter."""

    async def test_sms_backwards_compat(self, _mock_genai) -> None:
        result = await query_text(user_message="Hi")
        assert result == "Test AI response"

    async def test_sms_default_channel(self, _mock_genai) -> None:
        """Default channel should be sms."""
        await query_text(user_message="Hi")
        call_kwargs = _mock_genai.models.generate_content.call_args[1]
        config = call_kwargs["config"]
        assert config.system_instruction.endswith(_CHANNEL_CONFIG["sms"]["system_suffix"])
        assert "e hkaitay" not in config.system_instruction.lower()
        assert "named ehkaitay" in config.system_instruction.lower()
        assert "you are ekaette" not in config.system_instruction.lower()
        assert "ekaette-electronics" not in config.system_instruction.lower()

    async def test_whatsapp_channel(self, _mock_genai) -> None:
        await query_text(user_message="Hi", channel="whatsapp")
        call_kwargs = _mock_genai.models.generate_content.call_args[1]
        config = call_kwargs["config"]
        assert config.max_output_tokens == 1024

    async def test_sms_max_output_tokens(self, _mock_genai) -> None:
        await query_text(user_message="Hi", channel="sms")
        call_kwargs = _mock_genai.models.generate_content.call_args[1]
        assert call_kwargs["config"].max_output_tokens == 64

    @patch("app.api.v1.at.bridge_text.resolve_live_model_id", return_value="resolved-model")
    async def test_uses_resolved_live_model_by_default(self, _mock_resolve, _mock_genai) -> None:
        await query_text(user_message="Hi")
        call_kwargs = _mock_genai.models.generate_content.call_args[1]
        assert call_kwargs["model"] == "resolved-model"

    async def test_empty_response_fallback(self, _mock_genai) -> None:
        _mock_genai.models.generate_content.return_value.text = ""
        result = await query_text(user_message="Hi")
        assert "How can I help" in result
