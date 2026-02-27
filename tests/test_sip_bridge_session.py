"""TDD tests for SIP bridge call session lifecycle.

Covers: session creation, bounded queues, backpressure metrics,
shutdown signal, TaskGroup structured concurrency.
"""

from __future__ import annotations

import asyncio
import pytest
from sip_bridge.session import CallSession, INBOUND_QUEUE_SIZE, OUTBOUND_QUEUE_SIZE


class TestCallSessionCreation:
    """Session dataclass fields and defaults."""

    def test_session_has_call_id(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert s.call_id == "call-1"

    def test_session_has_bounded_queues(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert s.inbound_queue.maxsize == INBOUND_QUEUE_SIZE
        assert s.outbound_queue.maxsize == OUTBOUND_QUEUE_SIZE

    def test_session_initial_metrics_zero(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert s.frames_received == 0
        assert s.frames_sent == 0
        assert s.inbound_drops == 0
        assert s.outbound_drops == 0


class TestInboundFeeding:
    """feed_inbound() and backpressure metrics."""

    async def test_feed_inbound_increments_counter(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        await s.feed_inbound(b"\x00" * 160)
        assert s.frames_received == 1

    async def test_feed_inbound_drops_when_full(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        # Fill the queue
        for _ in range(INBOUND_QUEUE_SIZE):
            await s.feed_inbound(b"\x00" * 160)
        # This should be dropped
        await s.feed_inbound(b"\x00" * 160)
        assert s.inbound_drops == 1
        assert s.frames_received == INBOUND_QUEUE_SIZE


class TestShutdown:
    """Graceful shutdown signal."""

    def test_shutdown_sets_event(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")
        assert not s._shutdown.is_set()
        s.shutdown()
        assert s._shutdown.is_set()

    async def test_run_completes_after_shutdown(self) -> None:
        """Session.run() exits cleanly when shutdown is signaled."""
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")

        async def signal_shutdown():
            await asyncio.sleep(0.05)
            s.shutdown()

        # Run session with a concurrent shutdown signal
        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(signal_shutdown())

        # Should reach here without hanging
        assert s._shutdown.is_set()


class TestOutboundQueue:
    """Outbound queue and frame counting."""

    async def test_outbound_frame_counting(self) -> None:
        s = CallSession(call_id="call-1", tenant_id="public", company_id="acme")

        # Put frames in outbound queue
        for _ in range(3):
            await s.outbound_queue.put(b"\x00" * 160)

        # Simulate session reading from outbound (quick shutdown)
        async def run_briefly():
            await asyncio.sleep(0.1)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(run_briefly())

        assert s.frames_sent == 3
