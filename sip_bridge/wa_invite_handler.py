"""INVITE handling for WhatsApp SIP bridge.

Extracted from wa_main.py to keep file sizes within architecture caps.
Contains SIP INVITE authentication, SDP negotiation, and session creation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import socket
import uuid
from typing import TYPE_CHECKING, Any

from shared.phone_identity import canonical_phone_user_id

from .gateway_client import GatewayClient
from .sip_auth import verify_digest
from .sip_tls import SipMessage, serialize_message
from .wa_server_helpers import build_transaction_response, resolve_advertised_ip
from .wa_sip_client import (
    build_200_ok,
    build_407_response,
    generate_sdp_answer,
    parse_remote_sdp,
    resolve_call_id,
)

if TYPE_CHECKING:
    from .wa_main import WaSIPServer

logger = logging.getLogger(__name__)

_NONCE_RE = re.compile(r'nonce="([^"]+)"')


def extract_caller_phone(from_header: str) -> str:
    """Extract caller address from SIP From header."""
    match = re.search(r"sip:([^@;>]+)", from_header or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def send_maiden_srtp(
    sock: socket.socket,
    remote_addr: tuple[str, int],
    srtp_sender: Any,
    call_id: str,
    *,
    ssrc: int | None = None,
    sequence: int = 0,
    timestamp: int = 0,
) -> None:
    """Send the first SRTP packet to Meta's media endpoint.

    Meta requires the business to send the first media packet before it
    starts flowing RTP back.  A minimal Opus comfort-noise frame (silence)
    is used.  This mirrors the working March 4th behaviour.
    """
    from .rtp import RTPPacket

    if ssrc is None:
        ssrc = int.from_bytes(os.urandom(4), "big")
    # Opus payload: a single-byte silence frame (DTX/comfort noise)
    maiden_rtp = RTPPacket(
        version=2,
        payload_type=111,  # Opus
        sequence=sequence,
        timestamp=timestamp,
        ssrc=ssrc,
        payload=b"\xf8\xff\xfe",  # Opus silence (3-byte CBR comfort noise)
    ).serialize()
    try:
        maiden_srtp = srtp_sender.protect(maiden_rtp)
        sock.sendto(maiden_srtp, remote_addr)
        logger.info(
            "Maiden SRTP packet sent to %s (%d bytes, SSRC=%08x)",
            remote_addr, len(maiden_srtp), ssrc,
            extra={"call_id": call_id},
        )
    except Exception:
        logger.warning(
            "Failed to send maiden SRTP packet",
            exc_info=True,
            extra={"call_id": call_id},
        )


def extract_contact_host(invite: SipMessage, fallback_host: str) -> str:
    """Prefer the dialed SIP host for Contact so TLS peers see a hostname."""
    request_match = re.search(r"sips?:[^@]+@([^; >]+)", invite.first_line or "", re.IGNORECASE)
    to_match = re.search(r"sips?:[^@;>]+@([^;>]+)", invite.headers.get("to", ""), re.IGNORECASE)
    raw_host = (request_match or to_match).group(1).strip() if (request_match or to_match) else fallback_host
    if raw_host.startswith("[") and "]" in raw_host:
        return raw_host[1:].split("]", 1)[0]
    if raw_host.count(":") == 1:
        host, maybe_port = raw_host.rsplit(":", 1)
        if maybe_port.isdigit():
            return host
    return raw_host


def create_codec_bridge(remote_sdp: dict) -> Any:
    """Create OpusCodecBridge from parsed remote SDP parameters."""
    from .codec_bridge import OpusCodecBridge

    return OpusCodecBridge(
        rtp_payload_type=remote_sdp.get("opus_payload_type", 111),
        rtp_clock_rate=48000,
        encode_rate=remote_sdp.get("encode_rate", 16000),
        channels=remote_sdp.get("opus_channels", 2),
    )


def create_srtp_contexts(sdp_body: str) -> tuple[Any, Any, bytes | None]:
    """Create SRTP sender and receiver from SDP crypto attributes."""
    from .srtp_context import SRTPContext, generate_key_material, parse_sdes_crypto

    crypto = parse_sdes_crypto(sdp_body)
    if crypto is not None:
        remote_key = crypto["key"]
        local_key = generate_key_material()
        sender = SRTPContext(key_material=local_key, is_sender=True)
        receiver = SRTPContext(key_material=remote_key, is_sender=False)
        return sender, receiver, local_key
    return None, None, None


async def handle_invite(server: WaSIPServer, invite: SipMessage) -> SipMessage:
    """Handle INVITE: challenge or authenticate then create session."""
    call_id = resolve_call_id(invite.headers) or invite.headers.get("call-id", "")

    # Concurrency limit
    if len(server.active_sessions) >= server.max_concurrent_calls:
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
        return _challenge_invite(server, invite, call_id)

    # Verify nonce was issued by us for this call
    pending = server._pending_challenges.get(call_id)
    if pending:
        from .sip_auth import parse_challenge as _parse_auth
        try:
            auth_params = _parse_auth(auth_value)
            if auth_params.get("nonce") != pending.get("nonce"):
                logger.warning("Nonce mismatch for call %s", call_id)
                server._pending_challenges.pop(call_id, None)
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
        expected_username=server.config.sip_username,
        expected_password=server.config.sip_password,
        method="INVITE",
    ):
        logger.warning("WA digest auth failed", extra={"call_id": call_id})
        server._pending_challenges.pop(call_id, None)
        return build_transaction_response(
            invite,
            status_code=403,
            reason="Forbidden",
            call_id=call_id,
            add_local_to_tag=True,
        )

    # Auth passed -- create session
    server._pending_challenges.pop(call_id, None)
    return await _create_session(server, invite, call_id)


def _challenge_invite(server: WaSIPServer, invite: SipMessage, call_id: str) -> SipMessage:
    """Issue a 407 Proxy Authentication Required challenge."""
    # Evict oldest challenges if at capacity (prevent unbounded growth)
    max_pending = server.max_concurrent_calls * 2
    while len(server._pending_challenges) >= max_pending:
        oldest = next(iter(server._pending_challenges))
        server._pending_challenges.pop(oldest)
    # No auth -> send 407 challenge
    realm = extract_contact_host(
        invite,
        getattr(server.config, "sip_public_ip", "") or server.config.sip_host,
    )
    logger.info(
        "WA issuing 407 challenge realm=%s",
        realm,
        extra={"call_id": call_id},
    )
    resp = build_407_response(invite, realm=realm)
    # Debug: log full 407 response
    resp_407_bytes = serialize_message(resp)
    logger.debug(
        "WA 407 response (%d bytes):\n%s",
        len(resp_407_bytes),
        resp_407_bytes.decode("utf-8", errors="replace"),
        extra={"call_id": call_id},
    )
    challenge_value = resp.headers.get("proxy-authenticate", "")
    nonce_match = _NONCE_RE.search(challenge_value)
    issued_nonce = nonce_match.group(1) if nonce_match else ""
    server._pending_challenges[call_id] = {
        "realm": realm,
        "nonce": issued_nonce,
    }
    return resp


async def _create_session(
    server: WaSIPServer, invite: SipMessage, call_id: str
) -> SipMessage:
    """Parse SDP, set up media, and create a WaSession."""
    # Debug: log full authenticated INVITE headers
    invite_debug = serialize_message(invite)
    logger.debug(
        "WA authenticated INVITE (%d bytes):\n%s",
        len(invite_debug),
        invite_debug.decode("utf-8", errors="replace")[:2000],
        extra={"call_id": call_id},
    )
    remote_sdp = parse_remote_sdp(invite.body) if invite.body else {}
    # Log remote SDP for debugging media issues
    if invite.body:
        logger.debug(
            "WA remote SDP:\n%s",
            invite.body[:500],
            extra={"call_id": call_id},
        )
    logger.info(
        "WA parsed SDP: %s",
        {k: v for k, v in remote_sdp.items() if k != "raw"},
        extra={"call_id": call_id},
    )

    bind_ip = server.config.sip_host
    local_ip = resolve_advertised_ip(
        bind_ip,
        public_ip=getattr(server.config, "sip_public_ip", ""),
        logger=logger,
    )

    # Validate remote media endpoint before allocating resources
    media_ip = remote_sdp.get("media_ip", "")
    media_port = remote_sdp.get("media_port", 0)
    if not media_ip or not media_port:
        logger.warning("WA remote SDP missing media endpoint", extra={"call_id": call_id})
        return build_transaction_response(
            invite,
            status_code=488,
            reason="Not Acceptable Here",
            call_id=call_id,
            add_local_to_tag=True,
        )

    # Bind a local UDP socket for media
    media_sock = server._bind_media_socket(bind_ip)

    try:
        local_media_port = media_sock.getsockname()[1]

        # Wire media dependencies from SDP (may raise on bad crypto)
        codec_bridge = create_codec_bridge(remote_sdp)
        srtp_sender, srtp_receiver, local_srtp_key = create_srtp_contexts(invite.body or "")
        rtp_ssrc = int.from_bytes(os.urandom(4), "big")
        rtp_frame_duration_ms = getattr(codec_bridge, "frame_duration_ms", 20)
        if not isinstance(rtp_frame_duration_ms, int):
            rtp_frame_duration_ms = 20
        rtp_clock_rate = getattr(codec_bridge, "rtp_clock_rate", 48000)
        if not isinstance(rtp_clock_rate, int):
            rtp_clock_rate = 48000
        rtp_timestamp_step = rtp_clock_rate * rtp_frame_duration_ms // 1000

        sdp_body = generate_sdp_answer(
            local_ip=local_ip,
            local_port=local_media_port,
            payload_type=remote_sdp.get("opus_payload_type", 111),
            key_material=local_srtp_key,
            ssrc=rtp_ssrc,
        )
        contact_host = extract_contact_host(invite, local_ip)
        local_contact = f"<sip:ekaette@{contact_host}:{server.config.sip_port};transport=tls>"
        resp = build_200_ok(invite, sdp_body=sdp_body, local_contact=local_contact)
        # Debug: log the full 200 OK we're sending (headers + SDP)
        resp_bytes = serialize_message(resp)
        logger.debug(
            "WA 200 OK response (%d bytes):\n%s",
            len(resp_bytes),
            resp_bytes.decode("utf-8", errors="replace"),
            extra={"call_id": call_id},
        )

        remote_addr = (media_ip, media_port)

        caller_phone = extract_caller_phone(invite.headers.get("from", ""))

        # Build gateway client if gateway mode enabled
        gateway_client = _build_gateway_client(server, invite, call_id, caller_phone)

        # Create WaSession with full media pipeline (import here to avoid circular)
        from .wa_session import WaSession

        session = WaSession(
            call_id=call_id,
            tenant_id=server.config.tenant_id,
            company_id=server.config.company_id,
            codec_bridge=codec_bridge,
            srtp_sender=srtp_sender,
            srtp_receiver=srtp_receiver,
            media_transport=media_sock,
            remote_media_addr=remote_addr,
            gemini_api_key=server.config.gemini_api_key,
            gemini_model_id=server.config.live_model_id,
            gemini_system_instruction=server.config.system_instruction,
            gemini_voice=server.config.gemini_voice,
            _caller_phone=caller_phone,
            _bridge_config=server.config,
            _owns_transport=True,
            gateway_client=gateway_client,
            rtp_ssrc=rtp_ssrc,
            rtp_sequence=1,
            rtp_timestamp=rtp_timestamp_step,
        )
        server.active_sessions[call_id] = session
        task = asyncio.create_task(session.run(), name=f"wa_session_{call_id}")
        server._session_tasks[call_id] = task

        def _on_done(done_task: asyncio.Task[None]) -> None:
            server._session_tasks.pop(call_id, None)
            server.active_sessions.pop(call_id, None)
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

        logger.info(
            "WA INVITE accepted contact=%s media_port=%s",
            local_contact,
            local_media_port,
            extra={"call_id": call_id},
        )
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


def _build_gateway_client(
    server: WaSIPServer,
    invite: SipMessage,
    call_id: str,
    caller_phone: str,
) -> GatewayClient | None:
    """Build a GatewayClient if gateway mode is enabled."""
    if not getattr(server.config, "gateway_mode", False):
        return None
    if not getattr(server.config, "gateway_ws_url", ""):
        return None
    if not getattr(server.config, "gateway_ws_secret", ""):
        raise ValueError("WA_GATEWAY_WS_SECRET is required when WA_GATEWAY_MODE is enabled")

    user_id = canonical_phone_user_id(
        server.config.tenant_id, server.config.company_id, caller_phone,
        default_region=server.config.default_phone_region,
    )
    if user_id is None:
        anon_seed = f"{server.config.tenant_id}:{server.config.company_id}:call:{call_id}"
        user_id = f"wa-anon-{hashlib.sha256(anon_seed.encode()).hexdigest()[:16]}"
        if caller_phone:
            logger.warning(
                "Phone normalization failed for WA caller, using anonymous user_id",
                extra={"call_id": call_id},
            )
        else:
            logger.warning(
                "No caller phone in WA SIP From header, using anonymous user_id",
                extra={"call_id": call_id},
            )
    session_id = f"wa-{uuid.uuid4().hex[:24]}"
    return GatewayClient(
        gateway_ws_url=server.config.gateway_ws_url,
        user_id=user_id,
        session_id=session_id,
        tenant_id=server.config.tenant_id,
        company_id=server.config.company_id,
        industry="",  # omit -- session_init resolves from registry
        caller_phone=caller_phone,
        ws_secret=getattr(server.config, "gateway_ws_secret", ""),
    )
