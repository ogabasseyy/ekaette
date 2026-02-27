"""Per-call AI lifecycle with structured concurrency.

Manages the three concurrent tasks per call:
- RTP inbound (phone → audio queue)
- Gemini bidi (audio queue → Gemini Live → response queue)
- RTP outbound (response queue → phone)

Uses asyncio.TaskGroup for clean teardown.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Bounded queue sizes (backpressure)
INBOUND_QUEUE_SIZE = 50   # ~1s of 20ms frames
OUTBOUND_QUEUE_SIZE = 50


@dataclass(slots=True)
class CallSession:
    """Per-call session managing audio ↔ Gemini Live bridge."""

    call_id: str
    tenant_id: str
    company_id: str
    inbound_queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=INBOUND_QUEUE_SIZE))
    outbound_queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=OUTBOUND_QUEUE_SIZE))
    started_at: float = field(default_factory=time.time)
    _shutdown: asyncio.Event = field(default_factory=asyncio.Event)

    # Metrics
    frames_received: int = 0
    frames_sent: int = 0
    inbound_drops: int = 0
    outbound_drops: int = 0

    async def run(self) -> None:
        """Run the three concurrent tasks. Cancels all on first failure."""
        logger.info(
            "Call session started",
            extra={
                "call_id": self.call_id,
                "tenant_id": self.tenant_id,
                "company_id": self.company_id,
            },
        )
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._rtp_inbound_loop())
                tg.create_task(self._gemini_bidi_loop())
                tg.create_task(self._rtp_outbound_loop())
        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error("Call session task failed", exc_info=exc)
        finally:
            duration = time.time() - self.started_at
            logger.info(
                "Call session ended",
                extra={
                    "call_id": self.call_id,
                    "duration_seconds": round(duration, 2),
                    "frames_received": self.frames_received,
                    "frames_sent": self.frames_sent,
                    "inbound_drops": self.inbound_drops,
                    "outbound_drops": self.outbound_drops,
                },
            )

    def shutdown(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown.set()

    async def feed_inbound(self, frame: bytes) -> None:
        """Feed an RTP audio frame from the phone side."""
        try:
            self.inbound_queue.put_nowait(frame)
            self.frames_received += 1
        except asyncio.QueueFull:
            self.inbound_drops += 1

    async def _rtp_inbound_loop(self) -> None:
        """Read inbound audio frames and forward to Gemini."""
        while not self._shutdown.is_set():
            try:
                frame = await asyncio.wait_for(
                    self.inbound_queue.get(), timeout=1.0
                )
                # TODO: Convert frame and send to Gemini Live session
                _ = frame
            except TimeoutError:
                continue

    async def _gemini_bidi_loop(self) -> None:
        """Bidirectional Gemini Live session (placeholder)."""
        # TODO: Establish Gemini Live WebSocket connection
        # TODO: Send audio from inbound, receive audio to outbound
        while not self._shutdown.is_set():
            await asyncio.sleep(0.02)  # 20ms frame timing placeholder

    async def _rtp_outbound_loop(self) -> None:
        """Read AI response audio and send as RTP to phone."""
        while not self._shutdown.is_set():
            try:
                frame = await asyncio.wait_for(
                    self.outbound_queue.get(), timeout=1.0
                )
                self.frames_sent += 1
                # TODO: Send frame via RTP to phone
                _ = frame
            except TimeoutError:
                continue
