"""Async SIP server — handles INVITE/ACK/BYE signaling.

Adapted from sip-to-ai (Apache 2.0).
Minimal SIP/UDP implementation for single-line bridge.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field

from shared.callback_prewarm import (
    clear_callback_prewarm,
    list_callback_prewarms,
    update_callback_prewarm_status,
)
from shared.outbound_callback_hints import consume_outbound_callback_hint
from shared.phone_identity import canonical_phone_user_id, normalize_phone

from .codec_bridge import G711CodecBridge
from .config import BridgeConfig
from .gateway_client import GatewayClient
from .session import CallSession
from .sip_dialog import (
    build_sip_bye_request,
    build_sdp_answer,
    build_sip_response,
    ensure_dialog_to_header,
    extract_sip_uri,
    parse_sdp_g711,
    parse_sip_request,
)
from .sip_register import SIPRegistrar

# Same regex pattern used in wa_main.py:41 for caller phone extraction
_PHONE_RE = re.compile(r"[\+]?[\d]{7,15}")

logger = logging.getLogger(__name__)

# RTP port range for allocated media ports
_RTP_PORT_MIN = 10000
_RTP_PORT_MAX = 20000


def _read_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def _read_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


_PREANSWER_READY_TIMEOUT_SEC = max(
    0.0,
    _read_float_env("SIP_PREANSWER_READY_TIMEOUT_SEC", 6.0),
)
_CALLBACK_PREWARM_POLL_SEC = max(
    0.1,
    _read_float_env("AT_CALLBACK_PREWARM_POLL_SECONDS", 0.25),
)
_CALLBACK_PREWARM_READY_TIMEOUT_SEC = max(
    1.0,
    _read_float_env("AT_CALLBACK_PREWARM_TIMEOUT_SECONDS", 12.0),
)
_CALLBACK_POST_ANSWER_GRACE_SEC = max(
    0.0,
    _read_float_env("SIP_OUTBOUND_CALLBACK_POST_ANSWER_GRACE_MS", 1000.0) / 1000.0,
)
_SIP_T1_SEC = max(0.1, _read_float_env("SIP_T1_SECONDS", 0.5))
_BYE_MAX_ATTEMPTS = max(1, _read_int_env("SIP_BYE_MAX_ATTEMPTS", 4))
_CALLBACK_CONNECT_GREETING_TEXT = (
    "[The customer requested a callback and has just answered. "
    "Start speaking immediately, introduce yourself as ehkaitay, "
    "say you are calling them back, and continue naturally.]"
)
# Note: this seed is forwarded through the gateway as a normal text turn, so it
# must stay neutral. Instruction-like phrasing causes the router to treat the
# opening seed as customer intent and transfer before the caller speaks.
_INBOUND_CONNECT_GREETING_TEXT = "[Phone call connected]"


@dataclass
class PrewarmedCallbackSession:
    """Warm callback session waiting for the AT SIP leg to attach."""

    key: str
    tenant_id: str
    company_id: str
    phone: str
    session: CallSession
    task: asyncio.Task
    expires_at: float
    attached: bool = False


@dataclass(slots=True)
class ActiveSIPDialog:
    """Minimal in-dialog SIP state for originating a BYE."""

    remote_addr: tuple[str, int]
    request_uri: str
    local_from_header: str
    remote_to_header: str
    call_id: str
    next_local_cseq: int
    contact_uri: str


@dataclass(slots=True)
class PendingByeTransaction:
    """Track outbound BYE until the far side acknowledges it."""

    cseq: int
    reason: str
    remote_addr: tuple[str, int]
    request_bytes: bytes
    attempt_count: int = 1
    retry_handle: asyncio.TimerHandle | None = None


@dataclass
class SIPServer:
    """Async UDP SIP server."""

    config: BridgeConfig
    _transport: asyncio.DatagramTransport | None = None
    _active_sessions: dict[str, CallSession] = field(default_factory=dict)
    _dialogs: dict[str, ActiveSIPDialog] = field(default_factory=dict)
    _pending_byes: dict[str, PendingByeTransaction] = field(default_factory=dict)
    _prewarmed_callbacks: dict[str, PrewarmedCallbackSession] = field(default_factory=dict)
    _registrar: SIPRegistrar | None = None
    _register_task: asyncio.Task | None = None
    _callback_prewarm_task: asyncio.Task | None = None
    _stopping: bool = False

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
        if self.config.gateway_mode and self.config.gateway_ws_url:
            self._callback_prewarm_task = asyncio.create_task(self._callback_prewarm_loop())
            logger.info("Callback prewarm watcher started")

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
        self._stopping = True
        if self._register_task:
            self._register_task.cancel()
        if self._callback_prewarm_task:
            self._callback_prewarm_task.cancel()
        if self._registrar and self._registrar.registered:
            self._registrar.send_unregister()
        for pending in self._pending_byes.values():
            if pending.retry_handle is not None:
                pending.retry_handle.cancel()
        for session in self._active_sessions.values():
            session.shutdown()
        for record in self._prewarmed_callbacks.values():
            record.session.shutdown()
        if self._transport:
            self._transport.close()
        logger.info("SIP server stopped")

    @staticmethod
    def _extract_caller_phone(sip_from_header: str) -> str:
        """Extract caller phone from a SIP From header."""
        if not sip_from_header:
            return ""
        match = _PHONE_RE.search(sip_from_header)
        return match.group(0) if match else ""

    def _callback_reservation_key(self, *, tenant_id: str, company_id: str, phone: str) -> str:
        normalized_phone = normalize_phone(phone) or phone.strip()
        return f"{tenant_id}:{company_id}:{normalized_phone}"

    def _build_gateway_client(
        self,
        *,
        call_id: str,
        caller_phone: str,
        session_id_override: str = "",
    ) -> GatewayClient | None:
        """Build a gateway client for a SIP call leg when gateway mode is enabled."""
        if not (self.config.gateway_mode and self.config.gateway_ws_url):
            return None
        if not self.config.gateway_ws_secret:
            logger.error("Gateway mode enabled without GATEWAY_WS_SECRET")
            raise ValueError("GATEWAY_WS_SECRET is required when GATEWAY_MODE is enabled")

        user_id = canonical_phone_user_id(
            self.config.tenant_id,
            self.config.company_id,
            caller_phone,
            default_region=self.config.default_phone_region,
        )
        if user_id is None:
            anon_seed = f"{self.config.tenant_id}:{self.config.company_id}:call:{call_id}"
            user_id = f"sip-anon-{hashlib.sha256(anon_seed.encode()).hexdigest()[:16]}"
            if caller_phone:
                logger.warning(
                    "Phone normalization failed for SIP caller, using anonymous user_id",
                    extra={"call_id": call_id},
                )
            else:
                logger.warning(
                    "No caller phone in SIP From header, using anonymous user_id",
                    extra={"call_id": call_id},
                )

        session_seed = session_id_override or f"{self.config.tenant_id}:{self.config.company_id}:session:{call_id}"
        session_id = (
            session_id_override
            or f"sip-{hashlib.sha256(session_seed.encode()).hexdigest()[:24]}"
        )
        return GatewayClient(
            gateway_ws_url=self.config.gateway_ws_url,
            user_id=user_id,
            session_id=session_id,
            tenant_id=self.config.tenant_id,
            company_id=self.config.company_id,
            industry="",  # omit — session_init resolves from registry
            caller_phone=caller_phone,
            ws_secret=self.config.gateway_ws_secret,
        )

    async def _callback_prewarm_loop(self) -> None:
        """Poll callback reservations and keep warm sessions ready on the VM."""
        while not self._stopping:
            try:
                await self._sync_callback_prewarms()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Callback prewarm watcher failed")
            await asyncio.sleep(_CALLBACK_PREWARM_POLL_SEC)

    async def _sync_callback_prewarms(self) -> None:
        """Start or prune prewarmed callback sessions based on reservation state."""
        now = time.time()
        expired_keys: list[str] = []
        for key, record in list(self._prewarmed_callbacks.items()):
            if record.expires_at <= now and not record.attached:
                record.session.shutdown()
                expired_keys.append(key)
        for key in expired_keys:
            record = self._prewarmed_callbacks.pop(key, None)
            if record is None:
                continue
            await asyncio.to_thread(
                clear_callback_prewarm,
                tenant_id=record.tenant_id,
                company_id=record.company_id,
                phone=record.phone,
            )

        reservations = await asyncio.to_thread(list_callback_prewarms)
        for payload in reservations:
            tenant_id = str(payload.get("tenant_id", "")).strip()
            company_id = str(payload.get("company_id", "")).strip()
            phone = str(payload.get("phone", "")).strip()
            if not (tenant_id and company_id and phone):
                continue
            key = str(payload.get("key", "")).strip() or self._callback_reservation_key(
                tenant_id=tenant_id,
                company_id=company_id,
                phone=phone,
            )
            if key in self._prewarmed_callbacks:
                continue
            status = str(payload.get("status", "")).strip().lower()
            if status in {"attached", "failed", "consumed"}:
                continue
            expires_at = float(payload.get("expires_at", 0.0) or 0.0)
            if expires_at and expires_at <= now:
                await asyncio.to_thread(
                    clear_callback_prewarm,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    phone=phone,
                )
                continue
            self._start_callback_prewarm(
                key=key,
                tenant_id=tenant_id,
                company_id=company_id,
                phone=phone,
                expires_at=expires_at or (now + 30.0),
            )

    def _start_callback_prewarm(
        self,
        *,
        key: str,
        tenant_id: str,
        company_id: str,
        phone: str,
        expires_at: float,
    ) -> None:
        """Warm a callback session on the VM before the outbound INVITE arrives."""
        local_rtp_port = random.randint(_RTP_PORT_MIN, _RTP_PORT_MAX)
        prewarm_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        session = CallSession(
            call_id=f"callback-prewarm-{prewarm_id[:12]}",
            tenant_id=tenant_id,
            company_id=company_id,
            codec_bridge=G711CodecBridge(),
            remote_rtp_addr=None,
            local_rtp_port=local_rtp_port,
            _caller_phone=phone,
            gemini_api_key=self.config.gemini_api_key,
            gemini_model_id=self.config.live_model_id,
            gemini_system_instruction=self.config.system_instruction,
            gemini_voice=self.config.gemini_voice,
            gateway_client=self._build_gateway_client(
                call_id=f"callback-prewarm:{prewarm_id}",
                caller_phone=phone,
                session_id_override=f"sip-callback-{prewarm_id}",
            ),
            delay_answer_until_ready=True,
            callback_post_answer_grace_sec=_CALLBACK_POST_ANSWER_GRACE_SEC,
            connect_greeting_text=_CALLBACK_CONNECT_GREETING_TEXT,
        )
        task = asyncio.create_task(session.run())
        record = PrewarmedCallbackSession(
            key=key,
            tenant_id=tenant_id,
            company_id=company_id,
            phone=phone,
            session=session,
            task=task,
            expires_at=expires_at,
        )
        self._prewarmed_callbacks[key] = record
        asyncio.create_task(
            asyncio.to_thread(
                update_callback_prewarm_status,
                tenant_id=tenant_id,
                company_id=company_id,
                phone=phone,
                status="warming",
                detail="VM prewarming callback session",
            )
        )
        asyncio.create_task(self._await_callback_prewarm_ready(record))

        def _on_done(done_task: asyncio.Task) -> None:
            asyncio.create_task(self._handle_prewarm_session_done(record, done_task))

        task.add_done_callback(_on_done)
        logger.info(
            "Callback prewarm started phone=%s local_rtp_port=%d",
            phone,
            local_rtp_port,
        )

    async def _await_callback_prewarm_ready(self, record: PrewarmedCallbackSession) -> None:
        """Mark a prewarm reservation ready once outbound Ekaette audio is buffered."""
        timeout = max(
            0.5,
            min(record.expires_at - time.time(), _CALLBACK_PREWARM_READY_TIMEOUT_SEC),
        )
        logger.info(
            "Waiting for callback prewarm readiness phone=%s timeout=%.2fs",
            record.phone,
            timeout,
        )
        ready = await record.session.wait_until_answer_ready(timeout)
        if record.attached or self._stopping:
            return
        if ready:
            await asyncio.to_thread(
                update_callback_prewarm_status,
                tenant_id=record.tenant_id,
                company_id=record.company_id,
                phone=record.phone,
                status="ready",
                detail="Warm callback session ready on VM",
            )
            logger.info("Callback prewarm ready phone=%s", record.phone)
            return
        if record.session.startup_failed:
            detail = "Callback prewarm startup failed"
        else:
            detail = "Callback prewarm timed out"
        logger.warning(
            "Callback prewarm failed phone=%s detail=%s first_audio_ready=%s startup_failed=%s",
            record.phone,
            detail,
            record.session._first_outbound_audio_ready.is_set(),
            record.session.startup_failed,
        )
        await asyncio.to_thread(
            update_callback_prewarm_status,
            tenant_id=record.tenant_id,
            company_id=record.company_id,
            phone=record.phone,
            status="failed",
            detail=detail,
        )
        record.session.shutdown()

    async def _handle_prewarm_session_done(
        self,
        record: PrewarmedCallbackSession,
        done_task: asyncio.Task,
    ) -> None:
        """Clean up prewarm records when a warm session exits."""
        self._prewarmed_callbacks.pop(record.key, None)
        active_ids = [
            call_id
            for call_id, session in self._active_sessions.items()
            if session is record.session
        ]
        for call_id in active_ids:
            self._active_sessions.pop(call_id, None)

        if self._stopping or record.attached:
            return

        try:
            exc = None if done_task.cancelled() else done_task.exception()
        except Exception as callback_exc:  # pragma: no cover - defensive
            exc = callback_exc

        detail = "Callback prewarm session ended"
        if exc is not None:
            detail = str(exc)[:240] or detail

        await asyncio.to_thread(
            update_callback_prewarm_status,
            tenant_id=record.tenant_id,
            company_id=record.company_id,
            phone=record.phone,
            status="failed",
            detail=detail,
        )

    def claim_prewarmed_callback_session(
        self,
        *,
        call_id: str,
        sip_from_header: str,
        remote_rtp_addr: tuple[str, int] | None,
    ) -> CallSession | None:
        """Attach an outbound callback INVITE to an already-warm session."""
        caller_phone = self._extract_caller_phone(sip_from_header)
        normalized_phone = normalize_phone(caller_phone) or caller_phone.strip()
        if not normalized_phone:
            return None
        key = self._callback_reservation_key(
            tenant_id=self.config.tenant_id,
            company_id=self.config.company_id,
            phone=normalized_phone,
        )
        record = self._prewarmed_callbacks.get(key)
        if record is None:
            return None

        record.attached = True
        session = record.session
        session.call_id = call_id
        session.remote_rtp_addr = remote_rtp_addr
        self._active_sessions[call_id] = session
        asyncio.create_task(
            asyncio.to_thread(
                clear_callback_prewarm,
                tenant_id=record.tenant_id,
                company_id=record.company_id,
                phone=record.phone,
            )
        )
        logger.info(
            "Attached prewarmed callback session call_id=%s phone=%s local_rtp_port=%d",
            call_id,
            normalized_phone,
            session.local_rtp_port,
        )
        return session

    def handle_invite(
        self,
        call_id: str,
        remote_addr: tuple[str, int],
        remote_rtp_addr: tuple[str, int] | None = None,
        local_rtp_port: int = 0,
        sip_from_header: str = "",
    ) -> CallSession:
        """Create a new call session for an INVITE."""
        caller_phone = self._extract_caller_phone(sip_from_header)
        gateway_client = self._build_gateway_client(
            call_id=call_id,
            caller_phone=caller_phone,
        )

        outbound_callback_leg = False
        if gateway_client is not None and caller_phone:
            outbound_callback_leg = consume_outbound_callback_hint(
                tenant_id=self.config.tenant_id,
                company_id=self.config.company_id,
                phone=caller_phone,
            )

        session = CallSession(
            call_id=call_id,
            tenant_id=self.config.tenant_id,
            company_id=self.config.company_id,
            codec_bridge=G711CodecBridge(),
            remote_rtp_addr=remote_rtp_addr,
            local_rtp_port=local_rtp_port,
            _caller_phone=caller_phone,
            gemini_api_key=self.config.gemini_api_key,
            gemini_model_id=self.config.live_model_id,
            gemini_system_instruction=self.config.system_instruction,
            gemini_voice=self.config.gemini_voice,
            gateway_client=gateway_client,
            connect_greeting_text=_INBOUND_CONNECT_GREETING_TEXT,
            delay_answer_until_ready=(gateway_client is not None and not outbound_callback_leg),
        )
        self._active_sessions[call_id] = session
        logger.info(
            "SIP INVITE",
            extra={
                "call_id": call_id,
                "remote": remote_addr,
                "remote_rtp": remote_rtp_addr,
                "local_rtp_port": local_rtp_port,
                "gateway_mode": gateway_client is not None,
            },
        )
        return session

    def handle_bye(self, call_id: str) -> None:
        """End a call session on BYE."""
        self._dialogs.pop(call_id, None)
        self._clear_pending_bye(call_id)
        session = self._active_sessions.pop(call_id, None)
        if session:
            session.shutdown()
            logger.info("SIP BYE", extra={"call_id": call_id})

    def _clear_pending_bye(self, call_id: str) -> PendingByeTransaction | None:
        pending = self._pending_byes.pop(call_id, None)
        if pending is not None and pending.retry_handle is not None:
            pending.retry_handle.cancel()
        return pending

    def _schedule_bye_retry(self, call_id: str) -> None:
        pending = self._pending_byes.get(call_id)
        if pending is None:
            return
        if pending.attempt_count >= _BYE_MAX_ATTEMPTS:
            self._clear_pending_bye(call_id)
            logger.warning(
                "SIP BYE timed out call_id=%s reason=%s remote=%s attempts=%d",
                call_id,
                pending.reason,
                pending.remote_addr,
                pending.attempt_count,
            )
            return
        if pending.retry_handle is not None:
            pending.retry_handle.cancel()
        delay = _SIP_T1_SEC * (2 ** (pending.attempt_count - 1))
        loop = asyncio.get_running_loop()
        pending.retry_handle = loop.call_later(delay, self._retry_pending_bye, call_id)

    def _retry_pending_bye(self, call_id: str) -> None:
        pending = self._pending_byes.get(call_id)
        if pending is None:
            return
        transport = self._transport
        if transport is None:
            self._clear_pending_bye(call_id)
            return
        transport.sendto(pending.request_bytes, pending.remote_addr)
        pending.attempt_count += 1
        logger.info(
            "SIP BYE retransmit call_id=%s reason=%s remote=%s attempt=%d",
            call_id,
            pending.reason,
            pending.remote_addr,
            pending.attempt_count,
        )
        self._schedule_bye_retry(call_id)

    def request_hangup(self, call_id: str, *, reason: str = "normal") -> None:
        """Originate SIP BYE for an active dialog and stop the local session."""
        dialog = self._dialogs.pop(call_id, None)
        session = self._active_sessions.pop(call_id, None)
        if session is not None:
            session.shutdown()
        if dialog is None:
            logger.warning(
                "SIP hangup requested without active dialog call_id=%s reason=%s",
                call_id,
                reason,
            )
            return
        transport = self._transport
        if transport is None:
            logger.warning(
                "SIP hangup requested without transport call_id=%s reason=%s",
                call_id,
                reason,
            )
            return
        bye_request = build_sip_bye_request(
            request_uri=dialog.request_uri,
            local_from_header=dialog.local_from_header,
            remote_to_header=dialog.remote_to_header,
            call_id=dialog.call_id,
            cseq=dialog.next_local_cseq,
            contact_uri=dialog.contact_uri,
            via_host=self.config.sip_public_ip,
            via_port=self.config.sip_port,
        )
        request_bytes = bye_request.encode("utf-8")
        transport.sendto(request_bytes, dialog.remote_addr)
        self._pending_byes[call_id] = PendingByeTransaction(
            cseq=dialog.next_local_cseq,
            reason=reason,
            remote_addr=dialog.remote_addr,
            request_bytes=request_bytes,
        )
        self._schedule_bye_retry(call_id)
        logger.info(
            "SIP BYE sent call_id=%s reason=%s remote=%s",
            call_id,
            reason,
            dialog.remote_addr,
        )


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
                response_headers = self._extract_response_headers(message)
                call_id = response_headers.get("Call-ID", "")
                cseq_header = response_headers.get("CSeq", "")
                cseq_parts = cseq_header.split()
                if call_id and len(cseq_parts) >= 2 and cseq_parts[1].upper() == "BYE":
                    pending = self.server._pending_byes.get(call_id)
                    try:
                        status_code = int(first_line.split()[1])
                    except (IndexError, ValueError):
                        status_code = 0
                    if pending is not None:
                        if status_code >= 200:
                            pending = self.server._clear_pending_bye(call_id) or pending
                        log_fn = logger.info if 200 <= status_code < 300 else logger.warning
                        log_fn(
                            "SIP BYE acknowledged call_id=%s status=%s reason=%s remote=%s",
                            call_id,
                            status_code,
                            pending.reason,
                            pending.remote_addr,
                        )
                return

            if first_line.startswith("INVITE"):
                asyncio.create_task(self._handle_invite(message, addr))
            elif first_line.startswith("BYE"):
                parsed = parse_sip_request(message)
                headers = self._coerce_dialog_headers(parsed["headers"])
                call_id = headers.get("Call-ID") or self._extract_call_id(message)
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
                if call_id:
                    try:
                        self.server.handle_bye(call_id)
                    except Exception:
                        logger.exception("SIP BYE teardown failed", extra={"call_id": call_id})
            # ACK, OPTIONS, etc. are acknowledged but not processed
        except Exception:
            logger.exception("SIP message parse error")

    async def _handle_invite(self, message: str, addr: tuple[str, int]) -> None:
        """Parse INVITE, send 100 Trying + 200 OK, create session."""
        parsed = parse_sip_request(message)
        headers = self._coerce_dialog_headers(parsed["headers"])
        sdp_body = parsed["body"]
        call_id = headers.get("Call-ID", "")
        if not call_id:
            return
        dialog_to_header = ensure_dialog_to_header(headers.get("To", ""))
        dialog_headers = dict(headers)
        dialog_headers["To"] = dialog_to_header

        # Skip re-INVITEs for existing calls
        if call_id in self.server._active_sessions:
            return

        config = self.server.config
        transport = self.server._transport
        if not transport:
            return

        contact_uri = self._contact_uri()

        # 1. Send 100 Trying immediately
        trying = build_sip_response(
            100,
            "Trying",
            dialog_headers,
            sdp_body=None,
            contact_uri=contact_uri,
        )
        transport.sendto(trying.encode("utf-8"), addr)
        logger.info("100 Trying sent", extra={"call_id": call_id})

        # 2. Parse remote SDP for media address
        remote_rtp_addr: tuple[str, int] | None = None
        negotiated_payload_type = 0
        negotiated_codec_name = "PCMU"
        if sdp_body:
            sdp_info = parse_sdp_g711(sdp_body)
            if sdp_info["media_ip"] and sdp_info["media_port"]:
                remote_rtp_addr = (sdp_info["media_ip"], sdp_info["media_port"])
            negotiated_payload_type = int(sdp_info.get("audio_payload_type", 0) or 0)
            negotiated_codec_name = str(sdp_info.get("audio_codec", "PCMU") or "PCMU")

        session = self.server.claim_prewarmed_callback_session(
            call_id=call_id,
            sip_from_header=headers.get("From", ""),
            remote_rtp_addr=remote_rtp_addr,
        )
        if session is None:
            # 3. Allocate local RTP port
            local_rtp_port = random.randint(_RTP_PORT_MIN, _RTP_PORT_MAX)

            # 4. Create session and start the audio pipeline before answering so we can
            # keep the caller ringing until the model has audio ready.
            session = self.server.handle_invite(
                call_id, addr,
                remote_rtp_addr=remote_rtp_addr,
                local_rtp_port=local_rtp_port,
                sip_from_header=headers.get("From", ""),
            )
            session.codec_bridge = G711CodecBridge(
                rtp_payload_type=negotiated_payload_type,
                rtp_clock_rate=8000,
                law="alaw" if negotiated_codec_name.upper() == "PCMA" else "ulaw",
            )
            task = asyncio.create_task(session.run())

            def _on_done(done_task: asyncio.Task) -> None:
                self.server._active_sessions.pop(call_id, None)
                self.server._dialogs.pop(call_id, None)
                if not done_task.cancelled():
                    exc = done_task.exception()
                    if exc is not None:
                        logger.error("Call session task failed", exc_info=exc, extra={"call_id": call_id})

            task.add_done_callback(_on_done)
        else:
            local_rtp_port = session.local_rtp_port
            session.codec_bridge = G711CodecBridge(
                rtp_payload_type=negotiated_payload_type,
                rtp_clock_rate=8000,
                law="alaw" if negotiated_codec_name.upper() == "PCMA" else "ulaw",
            )

        session.request_hangup = (
            lambda reason="normal", _session=session: self.server.request_hangup(
                _session.call_id,
                reason=reason,
            )
        )

        if session.delay_answer_until_ready:
            ready = await session.wait_until_answer_ready(_PREANSWER_READY_TIMEOUT_SEC)
            if not ready and session.startup_failed:
                response = build_sip_response(
                    503,
                    "Service Unavailable",
                    dialog_headers,
                    sdp_body=None,
                    contact_uri=contact_uri,
                )
                transport.sendto(response.encode("utf-8"), addr)
                logger.warning(
                    "Rejecting INVITE after startup failure before answer",
                    extra={"call_id": call_id},
                )
                return
            if not ready:
                logger.warning(
                    "Proceeding to answer before audio readiness timeout=%.2fs",
                    _PREANSWER_READY_TIMEOUT_SEC,
                    extra={"call_id": call_id},
                )

        # 5. Build and send 200 OK with SDP answer
        local_sdp = build_sdp_answer(
            config.sip_public_ip,
            local_rtp_port,
            payload_type=negotiated_payload_type,
            codec_name=negotiated_codec_name,
        )
        ok_response = build_sip_response(
            200,
            "OK",
            dialog_headers,
            sdp_body=local_sdp,
            contact_uri=contact_uri,
        )
        transport.sendto(ok_response.encode("utf-8"), addr)
        remote_contact = parsed["headers"].get("Contact") or parsed["headers"].get("contact", "")
        request_uri = (
            extract_sip_uri(remote_contact)
            or extract_sip_uri(headers.get("From", ""))
            or str(parsed.get("request_uri", "")).strip()
        )
        try:
            invite_cseq_number = int(headers.get("CSeq", "0 INVITE").split()[0])
        except (AttributeError, IndexError, TypeError, ValueError):
            invite_cseq_number = 1
        self.server._dialogs[call_id] = ActiveSIPDialog(
            remote_addr=addr,
            request_uri=request_uri,
            # For a UAS-originated BYE: our To becomes the local From, and the
            # caller's original From becomes the remote To.
            local_from_header=dialog_to_header,
            remote_to_header=headers.get("From", ""),
            call_id=call_id,
            next_local_cseq=invite_cseq_number + 1,
            contact_uri=contact_uri,
        )
        session.mark_answered()
        logger.info(
            "200 OK sent",
            extra={
                "call_id": call_id,
                "local_rtp_port": local_rtp_port,
                "remote_rtp": remote_rtp_addr,
                "preanswer_timeout_sec": _PREANSWER_READY_TIMEOUT_SEC,
            },
        )

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

    @staticmethod
    def _extract_response_headers(message: str) -> dict[str, str]:
        """Extract SIP response headers using simple wire parsing."""
        headers: dict[str, str] = {}
        for line in message.split("\r\n")[1:]:
            if not line:
                break
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip()] = value.strip()
        return headers
