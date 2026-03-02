"""SIP REGISTER client for AT registrar integration.

Handles:
- Building SIP REGISTER messages
- Parsing SIP responses (200, 401, 403, etc.)
- Digest authentication challenge/response flow
- Periodic re-registration to keep registration alive

The registrar is integrated into SIPServer — it shares the UDP transport
and is woken by SIPProtocol when a SIP response is received.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field

from .config import BridgeConfig
from .sip_auth import build_auth_header, parse_challenge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SIP message builders
# ---------------------------------------------------------------------------


def build_register_message(
    registrar: str,
    username: str,
    public_ip: str,
    port: int,
    call_id: str,
    cseq: int,
    expires: int,
    auth_header: str | None = None,
) -> str:
    """Build a SIP REGISTER request message.

    Args:
        registrar: SIP registrar domain (e.g., ng.sip.africastalking.com).
        username: Full SIP username (e.g., ekaette.ekaette@ng.sip.africastalking.com).
        public_ip: Our public IP address.
        port: Our SIP port.
        call_id: Unique Call-ID for this registration dialog.
        cseq: CSeq number (incremented per request).
        expires: Registration expiry in seconds.
        auth_header: Optional Authorization header for authenticated REGISTER.

    Returns:
        Complete SIP REGISTER message as string.
    """
    branch = f"z9hG4bK{os.urandom(8).hex()}"
    tag = os.urandom(4).hex()

    # Contact uses just the user part (before @) to avoid double-@ in the URI
    user_part = username.split("@", 1)[0] if "@" in username else username

    lines = [
        f"REGISTER sip:{registrar} SIP/2.0",
        f"Via: SIP/2.0/UDP {public_ip}:{port};branch={branch}",
        f"Max-Forwards: 70",
        f"From: <sip:{username}>;tag={tag}",
        f"To: <sip:{username}>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} REGISTER",
        f"Contact: <sip:{user_part}@{public_ip}:{port}>",
        f"Expires: {expires}",
        f"User-Agent: Ekaette-SIP-Bridge/1.0",
        f"Allow: INVITE, ACK, BYE, CANCEL, OPTIONS",
    ]

    if auth_header:
        lines.append(auth_header)

    lines.append("Content-Length: 0")
    lines.append("")
    lines.append("")

    return "\r\n".join(lines)


# ---------------------------------------------------------------------------
# SIP response parser
# ---------------------------------------------------------------------------


def parse_sip_response(message: str) -> dict:
    """Parse a SIP response into status code, reason, and headers.

    Returns:
        dict with keys: status_code (int), reason (str), headers (dict[str, str]).
    """
    lines = message.split("\r\n")
    if not lines:
        return {"status_code": 0, "reason": "", "headers": {}}

    # Parse status line: "SIP/2.0 200 OK"
    status_line = lines[0]
    parts = status_line.split(" ", 2)
    status_code = int(parts[1]) if len(parts) >= 2 else 0
    reason = parts[2] if len(parts) >= 3 else ""

    # Parse headers (stop at empty line)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()

    return {
        "status_code": status_code,
        "reason": reason,
        "headers": headers,
    }


# ---------------------------------------------------------------------------
# SIP Registrar client
# ---------------------------------------------------------------------------


@dataclass
class SIPRegistrar:
    """SIP REGISTER client that registers with AT's registrar.

    Lifecycle:
    1. start() resolves registrar IP, sends initial REGISTER
    2. On 401 → builds auth response and re-sends
    3. On 200 → marks registered, schedules re-register
    4. stop() sends REGISTER with Expires: 0 to unregister
    """

    config: BridgeConfig
    registered: bool = False
    _transport: object | None = None
    _call_id: str = field(default_factory=lambda: os.urandom(8).hex())
    _cseq: int = 1
    _registrar_addr: tuple[str, int] | None = None

    @property
    def should_register(self) -> bool:
        """Check if we have credentials to register."""
        return bool(self.config.sip_username and self.config.sip_password)

    def resolve_registrar(self) -> tuple[str, int] | None:
        """Resolve registrar hostname to IP:port."""
        if self._registrar_addr:
            return self._registrar_addr
        try:
            ip = socket.gethostbyname(self.config.sip_registrar)
            self._registrar_addr = (ip, 5060)
            logger.info(
                "Resolved SIP registrar",
                extra={"registrar": self.config.sip_registrar, "ip": ip},
            )
            return self._registrar_addr
        except socket.gaierror:
            logger.error(
                "Cannot resolve SIP registrar",
                extra={"registrar": self.config.sip_registrar},
            )
            return None

    def send_register(self, auth_header: str | None = None) -> None:
        """Send a REGISTER message via the shared UDP transport."""
        if not self._transport:
            logger.warning("No transport available for REGISTER")
            return

        addr = self.resolve_registrar()
        if not addr:
            return

        msg = build_register_message(
            registrar=self.config.sip_registrar,
            username=self.config.sip_username,
            public_ip=self.config.sip_public_ip,
            port=self.config.sip_port,
            call_id=self._call_id,
            cseq=self._cseq,
            expires=self.config.sip_register_interval,
            auth_header=auth_header,
        )
        self._cseq += 1

        self._transport.sendto(msg.encode("utf-8"), addr)
        logger.info(
            "Sent SIP REGISTER",
            extra={
                "registrar": self.config.sip_registrar,
                "cseq": self._cseq - 1,
                "authenticated": auth_header is not None,
            },
        )

    def handle_response(self, message: str) -> None:
        """Handle a SIP response to our REGISTER request."""
        parsed = parse_sip_response(message)
        status = parsed["status_code"]

        if status == 200:
            self.registered = True
            logger.info(
                "SIP registration successful",
                extra={"registrar": self.config.sip_registrar},
            )

        elif status == 401:
            # WWW-Authenticate challenge
            challenge_header = parsed["headers"].get("WWW-Authenticate", "")
            if not challenge_header:
                logger.error("401 without WWW-Authenticate header")
                return

            self._respond_to_challenge(401, challenge_header)

        elif status == 407:
            # Proxy-Authenticate challenge
            challenge_header = parsed["headers"].get("Proxy-Authenticate", "")
            if not challenge_header:
                logger.error("407 without Proxy-Authenticate header")
                return

            self._respond_to_challenge(407, challenge_header)

        elif status == 403:
            logger.error(
                "SIP registration forbidden (bad credentials?)",
                extra={
                    "registrar": self.config.sip_registrar,
                    "headers": parsed["headers"],
                },
            )

        else:
            logger.warning(
                "Unexpected SIP REGISTER response",
                extra={"status": status, "reason": parsed["reason"]},
            )

    @property
    def _auth_username(self) -> str:
        """Extract user part from SIP username for digest auth.

        SIP digest auth uses just the user part (e.g., 'agent1.ekaette')
        not the full address (e.g., 'agent1.ekaette@ng.sip.africastalking.com').
        """
        username = self.config.sip_username
        if "@" in username:
            return username.split("@", 1)[0]
        return username

    def _respond_to_challenge(self, status_code: int, challenge_value: str) -> None:
        """Build digest auth response and re-send REGISTER."""
        try:
            params = parse_challenge(challenge_value)
        except Exception:
            logger.exception("Failed to parse auth challenge")
            return

        logger.info(
            "SIP auth challenge received",
            extra={
                "realm": params.get("realm"),
                "algorithm": params.get("algorithm"),
                "qop": params.get("qop"),
                "status": status_code,
            },
        )

        uri = f"sip:{self.config.sip_registrar}"

        auth_header = build_auth_header(
            status_code=status_code,
            username=self._auth_username,
            realm=params["realm"],
            password=self.config.sip_password,
            nonce=params["nonce"],
            method="REGISTER",
            uri=uri,
            algorithm=params.get("algorithm", "MD5"),
            qop=params.get("qop"),
            opaque=params.get("opaque"),
        )

        self.send_register(auth_header=auth_header)

    def send_unregister(self) -> None:
        """Send REGISTER with Expires: 0 to unregister."""
        if not self._transport:
            return

        addr = self.resolve_registrar()
        if not addr:
            return

        msg = build_register_message(
            registrar=self.config.sip_registrar,
            username=self.config.sip_username,
            public_ip=self.config.sip_public_ip,
            port=self.config.sip_port,
            call_id=self._call_id,
            cseq=self._cseq,
            expires=0,
        )
        self._cseq += 1

        self._transport.sendto(msg.encode("utf-8"), addr)
        logger.info("Sent SIP unregister (Expires: 0)")
