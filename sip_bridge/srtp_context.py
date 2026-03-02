"""SDES/SRTP context — parse SDP crypto lines and protect/unprotect RTP.

Uses pylibsrtp for SRTP operations. No ICE/DTLS needed with SDES.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any


class SRTPError(Exception):
    """Raised on SRTP protect/unprotect failure."""


# ---------------------------------------------------------------------------
# SDP crypto line parsing
# ---------------------------------------------------------------------------

_CRYPTO_RE = re.compile(
    r"a=crypto:(\d+)\s+([\w_]+)\s+inline:([A-Za-z0-9+/=]+)"
)


_SUPPORTED_SUITES = frozenset({
    "AES_CM_128_HMAC_SHA1_80",
    "AES_CM_128_HMAC_SHA1_32",
})

_EXPECTED_KEY_LENGTHS: dict[str, int] = {
    "AES_CM_128_HMAC_SHA1_80": 30,  # 16-byte key + 14-byte salt
    "AES_CM_128_HMAC_SHA1_32": 30,
}


def parse_sdes_crypto(sdp: str) -> dict[str, Any] | None:
    """Parse the first a=crypto line from SDP.

    Returns dict with keys: tag (int), suite (str), key (bytes)
    or None if no crypto line found.

    Raises SRTPError if suite is unsupported or key length is wrong.
    """
    match = _CRYPTO_RE.search(sdp)
    if not match:
        return None

    suite = match.group(2)
    if suite not in _SUPPORTED_SUITES:
        raise SRTPError(f"Unsupported SRTP suite: {suite}")

    key = base64.b64decode(match.group(3))
    expected = _EXPECTED_KEY_LENGTHS[suite]
    if len(key) != expected:
        raise SRTPError(
            f"Key length {len(key)} bytes, expected {expected} for {suite}"
        )

    return {
        "tag": int(match.group(1)),
        "suite": suite,
        "key": key,
    }


def generate_key_material() -> bytes:
    """Generate random SRTP key material (16-byte key + 14-byte salt = 30 bytes)."""
    return os.urandom(30)


def format_crypto_line(tag: int, key_material: bytes, suite: str = "AES_CM_128_HMAC_SHA1_80") -> str:
    """Format an a=crypto line for SDP answer."""
    b64_key = base64.b64encode(key_material).decode()
    return f"a=crypto:{tag} {suite} inline:{b64_key}"


# ---------------------------------------------------------------------------
# SRTP context wrapping pylibsrtp
# ---------------------------------------------------------------------------


class SRTPContext:
    """SRTP protect/unprotect using pylibsrtp.

    Args:
        key_material: 30 bytes (16-byte master key + 14-byte master salt)
        is_sender: True for protect (outbound), False for unprotect (inbound)
    """

    def __init__(self, key_material: bytes, is_sender: bool) -> None:
        if len(key_material) != 30:
            raise SRTPError(
                f"Key length {len(key_material)} bytes, expected 30 "
                "(16-byte master key + 14-byte salt)"
            )

        from pylibsrtp import Policy, Session

        policy = Policy()
        policy.key = key_material
        if is_sender:
            policy.ssrc_type = Policy.SSRC_ANY_OUTBOUND
        else:
            policy.ssrc_type = Policy.SSRC_ANY_INBOUND

        self._session = Session(policy=policy)

    def protect(self, rtp: bytes) -> bytes:
        """Encrypt RTP packet -> SRTP packet."""
        try:
            return self._session.protect(rtp)
        except Exception as exc:
            raise SRTPError(f"SRTP protect failed: {exc}") from exc

    def unprotect(self, srtp: bytes) -> bytes:
        """Decrypt SRTP packet -> RTP packet."""
        try:
            return self._session.unprotect(srtp)
        except Exception as exc:
            raise SRTPError(f"SRTP unprotect failed: {exc}") from exc
