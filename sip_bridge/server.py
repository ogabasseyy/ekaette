"""Async SIP server — handles INVITE/ACK/BYE signaling.

Adapted from sip-to-ai (Apache 2.0).
Minimal SIP/UDP implementation for single-line bridge.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .config import BridgeConfig
from .session import CallSession

logger = logging.getLogger(__name__)


@dataclass
class SIPServer:
    """Async UDP SIP server."""

    config: BridgeConfig
    _transport: asyncio.DatagramTransport | None = None
    _active_sessions: dict[str, CallSession] = field(default_factory=dict)

    async def start(self) -> None:
        """Bind UDP socket and start listening."""
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: SIPProtocol(self),
            local_addr=(self.config.sip_host, self.config.sip_port),
        )
        self._transport = transport
        logger.info(
            "SIP server listening",
            extra={
                "host": self.config.sip_host,
                "port": self.config.sip_port,
                "public_ip": self.config.sip_public_ip,
            },
        )

    async def stop(self) -> None:
        """Gracefully shut down all sessions and close transport."""
        for session in self._active_sessions.values():
            session.shutdown()
        if self._transport:
            self._transport.close()
        logger.info("SIP server stopped")

    def handle_invite(self, call_id: str, remote_addr: tuple[str, int]) -> CallSession:
        """Create a new call session for an INVITE."""
        session = CallSession(
            call_id=call_id,
            tenant_id=self.config.tenant_id,
            company_id=self.config.company_id,
        )
        self._active_sessions[call_id] = session
        logger.info("SIP INVITE", extra={"call_id": call_id, "remote": remote_addr})
        return session

    def handle_bye(self, call_id: str) -> None:
        """End a call session on BYE."""
        session = self._active_sessions.pop(call_id, None)
        if session:
            session.shutdown()
            logger.info("SIP BYE", extra={"call_id": call_id})


class SIPProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler for SIP messages."""

    def __init__(self, server: SIPServer) -> None:
        self.server = server

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Parse incoming SIP message and dispatch."""
        try:
            message = data.decode("utf-8", errors="replace")
            first_line = message.split("\r\n", 1)[0]

            if first_line.startswith("INVITE"):
                call_id = self._extract_call_id(message)
                if call_id:
                    session = self.server.handle_invite(call_id, addr)
                    asyncio.create_task(session.run())
            elif first_line.startswith("BYE"):
                call_id = self._extract_call_id(message)
                if call_id:
                    self.server.handle_bye(call_id)
            # ACK, OPTIONS, etc. are acknowledged but not processed
        except Exception:
            logger.exception("SIP message parse error")

    @staticmethod
    def _extract_call_id(message: str) -> str | None:
        """Extract Call-ID header from SIP message."""
        for line in message.split("\r\n"):
            if line.lower().startswith("call-id:"):
                return line.split(":", 1)[1].strip()
        return None
