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
            patch("app.api.v1.at.settings.WA_TASKS_INVOKER_EMAIL", "invoker@example.com"),
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
            patch("app.api.v1.at.settings.WA_TASKS_INVOKER_EMAIL", "invoker@example.com"),
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
            patch("app.api.v1.at.settings.WA_TASKS_INVOKER_EMAIL", "invoker@example.com"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_NAME", "tmpl"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_LANGUAGE", "en_US"),
            patch("app.api.v1.at.settings.WA_REPLAY_BUCKET", ""),
            pytest.raises(RuntimeError, match="WA_REPLAY_BUCKET"),
        ):
            _validate_whatsapp_config()

    def test_missing_tasks_invoker_email_raises(self) -> None:
        from app.api.v1.at.settings import _validate_whatsapp_config
        with (
            patch("app.api.v1.at.settings.WHATSAPP_ENABLED", True),
            patch("app.api.v1.at.settings.WHATSAPP_ACCESS_TOKEN", "token"),
            patch("app.api.v1.at.settings.WHATSAPP_PHONE_NUMBER_ID", "123"),
            patch("app.api.v1.at.settings.WHATSAPP_APP_SECRET", "secret"),
            patch("app.api.v1.at.settings.WHATSAPP_VERIFY_TOKEN", "vt"),
            patch("app.api.v1.at.settings.WA_SERVICE_SECRET", "ss"),
            patch("app.api.v1.at.settings.WA_CLOUD_TASKS_AUDIENCE", "aud"),
            patch("app.api.v1.at.settings.WA_TASKS_INVOKER_EMAIL", ""),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_NAME", "tmpl"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_LANGUAGE", "en_US"),
            patch("app.api.v1.at.settings.WA_REPLAY_BUCKET", "bucket"),
            pytest.raises(RuntimeError, match="WA_TASKS_INVOKER_EMAIL"),
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
            patch("app.api.v1.at.settings.WA_TASKS_INVOKER_EMAIL", "invoker@example.com"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_NAME", "tmpl"),
            patch("app.api.v1.at.settings.WA_UTILITY_TEMPLATE_LANGUAGE", "en_US"),
            patch("app.api.v1.at.settings.WA_REPLAY_BUCKET", "bucket"),
        ):
            _validate_whatsapp_config()  # Should not raise

    def test_disabled_skips_validation(self) -> None:
        from app.api.v1.at.settings import _validate_whatsapp_config
        with patch("app.api.v1.at.settings.WHATSAPP_ENABLED", False):
            _validate_whatsapp_config()  # Should not raise even with empty config

    def test_retry_max_attempts_must_be_positive(self) -> None:
        from pydantic import ValidationError
        from app.api.v1.at.settings import ATSettings

        with pytest.raises(ValidationError):
            ATSettings(
                WA_GRAPH_RETRY_MAX_ATTEMPTS=0,
                WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS=8,
            )

    def test_retry_max_backoff_seconds_must_be_positive(self) -> None:
        from pydantic import ValidationError
        from app.api.v1.at.settings import ATSettings

        with pytest.raises(ValidationError):
            ATSettings(
                WA_GRAPH_RETRY_MAX_ATTEMPTS=3,
                WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS=0,
            )
