"""RTP packet handling — G.711 μ-law, 20ms frame timing.

Adapted from sip-to-ai (Apache 2.0).
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass


# RTP header: V=2, P=0, X=0, CC=0, M=0, PT=0 (PCMU), SSRC=random
RTP_HEADER_SIZE = 12
PCMU_PAYLOAD_TYPE = 0
FRAME_DURATION_MS = 20
SAMPLES_PER_FRAME = 160  # 8kHz * 20ms


@dataclass(slots=True)
class RTPPacket:
    """Parsed RTP packet."""

    version: int
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes

    @classmethod
    def parse(cls, data: bytes) -> RTPPacket | None:
        """Parse an RTP packet from raw UDP bytes."""
        if len(data) < RTP_HEADER_SIZE:
            return None
        byte0, byte1, seq, ts, ssrc = struct.unpack("!BBHII", data[:RTP_HEADER_SIZE])
        version = (byte0 >> 6) & 0x03
        if version != 2:
            return None
        pt = byte1 & 0x7F
        return cls(
            version=version,
            payload_type=pt,
            sequence=seq,
            timestamp=ts,
            ssrc=ssrc,
            payload=data[RTP_HEADER_SIZE:],
        )

    def serialize(self) -> bytes:
        """Serialize RTP packet to bytes."""
        byte0 = 0x80  # V=2
        byte1 = self.payload_type & 0x7F
        header = struct.pack(
            "!BBHII",
            byte0, byte1,
            self.sequence & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc,
        )
        return header + self.payload


class RTPTimer:
    """20ms frame timing with drift correction."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._frame_count = 0

    def next_deadline(self) -> float:
        """Return the monotonic time for the next frame deadline."""
        self._frame_count += 1
        return self._start + (self._frame_count * FRAME_DURATION_MS / 1000.0)

    async def wait_next_frame(self) -> None:
        """Sleep until the next 20ms frame boundary."""
        import asyncio
        deadline = self.next_deadline()
        now = time.monotonic()
        if deadline > now:
            await asyncio.sleep(deadline - now)
