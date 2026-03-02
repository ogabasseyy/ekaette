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
PCMA_PAYLOAD_TYPE = 8
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
    marker: bool = False

    @classmethod
    def parse(cls, data: bytes) -> RTPPacket | None:
        """Parse an RTP packet from raw UDP bytes.

        Accounts for CSRC list (CC field), header extension, and padding
        per RFC 3550 Section 5.1.
        """
        if len(data) < RTP_HEADER_SIZE:
            return None
        byte0, byte1, seq, ts, ssrc = struct.unpack("!BBHII", data[:RTP_HEADER_SIZE])
        version = (byte0 >> 6) & 0x03
        if version != 2:
            return None

        padding = bool(byte0 & 0x20)
        extension = bool(byte0 & 0x10)
        cc = byte0 & 0x0F
        pt = byte1 & 0x7F
        marker = bool(byte1 & 0x80)

        # Payload starts after fixed header + CSRC list (4 bytes each)
        offset = RTP_HEADER_SIZE + cc * 4
        if len(data) < offset:
            return None

        # Skip header extension if present (RFC 3550 Section 5.3.1)
        if extension:
            if len(data) < offset + 4:
                return None
            # Extension header: 2-byte profile + 2-byte length (in 32-bit words)
            ext_length_words = struct.unpack("!HH", data[offset:offset + 4])[1]
            offset += 4 + ext_length_words * 4
            if len(data) < offset:
                return None

        payload = data[offset:]

        # Remove padding bytes if padding bit is set (last byte = pad count)
        if padding and len(payload) > 0:
            pad_count = payload[-1]
            if pad_count > 0 and pad_count <= len(payload):
                payload = payload[:-pad_count]

        return cls(
            version=version,
            payload_type=pt,
            sequence=seq,
            timestamp=ts,
            ssrc=ssrc,
            marker=marker,
            payload=payload,
        )

    def serialize(self) -> bytes:
        """Serialize RTP packet to bytes."""
        byte0 = 0x80  # V=2
        byte1 = self.payload_type & 0x7F
        if self.marker:
            byte1 |= 0x80
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

        # If we fell significantly behind (e.g. waiting for network/Gemini chunks),
        # do not burst-send hundreds of frames to "catch up". Reset the timer baseline.
        if now - deadline > 0.1:  # 100ms gap
            self._start = now
            self._frame_count = 0
            deadline = self.next_deadline()

        if deadline > now:
            await asyncio.sleep(deadline - now)
