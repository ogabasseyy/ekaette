"""TDD tests for WhatsApp fail-closed config validation."""

from __future__ import annotations

import pytest
from unittest.mock import patch


class TestWhatsAppConfigValidation:
    """Fail-closed validation when WHATSAPP_ENABLED=true."""

    def test_missing_app_secret_raises(self) -> None:
        from app.api.v1.at.settings import _validate_whatsapp_config
        with (
            patch("app.api.v1.at.settings.WHATSAPP_ENABLED", True),
            patch("app.api.v1.at.settings.WHATSAPP_ACCESS_TOKEN", "token"),
            patch("app.api.v1.at.settings.WHATSAPP_PHONE_NUMBER_ID", "123"),
            patch("app.api.v1.at.settings.WHATSAPP_APP_SECRET", ""),
            patch("app.api.v1.at.settings.WHATSAPP_VERIFY_TOKEN", "vt"),
            patch("app.api.v1.at.settings.WA_SERVICE_SECRET", "ss"),
            patch("app.api.v1.at.settings.WA_CLOUD_TASKS_AUDIENCE", "aud"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_NAME", "tmpl"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_LANGUAGE", "en_US"),
            patch("app.api.v1.at.settings.WA_REPLAY_BUCKET", "bucket"),
            pytest.raises(RuntimeError, match="WHATSAPP_APP_SECRET"),
        ):
            _validate_whatsapp_config()

    def test_missing_service_secret_raises(self) -> None:
        from app.api.v1.at.settings import _validate_whatsapp_config
        with (
            patch("app.api.v1.at.settings.WHATSAPP_ENABLED", True),
            patch("app.api.v1.at.settings.WHATSAPP_ACCESS_TOKEN", "token"),
            patch("app.api.v1.at.settings.WHATSAPP_PHONE_NUMBER_ID", "123"),
            patch("app.api.v1.at.settings.WHATSAPP_APP_SECRET", "secret"),
            patch("app.api.v1.at.settings.WHATSAPP_VERIFY_TOKEN", "vt"),
            patch("app.api.v1.at.settings.WA_SERVICE_SECRET", ""),
            patch("app.api.v1.at.settings.WA_CLOUD_TASKS_AUDIENCE", "aud"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_NAME", "tmpl"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_LANGUAGE", "en_US"),
            patch("app.api.v1.at.settings.WA_REPLAY_BUCKET", "bucket"),
            pytest.raises(RuntimeError, match="WA_SERVICE_SECRET"),
        ):
            _validate_whatsapp_config()

    def test_missing_replay_bucket_raises(self) -> None:
        from app.api.v1.at.settings import _validate_whatsapp_config
        with (
            patch("app.api.v1.at.settings.WHATSAPP_ENABLED", True),
            patch("app.api.v1.at.settings.WHATSAPP_ACCESS_TOKEN", "token"),
            patch("app.api.v1.at.settings.WHATSAPP_PHONE_NUMBER_ID", "123"),
            patch("app.api.v1.at.settings.WHATSAPP_APP_SECRET", "secret"),
            patch("app.api.v1.at.settings.WHATSAPP_VERIFY_TOKEN", "vt"),
            patch("app.api.v1.at.settings.WA_SERVICE_SECRET", "ss"),
            patch("app.api.v1.at.settings.WA_CLOUD_TASKS_AUDIENCE", "aud"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_NAME", "tmpl"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_LANGUAGE", "en_US"),
            patch("app.api.v1.at.settings.WA_REPLAY_BUCKET", ""),
            pytest.raises(RuntimeError, match="WA_REPLAY_BUCKET"),
        ):
            _validate_whatsapp_config()

    def test_valid_config_passes(self) -> None:
        from app.api.v1.at.settings import _validate_whatsapp_config
        with (
            patch("app.api.v1.at.settings.WHATSAPP_ENABLED", True),
            patch("app.api.v1.at.settings.WHATSAPP_ACCESS_TOKEN", "token"),
            patch("app.api.v1.at.settings.WHATSAPP_PHONE_NUMBER_ID", "123"),
            patch("app.api.v1.at.settings.WHATSAPP_APP_SECRET", "secret"),
            patch("app.api.v1.at.settings.WHATSAPP_VERIFY_TOKEN", "vt"),
            patch("app.api.v1.at.settings.WA_SERVICE_SECRET", "ss"),
            patch("app.api.v1.at.settings.WA_CLOUD_TASKS_AUDIENCE", "aud"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_NAME", "tmpl"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_LANGUAGE", "en_US"),
            patch("app.api.v1.at.settings.WA_REPLAY_BUCKET", "bucket"),
        ):
            _validate_whatsapp_config()  # Should not raise

    def test_disabled_skips_validation(self) -> None:
        from app.api.v1.at.settings import _validate_whatsapp_config
        with patch("app.api.v1.at.settings.WHATSAPP_ENABLED", False):
            _validate_whatsapp_config()  # Should not raise even with empty config
