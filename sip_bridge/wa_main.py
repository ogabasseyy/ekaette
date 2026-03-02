"""WhatsApp SIP bridge entry point.

Usage: python -m sip_bridge.wa_main
Starts a TLS SIP server for WhatsApp Business Calling (Opus/SRTP).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import sys
from dataclasses import dataclass, field
from typing import Any

_NONCE_RE = re.compile(r'nonce="([^"]+)"')

from .sip_auth import verify_digest
from .sip_tls import SipMessage, parse_message, serialize_message
from .wa_config import WhatsAppBridgeConfig
from .wa_sip_client import (
    build_200_ok,
    build_407_response,
    generate_sdp_answer,
    parse_remote_sdp,
    resolve_call_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WaSIPServer — TLS SIP server for WhatsApp Business Calling
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_CALLS = 10


@dataclass
class WaSIPServer:
    """Async TLS SIP server for WhatsApp inbound calls."""

    config: Any
    max_concurrent_calls: int = _MAX_CONCURRENT_CALLS
    active_sessions: dict[str, Any] = field(default_factory=dict)
    _pending_challenges: dict[str, dict[str, str]] = field(default_factory=dict)
    _tcp_server: asyncio.Server | None = None
    _health_server: asyncio.Server | None = None

    async def start(self) -> None:
        """Start TLS server accepting SIP connections."""
        from .sip_tls import create_tls_context

        ssl_ctx = None
        if self.config.tls_certfile and self.config.tls_keyfile:
            ssl_ctx = create_tls_context(
                certfile=self.config.tls_certfile,
                keyfile=self.config.tls_keyfile,
                server_side=True,
            )
        elif not self.config.sandbox_mode:
            raise RuntimeError(
                "TLS certificate and key are required in production mode. "
                "Set WA_TLS_CERTFILE and WA_TLS_KEYFILE, or enable "
                "WA_SANDBOX_MODE=true for local development."
            )

        self._tcp_server = await asyncio.start_server(
            self._handle_connection,
            host=self.config.sip_host,
            port=self.config.sip_port,
            ssl=ssl_ctx,
        )
        # Start health/readiness HTTP server
        self._health_server = await asyncio.start_server(
            self._handle_health_request,
            host="0.0.0.0",
            port=self.config.health_port,
        )
        logger.info(
            "WA SIP server listening",
            extra={
                "host": self.config.sip_host,
                "port": self.config.sip_port,
                "health_port": self.config.health_port,
            },
        )

    async def stop(self) -> None:
        """Shut down all sessions and close server."""
        for session in list(self.active_sessions.values()):
            session.shutdown()
        self.active_sessions.clear()
        if self._health_server:
            self._health_server.close()
            await self._health_server.wait_closed()
        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
        logger.info("WA SIP server stopped")

    async def _handle_health_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle HTTP health/readiness check requests."""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            path = request_line.decode("utf-8", errors="replace").split(" ")[1] if b" " in request_line else "/"
            # Drain remaining headers
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break

            if path == "/healthz":
                body = json.dumps({"status": "ok"})
                status = 200
            elif path == "/readyz":
                active = len(self.active_sessions)
                at_capacity = active >= self.max_concurrent_calls
                status = 503 if at_capacity else 200
                body = json.dumps({
                    "status": "unavailable" if at_capacity else "ready",
                    "active_sessions": active,
                    "max_concurrent_calls": self.max_concurrent_calls,
                })
            else:
                body = json.dumps({"error": "not found"})
                status = 404

            response = (
                f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{body}"
            )
            writer.write(response.encode("utf-8"))
            await writer.drain()
        except Exception:
            pass  # Best-effort health response — connection may have been reset
        finally:
            writer.close()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single TLS connection (may carry multiple SIP messages)."""
        peer = writer.get_extra_info("peername", ("unknown", 0))
        try:
            while True:
                msg = await parse_message(reader)
                if msg is None:
                    break
                resp = await self.handle_sip_message(msg, peer)
                if resp is not None:
                    writer.write(serialize_message(resp))
                    await writer.drain()
        except Exception:
            logger.exception("Connection error from %s", peer)
        finally:
            writer.close()

    async def handle_sip_message(
        self,
        msg: SipMessage,
        peer: tuple[str, int],
    ) -> SipMessage | None:
        """Dispatch a parsed SIP message and return a response."""
        # IP allowlist check
        if not self._check_ip_allowed(peer[0]):
            logger.warning("Blocked IP %s", peer[0])
            return SipMessage(
                first_line="SIP/2.0 403 Forbidden",
                headers={
                    "call-id": msg.headers.get("call-id", ""),
                    "content-length": "0",
                },
                body="",
            )

        method = msg.method
        if method == "INVITE":
            return await self._handle_invite(msg)
        elif method == "BYE":
            return self._handle_bye(msg)
        elif method == "ACK":
            return None  # ACK has no response
        else:
            return SipMessage(
                first_line="SIP/2.0 405 Method Not Allowed",
                headers={
                    "call-id": msg.headers.get("call-id", ""),
                    "content-length": "0",
                },
                body="",
            )

    def _check_ip_allowed(self, ip: str) -> bool:
        """Check if source IP is in the allowlist."""
        cidrs = self.config.sip_allowed_cidrs
        if not cidrs:
            if self.config.sandbox_mode:
                return True
            return False
        try:
            addr = ipaddress.ip_address(ip)
            return any(
                addr in ipaddress.ip_network(cidr, strict=False)
                for cidr in cidrs
            )
        except ValueError:
            return False

    async def _handle_invite(self, invite: SipMessage) -> SipMessage:
        """Handle INVITE: challenge or authenticate then create session."""
        call_id = resolve_call_id(invite.headers) or invite.headers.get("call-id", "")

        # Concurrency limit
        if len(self.active_sessions) >= self.max_concurrent_calls:
            return SipMessage(
                first_line="SIP/2.0 503 Service Unavailable",
                headers={
                    "call-id": call_id,
                    "content-length": "0",
                },
                body="",
            )

        # Check for Proxy-Authorization header
        auth_value = invite.headers.get("proxy-authorization", "")
        if not auth_value:
            # Evict oldest challenges if at capacity (prevent unbounded growth)
            max_pending = self.max_concurrent_calls * 2
            while len(self._pending_challenges) >= max_pending:
                oldest = next(iter(self._pending_challenges))
                self._pending_challenges.pop(oldest)
            # No auth → send 407 challenge
            realm = f"{self.config.sip_host}"
            resp = build_407_response(invite, realm=realm)
            challenge_value = resp.headers.get("proxy-authenticate", "")
            nonce_match = _NONCE_RE.search(challenge_value)
            issued_nonce = nonce_match.group(1) if nonce_match else ""
            self._pending_challenges[call_id] = {
                "realm": realm,
                "nonce": issued_nonce,
            }
            return resp

        # Verify nonce was issued by us for this call
        pending = self._pending_challenges.get(call_id)
        if pending:
            from .sip_auth import parse_challenge as _parse_auth
            try:
                auth_params = _parse_auth(auth_value)
                if auth_params.get("nonce") != pending.get("nonce"):
                    logger.warning("Nonce mismatch for call %s", call_id)
                    self._pending_challenges.pop(call_id, None)
                    return SipMessage(
                        first_line="SIP/2.0 403 Forbidden",
                        headers={"call-id": call_id, "content-length": "0"},
                        body="",
                    )
            except Exception:
                pass  # parse failure handled by verify_digest below

        # Verify credentials
        if not verify_digest(
            auth_value=auth_value,
            expected_username=self.config.sip_username,
            expected_password=self.config.sip_password,
            method="INVITE",
        ):
            self._pending_challenges.pop(call_id, None)
            return SipMessage(
                first_line="SIP/2.0 403 Forbidden",
                headers={"call-id": call_id, "content-length": "0"},
                body="",
            )

        # Auth passed — parse remote SDP and create session
        self._pending_challenges.pop(call_id, None)
        remote_sdp = parse_remote_sdp(invite.body) if invite.body else {}

        local_ip = self.config.sip_host
        if local_ip == "0.0.0.0":
            local_ip = "127.0.0.1"

        # Validate remote media endpoint before allocating resources
        media_ip = remote_sdp.get("media_ip", "")
        media_port = remote_sdp.get("media_port", 0)
        if not media_ip or not media_port:
            return SipMessage(
                first_line="SIP/2.0 488 Not Acceptable Here",
                headers={
                    "call-id": call_id,
                    "content-length": "0",
                },
                body="",
            )

        # Bind a local UDP socket for media — OS assigns a free port
        media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        media_sock.setblocking(False)
        media_sock.bind((local_ip, 0))

        try:
            local_media_port = media_sock.getsockname()[1]

            # Wire media dependencies from SDP (may raise on bad crypto)
            codec_bridge = self._create_codec_bridge(remote_sdp)
            srtp_sender, srtp_receiver = self._create_srtp_contexts(invite.body or "")

            sdp_body = generate_sdp_answer(
                local_ip=local_ip,
                local_port=local_media_port,
                payload_type=remote_sdp.get("opus_payload_type", 111),
            )
            local_contact = f"<sip:ekaette@{local_ip}:{self.config.sip_port};transport=tls>"
            resp = build_200_ok(invite, sdp_body=sdp_body, local_contact=local_contact)

            remote_addr = (media_ip, media_port)

            # Create WaSession with full media pipeline (import here to avoid circular)
            from .wa_session import WaSession

            session = WaSession(
                call_id=call_id,
                tenant_id=self.config.tenant_id,
                company_id=self.config.company_id,
                codec_bridge=codec_bridge,
                srtp_sender=srtp_sender,
                srtp_receiver=srtp_receiver,
                media_transport=media_sock,
                remote_media_addr=remote_addr,
                gemini_api_key=self.config.gemini_api_key,
                gemini_model_id=self.config.live_model_id,
                gemini_system_instruction=self.config.system_instruction,
                gemini_voice=self.config.gemini_voice,
                _owns_transport=True,
            )
            self.active_sessions[call_id] = session
            asyncio.create_task(session.run())

            logger.info("WA INVITE accepted", extra={"call_id": call_id})
            return resp
        except Exception:
            # Close the socket to prevent leaks on SDP/SRTP errors
            media_sock.close()
            logger.exception("INVITE processing failed", extra={"call_id": call_id})
            return SipMessage(
                first_line="SIP/2.0 488 Not Acceptable Here",
                headers={
                    "call-id": call_id,
                    "content-length": "0",
                },
                body="",
            )

    @staticmethod
    def _create_codec_bridge(remote_sdp: dict) -> Any:
        """Create OpusCodecBridge from parsed remote SDP parameters."""
        from .codec_bridge import OpusCodecBridge

        return OpusCodecBridge(
            rtp_payload_type=remote_sdp.get("opus_payload_type", 111),
            rtp_clock_rate=48000,
            encode_rate=remote_sdp.get("encode_rate", 16000),
        )

    @staticmethod
    def _create_srtp_contexts(sdp_body: str) -> tuple[Any, Any]:
        """Create SRTP sender and receiver from SDP crypto attributes."""
        from .srtp_context import SRTPContext, generate_key_material, parse_sdes_crypto

        crypto = parse_sdes_crypto(sdp_body)
        if crypto is not None:
            remote_key = crypto["key"]
            local_key = generate_key_material()
            sender = SRTPContext(key_material=local_key, is_sender=True)
            receiver = SRTPContext(key_material=remote_key, is_sender=False)
            return sender, receiver
        return None, None

    def _handle_bye(self, bye: SipMessage) -> SipMessage:
        """Handle BYE: terminate active session."""
        call_id = resolve_call_id(bye.headers) or bye.headers.get("call-id", "")
        session = self.active_sessions.pop(call_id, None)
        if session:
            session.shutdown()
            logger.info("WA BYE", extra={"call_id": call_id})

        return SipMessage(
            first_line="SIP/2.0 200 OK",
            headers={
                "via": bye.headers.get("via", ""),
                "from": bye.headers.get("from", ""),
                "to": bye.headers.get("to", ""),
                "call-id": call_id,
                "cseq": bye.headers.get("cseq", ""),
                "content-length": "0",
            },
            body="",
        )


# ---------------------------------------------------------------------------
# Production guards
# ---------------------------------------------------------------------------


def _check_production_guards(config: WhatsAppBridgeConfig) -> None:
    """Refuse to start in unsafe configurations."""
    k_service = os.getenv("K_SERVICE", "")
    if config.sandbox_mode and k_service:
        logger.fatal(
            "WA_SANDBOX_MODE=true detected in Cloud Run (K_SERVICE=%s). "
            "Sandbox mode is local/VM-only. Refusing to start.",
            k_service,
        )
        sys.exit(1)

    errors = config.validate()
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        logger.fatal("Cannot start with invalid configuration. Exiting.")
        sys.exit(1)


async def _run(config: WhatsAppBridgeConfig) -> None:
    """Main async entry point — starts TLS SIP server."""
    server = WaSIPServer(config=config)
    await server.start()

    logger.info(
        "WhatsApp SIP bridge started",
        extra={
            "host": config.sip_host,
            "port": config.sip_port,
            "sandbox_mode": config.sandbox_mode,
            "tenant_id": config.tenant_id,
            "company_id": config.company_id,
        },
    )

    try:
        await asyncio.Event().wait()  # Run until cancelled
    except asyncio.CancelledError:
        pass  # Normal shutdown signal
    finally:
        await server.stop()
        logger.info("WhatsApp SIP bridge shut down")


def main() -> None:
    """Entry point for `python -m sip_bridge.wa_main`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = WhatsAppBridgeConfig.from_env()
    _check_production_guards(config)
    asyncio.run(_run(config))


if __name__ == "__main__":
    main()
