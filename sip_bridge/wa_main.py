"""WhatsApp SIP bridge entry point.

Usage: python -m sip_bridge.wa_main
Starts a TLS SIP server for WhatsApp Business Calling (Opus/SRTP).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import sys
from dataclasses import dataclass, field
from typing import Any

from .sip_tls import SipMessage, parse_message, serialize_message
from .wa_config import WhatsAppBridgeConfig
from .wa_server_helpers import (
    build_transaction_response,
    handle_health_request,
    handle_sip_connection,
)
from .wa_sip_client import resolve_call_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WaSIPServer — TLS SIP server for WhatsApp Business Calling
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_CALLS = 10
_WA_RTP_PORT_MIN = 10000
_WA_RTP_PORT_MAX = 20000


def _initial_media_port() -> int:
    """Pick a randomized initial RTP port within the allowed range."""
    port_count = _WA_RTP_PORT_MAX - _WA_RTP_PORT_MIN + 1
    if port_count <= 1:
        return _WA_RTP_PORT_MIN
    offset = int.from_bytes(os.urandom(2), "big") % port_count
    return _WA_RTP_PORT_MIN + offset


@dataclass
class WaSIPServer:
    """Async TLS SIP server for WhatsApp inbound calls."""

    config: Any
    max_concurrent_calls: int = _MAX_CONCURRENT_CALLS
    active_sessions: dict[str, Any] = field(default_factory=dict)
    _session_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _pending_challenges: dict[str, dict[str, str]] = field(default_factory=dict)
    _tcp_server: asyncio.Server | None = None
    _health_server: asyncio.Server | None = None
    _next_media_port: int = field(default_factory=_initial_media_port)

    def _bind_media_socket(self, bind_ip: str) -> socket.socket:
        """Bind RTP inside the firewall-open UDP range without reusing the same port."""
        last_error: OSError | None = None
        port_count = _WA_RTP_PORT_MAX - _WA_RTP_PORT_MIN + 1
        if port_count <= 0:
            raise OSError("Invalid WA RTP port range configuration")

        start_port = min(max(self._next_media_port, _WA_RTP_PORT_MIN), _WA_RTP_PORT_MAX)
        for offset in range(port_count):
            port = _WA_RTP_PORT_MIN + ((start_port - _WA_RTP_PORT_MIN + offset) % port_count)
            media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            media_sock.setblocking(False)
            try:
                media_sock.bind((bind_ip, port))
                self._next_media_port = _WA_RTP_PORT_MIN + (
                    ((port - _WA_RTP_PORT_MIN + 1) % port_count)
                )
                return media_sock
            except OSError as exc:
                media_sock.close()
                last_error = exc
        raise OSError(
            f"No free WA RTP UDP ports available in range {_WA_RTP_PORT_MIN}-{_WA_RTP_PORT_MAX}"
        ) from last_error

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
        # Pre-warm heavy imports to avoid multi-second latency on first call.
        # wa_session imports google.genai which is ~2s on first load.
        try:
            from . import wa_session as _wa_session_mod  # noqa: F811
            logger.debug("wa_session module pre-warmed")
        except Exception:
            logger.debug("wa_session pre-warm failed (non-fatal)", exc_info=True)
        try:
            from shared.phone_identity import normalize_phone
            normalize_phone("+2348001234567", default_region="NG")
        except Exception:
            logger.debug("phonenumbers pre-warm failed (non-fatal)", exc_info=True)

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
        tasks = list(self._session_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._session_tasks.clear()
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
        await handle_health_request(
            reader,
            writer,
            active_sessions=len(self.active_sessions),
            max_concurrent_calls=self.max_concurrent_calls,
            logger=logger,
        )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single TLS connection (may carry multiple SIP messages)."""
        await handle_sip_connection(
            reader,
            writer,
            parse_message=parse_message,
            serialize_message=serialize_message,
            dispatch=self.handle_sip_message,
            logger=logger,
        )

    async def handle_sip_message(
        self,
        msg: SipMessage,
        peer: tuple[str, int],
    ) -> SipMessage | None:
        """Dispatch a parsed SIP message and return a response."""
        call_id = resolve_call_id(msg.headers) or msg.headers.get("call-id", "")
        if msg.method in {"INVITE", "ACK", "BYE"}:
            logger.info(
                "WA SIP %s from %s call_id=%s has_auth=%s",
                msg.method,
                peer[0],
                call_id,
                bool(msg.headers.get("proxy-authorization", "")),
                extra={"call_id": call_id},
            )
        # IP allowlist check
        if not self._check_ip_allowed(peer[0]):
            logger.warning("Blocked IP %s", peer[0])
            return build_transaction_response(
                msg,
                status_code=403,
                reason="Forbidden",
                add_local_to_tag=(msg.method == "INVITE"),
            )

        method = msg.method
        if method == "INVITE":
            return await self._handle_invite(msg)
        elif method == "BYE":
            return self._handle_bye(msg)
        elif method == "ACK":
            logger.info("WA ACK", extra={"call_id": call_id})
            # Notify session that ACK arrived — maiden SRTP can now be sent
            session = self.active_sessions.get(call_id)
            if session is not None:
                session.notify_ack()
            else:
                # ACK call_id might use SIP Call-ID (outgoing:wacid.xxx)
                # instead of X-WA-Meta-WACID (wacid.xxx). Try fallback.
                sip_call_id = msg.headers.get("call-id", "").strip()
                for sid, sess in self.active_sessions.items():
                    if sip_call_id and sip_call_id.endswith(sid):
                        sess.notify_ack()
                        break
            return None  # ACK has no response
        else:
            return build_transaction_response(
                msg,
                status_code=405,
                reason="Method Not Allowed",
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
        """Handle INVITE: delegate to wa_invite_handler."""
        from .wa_invite_handler import handle_invite
        return await handle_invite(self, invite)

    def _handle_bye(self, bye: SipMessage) -> SipMessage:
        """Handle BYE: terminate active session."""
        call_id = resolve_call_id(bye.headers) or bye.headers.get("call-id", "")
        reason = bye.headers.get("reason", "")
        session = self.active_sessions.pop(call_id, None)
        if session:
            session.shutdown()
            logger.info("WA BYE reason=%s", reason or "-", extra={"call_id": call_id})
        task = self._session_tasks.pop(call_id, None)
        if task and not task.done():
            task.cancel()

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
        # Normal shutdown path when service receives termination signal.
        pass
    finally:
        await server.stop()
        logger.info("WhatsApp SIP bridge shut down")


def main() -> None:
    """Entry point for `python -m sip_bridge.wa_main`."""
    import signal

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = WhatsAppBridgeConfig.from_env()
    _check_production_guards(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(_run(config))

    def _shutdown(sig: int, _frame: object) -> None:
        logger.info("Received signal %s, shutting down", signal.Signals(sig).name)
        loop.call_soon_threadsafe(main_task.cancel)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        logger.info("Main task cancelled, exiting normally")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
