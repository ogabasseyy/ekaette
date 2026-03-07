"""WebSocket client bridging SIP audio to Cloud Run ADK.

Phase 1 of Single AI Brain — makes SIP bridge a thin transport adapter
that connects to Cloud Run instead of Gemini Live directly. One AI brain
for all channels.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json as _json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import AsyncIterator
from urllib.parse import quote, urlencode

import websockets

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GatewayFrame:
    """A frame received from the Cloud Run WebSocket."""

    is_audio: bool  # True = binary PCM16, False = JSON text
    audio_data: bytes = b""  # populated when is_audio=True
    text_data: str = ""  # populated when is_audio=False


class GatewayConnectionError(Exception):
    """Raised when gateway WebSocket connection fails."""


class GatewayDisconnectedError(ConnectionError):
    """Raised when an operation requires an active gateway websocket."""


@dataclass
class GatewayClient:
    """WebSocket client bridging SIP audio to Cloud Run ADK."""

    gateway_ws_url: str  # e.g. "wss://ekaette-xxx.run.app"
    user_id: str
    session_id: str
    tenant_id: str = "public"
    company_id: str = "ekaette-electronics"
    industry: str = "electronics"
    caller_phone: str = field(default="", repr=False)
    ws_secret: str = field(
        default="",
        repr=False,
    )  # Shared HMAC secret (same value as WS_TOKEN_SECRET on Cloud Run)

    # Reconnect state (updated from session_started / session_ending frames)
    _canonical_session_id: str = field(default="", repr=False)
    _resumption_token: str = field(default="", repr=False)

    # WebSocket connection
    _ws: websockets.WebSocketClientProtocol | None = field(
        default=None, repr=False
    )

    # Token TTL for minted per-call tokens (seconds)
    _TOKEN_TTL: int = 300

    @property
    def canonical_session_id(self) -> str:
        """Canonical backend session ID captured after startup or resume."""
        return self._canonical_session_id

    @property
    def resumption_token(self) -> str:
        """Latest backend-issued resumption token."""
        return self._resumption_token

    def remember_canonical_session_id(self, session_id: str) -> None:
        """Store the backend-issued canonical session ID."""
        self._canonical_session_id = session_id

    def remember_resumption_token(self, token: str) -> None:
        """Store the latest backend-issued resumption token."""
        self._resumption_token = token

    def _mint_token(self) -> str:
        """Mint a per-call HMAC-SHA256 token matching ws_auth.py's format.

        Creates a fresh token with the current user_id, tenant_id, company_id,
        a unique JTI, and a TTL. Uses the same HMAC signing algorithm as
        app/api/v1/public/ws_auth.py:create_ws_token so the Cloud Run server
        can validate it with validate_ws_token(token, expected_user_id).
        """
        if not self.ws_secret:
            raise ValueError("Missing ws_secret for gateway HMAC token generation")
        secret = self.ws_secret.encode("utf-8")

        def _b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        header = _b64url(_json.dumps({"alg": "HS256", "typ": "WS"}).encode())
        payload_dict = {
            "sub": self.user_id,
            "tenant_id": self.tenant_id,
            "company_id": self.company_id,
            "exp": time.time() + self._TOKEN_TTL,
            "jti": secrets.token_urlsafe(16),
            "caller_phone": self.caller_phone,
        }
        payload = _b64url(_json.dumps(payload_dict).encode())

        signing_input = f"{header}.{payload}".encode()
        signature = _b64url(
            hmac.new(secret, signing_input, hashlib.sha256).digest()
        )
        return f"{header}.{payload}.{signature}"

    def _build_connect_url(
        self,
        *,
        session_id_override: str = "",
        resumption_token: str = "",
    ) -> str:
        """Build the WebSocket URL with path and query params."""
        sid = session_id_override or self.session_id
        base = self.gateway_ws_url.rstrip("/")
        path = f"/ws/{quote(self.user_id, safe='')}/{quote(sid, safe='')}"

        params: dict[str, str] = {}
        if self.tenant_id:
            params["tenantId"] = self.tenant_id
        if self.company_id:
            params["companyId"] = self.company_id
        if self.industry:
            params["industry"] = self.industry
        # Mint a fresh per-call token (unique JTI, correct user_id)
        token = self._mint_token()
        params["token"] = token
        if resumption_token:
            params["resumption_token"] = resumption_token

        qs = urlencode(params) if params else ""
        return f"{base}{path}?{qs}" if qs else f"{base}{path}"

    async def connect(self) -> None:
        """Connect to Cloud Run WebSocket endpoint."""
        url = self._build_connect_url()
        try:
            self._ws = await websockets.connect(url)
            logger.info("Gateway connected: %s", url.split("?")[0])
        except Exception as exc:
            logger.error(
                "Gateway connect failed: %s (%s)",
                url.split("?")[0],
                exc.__class__.__name__,
            )
            raise GatewayConnectionError("Failed to connect to gateway") from None

    async def reconnect(self) -> None:
        """Reconnect using canonical session ID + resumption token.

        Uses _canonical_session_id (from session_started) if available.
        Passes _resumption_token as query param if available.
        """
        # Close existing connection if any
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.exception("Gateway reconnect failed to close existing websocket")
            self._ws = None

        sid = self.canonical_session_id or self.session_id
        url = self._build_connect_url(
            session_id_override=sid,
            resumption_token=self.resumption_token,
        )
        try:
            self._ws = await websockets.connect(url)
            logger.info("Gateway reconnected: %s", url.split("?")[0])
        except Exception as exc:
            logger.error(
                "Gateway reconnect failed: %s (%s)",
                url.split("?")[0],
                exc.__class__.__name__,
            )
            raise GatewayConnectionError("Failed to connect to gateway") from None

    async def send_audio(self, pcm16: bytes) -> None:
        """Send PCM16 16kHz audio as binary WebSocket frame."""
        if self._ws is None:
            raise GatewayDisconnectedError("send_audio called while gateway websocket is disconnected")
        await self._ws.send(pcm16)

    async def send_text(self, json_str: str) -> None:
        """Send JSON control message as text WebSocket frame."""
        if self._ws is None:
            raise GatewayDisconnectedError("send_text called while gateway websocket is disconnected")
        await self._ws.send(json_str)

    async def receive(self) -> AsyncIterator[GatewayFrame]:
        """Yield frames from Cloud Run — binary (audio) or text (JSON)."""
        if self._ws is None:
            raise GatewayDisconnectedError("receive called while gateway websocket is disconnected")
        async for message in self._ws:
            if isinstance(message, bytes):
                yield GatewayFrame(is_audio=True, audio_data=message)
            else:
                yield GatewayFrame(is_audio=False, text_data=message)

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
