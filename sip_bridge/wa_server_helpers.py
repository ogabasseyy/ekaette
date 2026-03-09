"""Shared helpers for WhatsApp SIP server runtime.

Isolated from ``wa_main.py`` to keep the entrypoint module compact and focused.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
from typing import Awaitable, Callable

from .sip_tls import SipMessage


def _ensure_to_tag(to_header: str) -> str:
    """Ensure To header contains a local tag for final INVITE responses."""
    if not to_header or ";tag=" in to_header:
        return to_header
    return f"{to_header};tag={os.urandom(8).hex()}"


def build_transaction_response(
    msg: SipMessage,
    *,
    status_code: int,
    reason: str,
    call_id: str | None = None,
    add_local_to_tag: bool = False,
) -> SipMessage:
    """Build a SIP response preserving required transaction headers."""
    to_header = msg.headers.get("to", "")
    if add_local_to_tag:
        to_header = _ensure_to_tag(to_header)
    headers = {
        "via": msg.headers.get("via", ""),
        "from": msg.headers.get("from", ""),
        "to": to_header,
        "call-id": call_id if call_id is not None else msg.headers.get("call-id", ""),
        "cseq": msg.headers.get("cseq", ""),
        "content-length": "0",
    }
    return SipMessage(
        first_line=f"SIP/2.0 {status_code} {reason}",
        headers=headers,
        body="",
    )


def resolve_advertised_ip(
    bind_host: str,
    *,
    public_ip: str = "",
    logger: logging.Logger,
) -> str:
    """Resolve a non-loopback IP to advertise in SDP when bound to wildcard."""
    candidate = (bind_host or "").strip()
    if candidate and candidate not in {"0.0.0.0", "::"} and not candidate.startswith("127."):
        return candidate

    configured_public_ip = public_ip.strip() or os.getenv("WA_SIP_PUBLIC_IP", "").strip()
    if configured_public_ip and not configured_public_ip.startswith("127."):
        return configured_public_ip

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            discovered_ip = probe.getsockname()[0]
            if discovered_ip and not discovered_ip.startswith("127."):
                return discovered_ip
    except Exception:
        logger.debug("Failed to auto-detect routable local IP for SDP", exc_info=True)

    return "127.0.0.1"


async def handle_health_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    active_sessions: int,
    max_concurrent_calls: int,
    logger: logging.Logger,
) -> None:
    """Handle HTTP health/readiness check requests."""
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        path = request_line.decode("utf-8", errors="replace").split(" ")[1] if b" " in request_line else "/"
        # Drain remaining headers.
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line in (b"\r\n", b"\n", b""):
                break

        if path == "/healthz":
            body = json.dumps({"status": "ok"})
            status = 200
        elif path == "/readyz":
            at_capacity = active_sessions >= max_concurrent_calls
            status = 503 if at_capacity else 200
            body = json.dumps({
                "status": "unavailable" if at_capacity else "ready",
                "active_sessions": active_sessions,
                "max_concurrent_calls": max_concurrent_calls,
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
        logger.exception("Health endpoint request handling failed")
        if not writer.is_closing():
            body = json.dumps({"status": "error"})
            response = (
                "HTTP/1.1 500 Internal Server Error\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
                f"{body}"
            )
            with contextlib.suppress(Exception):
                writer.write(response.encode("utf-8"))
                await writer.drain()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def handle_sip_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    parse_message: Callable[[asyncio.StreamReader], Awaitable[SipMessage | None]],
    serialize_message: Callable[[SipMessage], bytes],
    dispatch: Callable[[SipMessage, tuple[str, int]], Awaitable[SipMessage | None]],
    logger: logging.Logger,
) -> None:
    """Handle a single TLS connection that may carry multiple SIP messages."""
    peer = writer.get_extra_info("peername", ("unknown", 0))
    try:
        while True:
            msg = await parse_message(reader)
            if msg is None:
                break
            resp = await dispatch(msg, peer)
            if resp is not None:
                writer.write(serialize_message(resp))
                await writer.drain()
    except Exception:
        logger.exception("Connection error from %s", peer)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
