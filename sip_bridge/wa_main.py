"""WhatsApp SIP bridge entry point.

Usage: python -m sip_bridge.wa_main
Starts a TLS SIP server for WhatsApp Business Calling (Opus/SRTP).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import socket
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

_NONCE_RE = re.compile(r'nonce="([^"]+)"')

from shared.phone_identity import canonical_phone_user_id, mask_phone

from .gateway_client import GatewayClient
from .sip_auth import verify_digest
from .sip_tls import SipMessage, parse_message, serialize_message
from .wa_config import WhatsAppBridgeConfig
from .wa_server_helpers import (
    build_transaction_response,
    handle_health_request,
    handle_sip_connection,
    resolve_advertised_ip,
)
from .wa_sip_client import (
    build_200_ok,
    build_407_response,
    generate_sdp_answer,
    parse_remote_sdp,
    resolve_call_id,
)

logger = logging.getLogger(__name__)


def _extract_caller_phone(from_header: str) -> str:
    """Extract caller address from SIP From header."""
    match = re.search(r"sip:([^@;>]+)", from_header or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


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
    _session_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
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
        """Handle INVITE: challenge or authenticate then create session."""
        call_id = resolve_call_id(invite.headers) or invite.headers.get("call-id", "")

        # Concurrency limit
        if len(self.active_sessions) >= self.max_concurrent_calls:
            return build_transaction_response(
                invite,
                status_code=503,
                reason="Service Unavailable",
                call_id=call_id,
                add_local_to_tag=True,
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
                    return build_transaction_response(
                        invite,
                        status_code=403,
                        reason="Forbidden",
                        call_id=call_id,
                        add_local_to_tag=True,
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
            return build_transaction_response(
                invite,
                status_code=403,
                reason="Forbidden",
                call_id=call_id,
                add_local_to_tag=True,
            )

        # Auth passed — parse remote SDP and create session
        self._pending_challenges.pop(call_id, None)
        remote_sdp = parse_remote_sdp(invite.body) if invite.body else {}

        bind_ip = self.config.sip_host
        local_ip = resolve_advertised_ip(bind_ip, logger=logger)

        # Validate remote media endpoint before allocating resources
        media_ip = remote_sdp.get("media_ip", "")
        media_port = remote_sdp.get("media_port", 0)
        if not media_ip or not media_port:
            return build_transaction_response(
                invite,
                status_code=488,
                reason="Not Acceptable Here",
                call_id=call_id,
                add_local_to_tag=True,
            )

        # Bind a local UDP socket for media — OS assigns a free port
        media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        media_sock.setblocking(False)
        media_sock.bind((bind_ip, 0))

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
            caller_phone = _extract_caller_phone(invite.headers.get("from", ""))

            # Build gateway client if gateway mode enabled
            gateway_client = None
            if getattr(self.config, "gateway_mode", False) and getattr(self.config, "gateway_ws_url", ""):
                if not getattr(self.config, "gateway_ws_secret", ""):
                    raise ValueError("WA_GATEWAY_WS_SECRET is required when WA_GATEWAY_MODE is enabled")
                user_id = canonical_phone_user_id(
                    self.config.tenant_id, self.config.company_id, caller_phone,
                    default_region=self.config.default_phone_region,
                )
                if user_id is None:
                    anon_seed = f"{self.config.tenant_id}:{self.config.company_id}:call:{call_id}"
                    user_id = f"wa-anon-{hashlib.sha256(anon_seed.encode()).hexdigest()[:16]}"
                    if caller_phone:
                        logger.warning(
                            "Phone normalization failed for WA caller: %s",
                            mask_phone(caller_phone),
                        )
                    else:
                        logger.warning("No caller phone in WA SIP From header, using anonymous user_id")
                session_id = f"wa-{uuid.uuid4().hex[:24]}"
                gateway_client = GatewayClient(
                    gateway_ws_url=self.config.gateway_ws_url,
                    user_id=user_id,
                    session_id=session_id,
                    tenant_id=self.config.tenant_id,
                    company_id=self.config.company_id,
                    industry="",  # omit — session_init resolves from registry
                    caller_phone=caller_phone,
                    ws_secret=getattr(self.config, "gateway_ws_secret", ""),
                )

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
                _caller_phone=caller_phone,
                _bridge_config=self.config,
                _owns_transport=True,
                gateway_client=gateway_client,
            )
            self.active_sessions[call_id] = session
            task = asyncio.create_task(session.run(), name=f"wa_session_{call_id}")
            self._session_tasks[call_id] = task

            def _on_done(done_task: asyncio.Task[None]) -> None:
                self._session_tasks.pop(call_id, None)
                self.active_sessions.pop(call_id, None)
                if done_task.cancelled():
                    return
                exc = done_task.exception()
                if exc is not None:
                    logger.error(
                        "WA session task failed",
                        exc_info=exc,
                        extra={"call_id": call_id},
                    )

            task.add_done_callback(_on_done)

            logger.info("WA INVITE accepted", extra={"call_id": call_id})
            return resp
        except Exception:
            # Close the socket to prevent leaks on SDP/SRTP errors
            media_sock.close()
            logger.exception("INVITE processing failed", extra={"call_id": call_id})
            return build_transaction_response(
                invite,
                status_code=488,
                reason="Not Acceptable Here",
                call_id=call_id,
                add_local_to_tag=True,
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
    main_task = loop.create_task(_run(config))

    def _shutdown(sig: int, _frame: object) -> None:
        logger.info("Received signal %s, shutting down", signal.Signals(sig).name)
        loop.call_soon_threadsafe(main_task.cancel)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
