"""WhatsApp SIP UA — dialog state machine, SDP, auth, call lifecycle.

Handles inbound and outbound SIP call flows for WhatsApp Business Calling.

State boundaries (from plan):
- Owns: SIP dialog state, SIP message parsing/generation
- Must NOT touch: Codec, SRTP keys, Gemini, Firestore
"""

from __future__ import annotations

import enum
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .sip_auth import build_challenge_header
from .sip_tls import SipMessage
from .srtp_context import format_crypto_line, generate_key_material

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dialog state machine — RFC 3261
# ---------------------------------------------------------------------------


class DialogState(enum.Enum):
    """SIP dialog states per RFC 3261."""

    IDLE = "idle"
    INVITED = "invited"
    EARLY = "early"
    CONFIRMED = "confirmed"
    TERMINATED = "terminated"


@dataclass
class Dialog:
    """Tracks state for a single SIP dialog (call)."""

    call_id: str
    state: DialogState = DialogState.IDLE
    local_tag: str = ""
    remote_tag: str = ""
    remote_sdp: dict[str, Any] = field(default_factory=dict)
    local_sdp_body: str = ""
    local_key_material: bytes = b""

    def transition(self, new_state: DialogState) -> None:
        """Transition to a new dialog state."""
        logger.debug(
            "Dialog %s: %s -> %s",
            self.call_id,
            self.state.name,
            new_state.name,
        )
        self.state = new_state


# ---------------------------------------------------------------------------
# Call ID resolution
# ---------------------------------------------------------------------------


def resolve_call_id(headers: dict[str, str]) -> str | None:
    """Resolve call ID from SIP headers.

    Prefers x-wa-meta-wacid (Meta's WhatsApp call ID).
    Falls back to SIP Call-ID. Returns None if neither present.
    """
    wacid = headers.get("x-wa-meta-wacid", "").strip()
    if wacid:
        return wacid
    call_id = headers.get("call-id", "").strip()
    return call_id or None


# ---------------------------------------------------------------------------
# SDP generation — our answer with Opus + SDES
# ---------------------------------------------------------------------------


def generate_sdp_answer(
    local_ip: str,
    local_port: int,
    payload_type: int = 111,
    key_material: bytes | None = None,
    ssrc: int | None = None,
) -> str:
    """Generate an SDP answer with Opus codec and SDES crypto.

    Returns the SDP body string. Key material is embedded in the crypto line.
    """
    local_key_material = key_material or generate_key_material()
    crypto_line = format_crypto_line(tag=1, key_material=local_key_material)

    # Meta's offer includes a=group:BUNDLE audio and a=mid:audio.
    # Per RFC 8843 §7.4 the answerer SHOULD echo BUNDLE when accepting.
    # Working Asterisk configs and the research agent both confirm this
    # is needed for Meta's relay to accept the session reliably.
    ssrc_line = ""
    if ssrc is not None:
        ssrc_line = f"a=ssrc:{ssrc} cname:EkaetteAudioStream\r\n"
    sdp = (
        "v=0\r\n"
        f"o=ekaette 1 1 IN IP4 {local_ip}\r\n"
        "s=Ekaette SIP Bridge\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        "t=0 0\r\n"
        "a=group:BUNDLE audio\r\n"
        f"m=audio {local_port} RTP/SAVP {payload_type} 126\r\n"
        "a=mid:audio\r\n"
        f"a=rtpmap:{payload_type} opus/48000/2\r\n"
        f"a=fmtp:{payload_type} maxplaybackrate=16000;sprop-maxcapturerate=16000;"
        "maxaveragebitrate=20000;useinbandfec=1\r\n"
        "a=rtpmap:126 telephone-event/8000\r\n"
        "a=fmtp:126 0-16\r\n"
        f"{crypto_line}\r\n"
        f"{ssrc_line}"
        "a=rtcp-mux\r\n"
        "a=ptime:20\r\n"
        "a=maxptime:20\r\n"
        "a=sendrecv\r\n"
    )
    return sdp


# ---------------------------------------------------------------------------
# SDP parsing — extract remote media parameters
# ---------------------------------------------------------------------------

_MEDIA_LINE_RE = re.compile(r"m=audio\s+(\d+)\s+\S+\s+([\d\s]+)")
_CONNECTION_RE = re.compile(r"c=IN\s+IP4\s+([\d.]+)")
_RTPMAP_RE = re.compile(r"a=rtpmap:(\d+)\s+(\S+)")
_FMTP_RE = re.compile(r"a=fmtp:(\d+)\s+(.*)")


def parse_remote_sdp(sdp: str) -> dict[str, Any]:
    """Parse remote SDP to extract media parameters.

    Returns dict with: media_ip, media_port, opus_payload_type,
    encode_rate, opus_channels, dtmf_payload_type.
    """
    result: dict[str, Any] = {
        "media_ip": "",
        "media_port": 0,
        "opus_payload_type": None,
        "encode_rate": 16000,  # Default per plan
        "opus_channels": 2,
        "dtmf_payload_type": None,
    }

    # Connection line
    conn_match = _CONNECTION_RE.search(sdp)
    if conn_match:
        result["media_ip"] = conn_match.group(1)

    # Media line
    media_match = _MEDIA_LINE_RE.search(sdp)
    if media_match:
        result["media_port"] = int(media_match.group(1))

    # rtpmap lines
    for match in _RTPMAP_RE.finditer(sdp):
        pt = int(match.group(1))
        codec = match.group(2).lower()
        if codec.startswith("opus/"):
            result["opus_payload_type"] = pt
            codec_parts = codec.split("/")
            if len(codec_parts) >= 3:
                try:
                    result["opus_channels"] = max(1, int(codec_parts[2]))
                except ValueError:
                    pass
        elif codec.startswith("telephone-event/"):
            result["dtmf_payload_type"] = pt

    # fmtp for Opus — extract maxplaybackrate
    if result["opus_payload_type"] is not None:
        for match in _FMTP_RE.finditer(sdp):
            pt = int(match.group(1))
            if pt == result["opus_payload_type"]:
                params = match.group(2)
                rate_match = re.search(r"maxplaybackrate=(\d+)", params)
                if rate_match:
                    result["encode_rate"] = int(rate_match.group(1))

    return result


# ---------------------------------------------------------------------------
# SIP response builders (for inbound call handling)
# ---------------------------------------------------------------------------


def _generate_tag() -> str:
    """Generate a random local tag for SIP dialog identification (RFC 3261 §19.3)."""
    return os.urandom(8).hex()


def _add_tag_to_to_header(to_header: str) -> str:
    """Append a locally-generated tag to the To header value.

    RFC 3261 §8.2.6.2: The UAS MUST add a tag to the To header field
    in the response if the request did not contain one.
    """
    if ";tag=" in to_header:
        return to_header
    return f"{to_header};tag={_generate_tag()}"


def build_407_response(invite: SipMessage, realm: str) -> SipMessage:
    """Build a 407 Proxy Authentication Required response to an INVITE."""
    challenge = build_challenge_header(status_code=407, realm=realm)

    headers = {
        "via": invite.headers.get("via", ""),
        "from": invite.headers.get("from", ""),
        "to": _add_tag_to_to_header(invite.headers.get("to", "")),
        "call-id": invite.headers.get("call-id", ""),
        "cseq": invite.headers.get("cseq", ""),
        # Extract just the value part (after "Proxy-Authenticate: ")
        "proxy-authenticate": challenge.split(": ", 1)[1] if ": " in challenge else challenge,
        "content-length": "0",
    }

    return SipMessage(
        first_line="SIP/2.0 407 Proxy Authentication Required",
        headers=headers,
        body="",
    )


def build_200_ok(
    invite: SipMessage,
    sdp_body: str,
    local_contact: str = "",
) -> SipMessage:
    """Build a 200 OK response to an INVITE with SDP answer.

    Args:
        invite: The original INVITE request.
        sdp_body: SDP answer body.
        local_contact: Local Contact URI (e.g., '<sip:ekaette@host:5061;transport=tls>').
    """
    headers = {
        "via": invite.headers.get("via", ""),
        "from": invite.headers.get("from", ""),
        "to": _add_tag_to_to_header(invite.headers.get("to", "")),
        "call-id": invite.headers.get("call-id", ""),
        "cseq": invite.headers.get("cseq", ""),
        "contact": local_contact,
        "allow": "INVITE, ACK, CANCEL, OPTIONS, BYE, NOTIFY",
        "content-type": "application/sdp",
        "content-length": str(len(sdp_body)),
    }
    # RFC 3261 §13.2.2.4: Copy Record-Route from INVITE into 200 OK
    # so that subsequent requests (ACK, BYE) route through the proxy chain.
    record_route = invite.headers.get("record-route", "")
    if record_route:
        headers["record-route"] = record_route

    return SipMessage(
        first_line="SIP/2.0 200 OK",
        headers=headers,
        body=sdp_body,
    )
