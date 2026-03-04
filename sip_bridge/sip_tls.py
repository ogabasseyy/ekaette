"""Async TLS transport for SIP over TCP/TLS.

Provides:
- SipMessage dataclass for parsed SIP messages
- Header normalization (case-insensitive, compact forms per RFC 3261)
- Stream parser with security limits (64KB message, 100 headers)
- Serializer for outbound messages
- TLS connection helper (TLS 1.2+ only)

Python stdlib only (asyncio + ssl).
"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass, field
from typing import Any


class SipTransportError(Exception):
    """Raised on SIP transport-level errors (framing, limits)."""


# ---------------------------------------------------------------------------
# Header normalization — RFC 3261 §7.3.1 (case-insensitive) + compact forms
# ---------------------------------------------------------------------------

_COMPACT_FORMS: dict[str, str] = {
    "i": "call-id",
    "f": "from",
    "t": "to",
    "v": "via",
    "m": "contact",
    "l": "content-length",
    "c": "content-type",
    "e": "content-encoding",
    "k": "supported",
    "s": "subject",
}


def normalize_header_name(name: str) -> str:
    """Normalize a SIP header name to lowercase, expanding compact forms."""
    lower = name.lower().strip()
    return _COMPACT_FORMS.get(lower, lower)


# ---------------------------------------------------------------------------
# SipMessage dataclass
# ---------------------------------------------------------------------------

_MAX_MESSAGE_SIZE = 65536  # 64KB
_MAX_HEADER_COUNT = 100


@dataclass
class SipMessage:
    """Parsed SIP request or response."""

    first_line: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    @property
    def is_request(self) -> bool:
        """True if this is a SIP request (not a response)."""
        return not self.first_line.startswith("SIP/")

    @property
    def method(self) -> str | None:
        """Extract method from request line (e.g., 'INVITE')."""
        if self.is_request:
            return self.first_line.split(" ", 1)[0]
        return None

    @property
    def status_code(self) -> int | None:
        """Extract status code from response line (e.g., 200)."""
        if not self.is_request:
            parts = self.first_line.split(" ", 2)
            if len(parts) >= 2:
                return int(parts[1])
        return None


# ---------------------------------------------------------------------------
# Stream parser — strict TCP framing per RFC 3261 §18.3
# ---------------------------------------------------------------------------


async def parse_message(reader: asyncio.StreamReader) -> SipMessage | None:
    """Parse one SIP message from an async TCP stream.

    Returns None on clean EOF. Raises SipTransportError on protocol violations.

    Framing: read header lines until blank line (\\r\\n\\r\\n), then read
    exactly Content-Length bytes for the body.
    """
    # Read first line
    try:
        first_line_raw = await reader.readline()
    except (asyncio.IncompleteReadError, ConnectionError):
        return None

    if not first_line_raw:
        return None

    first_line = first_line_raw.decode("utf-8", errors="replace").rstrip("\r\n")
    if not first_line:
        return None

    # Read headers
    headers: dict[str, str] = {}
    header_count = 0
    total_size = len(first_line_raw)

    while True:
        try:
            line_raw = await reader.readline()
        except (asyncio.IncompleteReadError, ConnectionError):
            return None

        if not line_raw:
            return None

        total_size += len(line_raw)
        if total_size > _MAX_MESSAGE_SIZE:
            raise SipTransportError(
                f"SIP message headers exceed size limit ({_MAX_MESSAGE_SIZE} bytes)"
            )

        line = line_raw.decode("utf-8", errors="replace").rstrip("\r\n")

        # Blank line = end of headers
        if line == "":
            break

        # Parse "Header-Name: value"
        if ":" not in line:
            raise SipTransportError(
                f"Malformed SIP header line (no ':'): {line!r}"
            )

        name, _, value = line.partition(":")
        normalized = normalize_header_name(name)
        headers[normalized] = value.strip()
        header_count += 1

        if header_count > _MAX_HEADER_COUNT:
            raise SipTransportError(
                f"SIP message exceeds header count limit ({_MAX_HEADER_COUNT})"
            )

    # Read body based on Content-Length
    content_length_str = headers.get("content-length", "0")
    try:
        content_length = int(content_length_str)
    except ValueError:
        raise SipTransportError(
            f"Invalid Content-Length value: {content_length_str!r}"
        )

    if content_length < 0:
        raise SipTransportError(
            f"Negative Content-Length: {content_length}"
        )

    # Check total size including body
    if total_size + content_length > _MAX_MESSAGE_SIZE:
        raise SipTransportError(
            f"SIP message size {total_size + content_length} exceeds limit "
            f"({_MAX_MESSAGE_SIZE} bytes)"
        )

    body = ""
    if content_length > 0:
        try:
            body_raw = await reader.readexactly(content_length)
            body = body_raw.decode("utf-8", errors="replace")
        except asyncio.IncompleteReadError:
            return None

    return SipMessage(first_line=first_line, headers=headers, body=body)


# ---------------------------------------------------------------------------
# Serializer — SipMessage -> bytes
# ---------------------------------------------------------------------------


def serialize_message(msg: SipMessage) -> bytes:
    """Serialize a SipMessage to bytes for sending over TLS.

    Header names are title-cased for wire compatibility.
    """
    body_bytes = msg.body.encode("utf-8")
    headers = {
        normalize_header_name(name): value
        for name, value in msg.headers.items()
    }
    # RFC 3261 framing over stream transports depends on exact body byte length.
    headers["content-length"] = str(len(body_bytes))

    lines: list[str] = [msg.first_line]

    for name, value in headers.items():
        # Title-case header name for wire format (e.g., call-id -> Call-Id)
        wire_name = "-".join(part.capitalize() for part in name.split("-"))
        lines.append(f"{wire_name}: {value}")

    header_block = "\r\n".join(lines) + "\r\n\r\n"
    return header_block.encode("utf-8") + body_bytes


# ---------------------------------------------------------------------------
# TLS connection helper
# ---------------------------------------------------------------------------


def create_tls_context(
    certfile: str | None = None,
    keyfile: str | None = None,
    server_side: bool = False,
) -> ssl.SSLContext:
    """Create an SSL context for SIP TLS (TLS 1.2+ only).

    For client mode: verifies server cert.
    For server mode: requires certfile + keyfile.
    """
    if server_side:
        if not certfile or not keyfile:
            raise ValueError("certfile and keyfile are required for server-side TLS")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    # TLS 1.2+ only
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    return ctx


async def connect(
    host: str,
    port: int,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a TLS connection to a SIP server.

    Returns (reader, writer) for stream I/O.
    """
    if ssl_context is None:
        ssl_context = create_tls_context()

    reader, writer = await asyncio.open_connection(
        host, port, ssl=ssl_context
    )
    return reader, writer
