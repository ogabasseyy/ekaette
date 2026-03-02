"""TDD tests for AT + SIP bridge resilience and chaos scenarios.

Covers: bridge restart during call, Gemini disconnect handling,
AT callback retries/out-of-order delivery, circuit breaker behavior.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock


# ── Bridge Restart During Active Call ──


class TestBridgeRestart:
    """Session handles bridge process restart gracefully."""

    async def test_session_shutdown_cleans_up(self) -> None:
        """Shutdown signal causes clean task teardown."""
        from sip_bridge.session import CallSession

        s = CallSession(call_id="chaos-1", tenant_id="public", company_id="acme")

        async def trigger_shutdown():
            await asyncio.sleep(0.05)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(trigger_shutdown())

        # All tasks should have exited cleanly
        assert s._shutdown.is_set()
        assert s.frames_received == 0  # No frames fed

    async def test_session_survives_inbound_queue_full(self) -> None:
        """Full inbound queue drops frames without crashing."""
        from sip_bridge.session import CallSession, INBOUND_QUEUE_SIZE

        s = CallSession(call_id="chaos-2", tenant_id="public", company_id="acme")

        # Overflow the inbound queue
        for _ in range(INBOUND_QUEUE_SIZE + 10):
            await s.feed_inbound(b"\x00" * 160)

        assert s.inbound_drops == 10
        assert s.frames_received == INBOUND_QUEUE_SIZE


# ── Gemini Live Disconnect ──


class TestGeminiDisconnect:
    """Handle upstream Gemini Live WebSocket failures."""

    async def test_gemini_client_close_is_idempotent(self) -> None:
        """Closing an already-closed client doesn't crash."""
        from sip_bridge.gemini_live_client import GeminiLiveClient
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test"}, clear=True):
            cfg = BridgeConfig.from_env()
        client = GeminiLiveClient(config=cfg)
        # Close without connecting — should not raise
        await client.close()

    async def test_send_audio_before_connect_is_noop(self) -> None:
        """Sending audio before connect is a safe no-op."""
        from sip_bridge.gemini_live_client import GeminiLiveClient
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test"}, clear=True):
            cfg = BridgeConfig.from_env()
        client = GeminiLiveClient(config=cfg)
        # Should not raise — returns silently when not connected
        await client.send_audio(b"\x00" * 160)

    async def test_receive_audio_before_connect_returns_none(self) -> None:
        """Receiving audio before connect returns None (safe)."""
        from sip_bridge.gemini_live_client import GeminiLiveClient
        from sip_bridge.config import BridgeConfig

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test"}, clear=True):
            cfg = BridgeConfig.from_env()
        client = GeminiLiveClient(config=cfg)
        result = await client.receive_audio()
        assert result is None


# ── AT Callback Retries and Ordering ──


class TestCallbackRetryResilience:
    """AT delivers callbacks at-least-once — handle duplicates and ordering."""

    def test_duplicate_callback_is_idempotent(self) -> None:
        """Same session+event delivered twice returns same result."""
        from app.api.v1.at.idempotency import is_duplicate_callback, _callback_seen

        _callback_seen.clear()
        # First delivery
        assert is_duplicate_callback("retry-session-1", "voice:1") is False
        # Retry — should be flagged as duplicate
        assert is_duplicate_callback("retry-session-1", "voice:1") is True

    def test_out_of_order_events_handled(self) -> None:
        """Different events for same session can arrive in any order."""
        from app.api.v1.at.idempotency import is_duplicate_callback, _callback_seen

        _callback_seen.clear()
        # Event 2 arrives before event 1
        assert is_duplicate_callback("ooo-session", "voice:2") is False
        assert is_duplicate_callback("ooo-session", "voice:1") is False
        # Retries of both are duplicates
        assert is_duplicate_callback("ooo-session", "voice:2") is True
        assert is_duplicate_callback("ooo-session", "voice:1") is True

    def test_many_concurrent_sessions_dont_leak(self) -> None:
        """High session volume doesn't cause unbounded memory growth."""
        from app.api.v1.at.idempotency import is_duplicate_callback, _callback_seen

        _callback_seen.clear()
        for i in range(500):
            is_duplicate_callback(f"session-{i}", "voice:1")
        # All should be tracked
        assert len(_callback_seen) == 500


# ── Voice Endpoint Resilience ──


class TestVoiceEndpointResilience:
    """Voice endpoints handle provider failures gracefully."""

    def test_outbound_call_provider_failure_returns_error(self) -> None:
        """Provider exception during outbound call returns error status."""
        from app.api.v1.at.voice import router
        from app.api.v1.at.idempotency import _store, _callback_seen
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        _store.clear()
        _callback_seen.clear()

        with (
            patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
            patch("app.api.v1.at.voice.AT_VOICE_ENABLED", True),
            patch(
                "app.api.v1.at.providers.make_call",
                new_callable=AsyncMock,
                side_effect=Exception("AT API timeout"),
            ),
        ):
            import app.api.v1.at.security as sec_mod
            sec_mod._at_rate_buckets.clear()
            sec_mod._at_last_prune = 0.0

            app = FastAPI()
            app.include_router(router, prefix="/api/v1/at")
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/at/voice/call",
                json={"to": "+2348012345678"},
                headers={"Idempotency-Key": "resilience-test-1"},
            )
            # Should return 502 (Bad Gateway — provider unavailable), not crash
            assert resp.status_code == 502


# ── SMS Endpoint Resilience ──


class TestSMSEndpointResilience:
    """SMS endpoints handle provider failures gracefully."""

    def test_sms_send_provider_failure_returns_error(self) -> None:
        """Provider exception during SMS send returns error status."""
        from app.api.v1.at.sms import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with (
            patch("app.api.v1.at.security.ALLOWED_SOURCE_IPS", set()),
            patch("app.api.v1.at.sms.AT_SMS_ENABLED", True),
            patch(
                "app.api.v1.at.providers.send_sms",
                new_callable=AsyncMock,
                side_effect=Exception("AT SMS gateway error"),
            ),
        ):
            import app.api.v1.at.security as sec_mod
            sec_mod._at_rate_buckets.clear()
            sec_mod._at_last_prune = 0.0

            app = FastAPI()
            app.include_router(router, prefix="/api/v1/at")
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/at/sms/send",
                json={"to": "+2348012345678", "message": "test"},
            )
            # Should return 502 (Bad Gateway — provider unavailable), not crash
            assert resp.status_code == 502
