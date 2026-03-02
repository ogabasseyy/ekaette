"""SIP dialog helpers — request parsing, G.711 SDP, response building.

Handles the SIP signaling needed to answer AT INVITE calls:
- Parse incoming SIP INVITE (method, headers, SDP body)
- Parse G.711 SDP to extract remote media address
- Build SDP answer with G.711 PCMU
- Build SIP responses (100 Trying, 200 OK)
"""

from __future__ import annotations

import os
import re

# Reuse regex patterns from wa_sip_client.py
_MEDIA_LINE_RE = re.compile(r"m=audio\s+(\d+)\s+\S+\s+([\d\s]+)")
_CONNECTION_RE = re.compile(r"c=IN\s+IP4\s+([\d.]+)")
_RTPMAP_RE = re.compile(r"a=rtpmap:(\d+)\s+(\S+)")


def parse_sip_request(message: str) -> dict:
    """Parse a raw SIP request into method, headers, and body.

    Returns:
        dict with keys: method (str), headers (dict[str, str]), body (str).
    """
    # Split headers from body at the blank line
    parts = message.split("\r\n\r\n", 1)
    header_block = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    lines = header_block.split("\r\n")
    if not lines:
        return {"method": "", "headers": {}, "body": ""}

    # Request line: "INVITE sip:user@host SIP/2.0"
    request_line = lines[0]
    method = request_line.split(" ", 1)[0] if request_line else ""

    # Parse headers
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()

    return {"method": method, "headers": headers, "body": body}


def parse_sdp_g711(sdp: str) -> dict:
    """Parse G.711 SDP to extract remote media parameters.

    Returns:
        dict with keys: media_ip (str), media_port (int),
        pcmu_payload_type (int), dtmf_payload_type (int | None).
    """
    result: dict = {
        "media_ip": "",
        "media_port": 0,
        "pcmu_payload_type": 0,  # PCMU is always PT 0 per RFC 3551
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

    # rtpmap lines — detect telephone-event for DTMF
    for match in _RTPMAP_RE.finditer(sdp):
        pt = int(match.group(1))
        codec = match.group(2).lower()
        if codec.startswith("telephone-event/"):
            result["dtmf_payload_type"] = pt

    return result


def build_sdp_answer(local_ip: str, rtp_port: int) -> str:
    """Build a G.711 PCMU SDP answer.

    Args:
        local_ip: Our public IP for the connection line.
        rtp_port: Our RTP port for the media line.

    Returns:
        SDP body string.
    """
    return (
        "v=0\r\n"
        f"o=ekaette 0 0 IN IP4 {local_ip}\r\n"
        "s=Ekaette SIP Bridge\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {rtp_port} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=ptime:20\r\n"
        "a=sendrecv\r\n"
    )


def build_sip_response(
    status: int,
    reason: str,
    invite_headers: dict[str, str],
    sdp_body: str | None,
    contact_uri: str,
) -> str:
    """Build a SIP response (100 Trying, 200 OK, etc).

    Copies Via, From, To, Call-ID, CSeq from the INVITE.
    Adds a tag to the To header per RFC 3261.

    Args:
        status: SIP status code (100, 200, etc).
        reason: SIP reason phrase.
        invite_headers: Headers from the original INVITE.
        sdp_body: SDP body for 200 OK (None for 100 Trying).
        contact_uri: Local Contact URI.

    Returns:
        Complete SIP response as string.
    """
    # Add tag to To header per RFC 3261 §8.2.6.2
    to_header = invite_headers.get("To", "")
    if ";tag=" not in to_header:
        to_header = f"{to_header};tag={os.urandom(4).hex()}"

    lines = [
        f"SIP/2.0 {status} {reason}",
        f"Via: {invite_headers.get('Via', '')}",
        f"From: {invite_headers.get('From', '')}",
        f"To: {to_header}",
        f"Call-ID: {invite_headers.get('Call-ID', '')}",
        f"CSeq: {invite_headers.get('CSeq', '')}",
        f"Contact: {contact_uri}",
    ]

    if sdp_body:
        lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(sdp_body)}")
        lines.append("")
        lines.append(sdp_body)
    else:
        lines.append("Content-Length: 0")
        lines.append("")
        lines.append("")

    return "\r\n".join(lines)
