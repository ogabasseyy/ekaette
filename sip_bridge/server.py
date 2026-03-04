"""Async SIP server — handles INVITE/ACK/BYE signaling.

Adapted from sip-to-ai (Apache 2.0).
Minimal SIP/UDP implementation for single-line bridge.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from .codec_bridge import G711CodecBridge
from .config import BridgeConfig
from .session import CallSession
from .sip_dialog import (
    build_sdp_answer,
    build_sip_response,
    parse_sdp_g711,
    parse_sip_request,
)
from .sip_register import SIPRegistrar

logger = logging.getLogger(__name__)

# RTP port range for allocated media ports
_RTP_PORT_MIN = 10000
_RTP_PORT_MAX = 20000


@dataclass
class SIPServer:
    """Async UDP SIP server."""

    config: BridgeConfig
    _transport: asyncio.DatagramTransport | None = None
    _active_sessions: dict[str, CallSession] = field(default_factory=dict)
    _registrar: SIPRegistrar | None = None
    _register_task: asyncio.Task | None = None

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

        # Start SIP registration with AT registrar
        self._registrar = SIPRegistrar(config=self.config)
        if self._registrar.should_register:
            self._registrar._transport = self._transport
            self._registrar.send_register()
            self._register_task = asyncio.create_task(self._registration_loop())
            logger.info("SIP registration started")

    async def _registration_loop(self) -> None:
        """Periodically re-register to keep registration alive."""
        interval = self.config.sip_register_interval
        # Re-register at 80% of expiry to avoid gaps
        refresh = max(interval * 0.8, 30)
        while True:
            await asyncio.sleep(refresh)
            if self._registrar and self._registrar.should_register:
                logger.info("SIP re-registration")
                self._registrar.send_register()

    async def stop(self) -> None:
        """Gracefully shut down all sessions and close transport."""
        if self._register_task:
            self._register_task.cancel()
        if self._registrar and self._registrar.registered:
            self._registrar.send_unregister()
        for session in self._active_sessions.values():
            session.shutdown()
        if self._transport:
            self._transport.close()
        logger.info("SIP server stopped")

    def handle_invite(
        self,
        call_id: str,
        remote_addr: tuple[str, int],
        remote_rtp_addr: tuple[str, int] | None = None,
        local_rtp_port: int = 0,
    ) -> CallSession:
        """Create a new call session for an INVITE."""
        session = CallSession(
            call_id=call_id,
            tenant_id=self.config.tenant_id,
            company_id=self.config.company_id,
            codec_bridge=G711CodecBridge(),
            remote_rtp_addr=remote_rtp_addr,
            local_rtp_port=local_rtp_port,
            gemini_api_key=self.config.gemini_api_key,
            gemini_model_id=self.config.live_model_id,
            gemini_system_instruction=self.config.system_instruction,
            gemini_voice=self.config.gemini_voice,
        )
        self._active_sessions[call_id] = session
        logger.info(
            "SIP INVITE",
            extra={
                "call_id": call_id,
                "remote": remote_addr,
                "remote_rtp": remote_rtp_addr,
                "local_rtp_port": local_rtp_port,
            },
        )
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

            # SIP responses (e.g., "SIP/2.0 200 OK") → delegate to registrar
            if first_line.startswith("SIP/2.0"):
                if self.server._registrar:
                    self.server._registrar.handle_response(message)
                return

            if first_line.startswith("INVITE"):
                self._handle_invite(message, addr)
            elif first_line.startswith("BYE"):
                parsed = parse_sip_request(message)
                headers = self._coerce_dialog_headers(parsed["headers"])
                call_id = headers.get("Call-ID") or self._extract_call_id(message)
                if call_id:
                    self.server.handle_bye(call_id)
                transport = self.server._transport
                if transport is not None:
                    ok_response = build_sip_response(
                        200,
                        "OK",
                        headers,
                        sdp_body=None,
                        contact_uri=self._contact_uri(),
                    )
                    transport.sendto(ok_response.encode("utf-8"), addr)
                    logger.info("200 OK sent for BYE", extra={"call_id": call_id})
            # ACK, OPTIONS, etc. are acknowledged but not processed
        except Exception:
            logger.exception("SIP message parse error")

    def _handle_invite(self, message: str, addr: tuple[str, int]) -> None:
        """Parse INVITE, send 100 Trying + 200 OK, create session."""
        parsed = parse_sip_request(message)
        headers = self._coerce_dialog_headers(parsed["headers"])
        sdp_body = parsed["body"]
        call_id = headers.get("Call-ID", "")
        if not call_id:
            return

        # Skip re-INVITEs for existing calls
        if call_id in self.server._active_sessions:
            return

        config = self.server.config
        transport = self.server._transport
        if not transport:
            return

        contact_uri = self._contact_uri()

        # 1. Send 100 Trying immediately
        trying = build_sip_response(100, "Trying", headers, sdp_body=None, contact_uri=contact_uri)
        transport.sendto(trying.encode("utf-8"), addr)
        logger.info("100 Trying sent", extra={"call_id": call_id})

        # 2. Parse remote SDP for media address
        remote_rtp_addr: tuple[str, int] | None = None
        if sdp_body:
            sdp_info = parse_sdp_g711(sdp_body)
            if sdp_info["media_ip"] and sdp_info["media_port"]:
                remote_rtp_addr = (sdp_info["media_ip"], sdp_info["media_port"])

        # 3. Allocate local RTP port
        local_rtp_port = random.randint(_RTP_PORT_MIN, _RTP_PORT_MAX)

        # 4. Build and send 200 OK with SDP answer
        local_sdp = build_sdp_answer(config.sip_public_ip, local_rtp_port)
        ok_response = build_sip_response(200, "OK", headers, sdp_body=local_sdp, contact_uri=contact_uri)
        transport.sendto(ok_response.encode("utf-8"), addr)
        logger.info(
            "200 OK sent",
            extra={
                "call_id": call_id,
                "local_rtp_port": local_rtp_port,
                "remote_rtp": remote_rtp_addr,
            },
        )

        # 5. Create session and start audio pipeline
        session = self.server.handle_invite(
            call_id, addr,
            remote_rtp_addr=remote_rtp_addr,
            local_rtp_port=local_rtp_port,
        )
        asyncio.create_task(session.run())

    def _contact_uri(self) -> str:
        """Build local Contact URI for SIP responses."""
        config = self.server.config
        user_part = config.sip_username.split("@", 1)[0] if "@" in config.sip_username else config.sip_username
        return f"<sip:{user_part}@{config.sip_public_ip}:{config.sip_port}>"

    @staticmethod
    def _coerce_dialog_headers(headers: dict[str, str]) -> dict[str, str]:
        """Coerce transaction header names to canonical SIP casing."""
        return {
            "Via": headers.get("Via") or headers.get("via", ""),
            "From": headers.get("From") or headers.get("from", ""),
            "To": headers.get("To") or headers.get("to", ""),
            "Call-ID": headers.get("Call-ID") or headers.get("call-id", ""),
            "CSeq": headers.get("CSeq") or headers.get("cseq", ""),
        }

    @staticmethod
    def _extract_call_id(message: str) -> str | None:
        """Extract Call-ID header from SIP message."""
        for line in message.split("\r\n"):
            if line.lower().startswith("call-id:"):
                return line.split(":", 1)[1].strip()
        return None
