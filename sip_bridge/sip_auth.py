"""SIP digest authentication (RFC 2617 + RFC 7616).

Supports both:
- 407 Proxy-Authenticate / Proxy-Authorization
- 401 WWW-Authenticate / Authorization

Pure stdlib implementation (hashlib only).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Any


class AuthParseError(Exception):
    """Raised when a challenge header cannot be parsed."""


# ---------------------------------------------------------------------------
# Parse challenge headers
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r'(\w+)=(?:"([^"]+)"|([^,\s]+))')


def parse_challenge(header: str) -> dict[str, Any]:
    """Parse a Proxy-Authenticate or WWW-Authenticate header.

    Returns dict with: realm, nonce, algorithm (default MD5), qop, opaque.
    Raises AuthParseError if the header is not a valid Digest challenge.
    """
    # Strip optional header name prefix (e.g., "Proxy-Authenticate: Digest ...")
    # Use regex to detect header-name pattern (alphanumeric + hyphens + colon)
    # to avoid splitting on colons inside parameter values like uri="sip:..."
    value = header.strip()
    header_prefix = re.match(r'^[\w-]+\s*:\s*', value)
    if header_prefix:
        value = value[header_prefix.end():]

    if not value.lower().startswith("digest"):
        raise AuthParseError(f"Not a Digest challenge: {header}")

    value = value[6:].strip()  # Skip "Digest "

    params: dict[str, Any] = {}
    for match in _PARAM_RE.finditer(value):
        key = match.group(1).lower()
        val = match.group(2) if match.group(2) is not None else match.group(3)
        params[key] = val

    if "realm" not in params or "nonce" not in params:
        raise AuthParseError(f"Missing required fields in challenge: {header}")

    # Default algorithm is MD5 per RFC 2617
    params.setdefault("algorithm", "MD5")

    return params


# ---------------------------------------------------------------------------
# Compute digest response
# ---------------------------------------------------------------------------


def _hash(data: str, algorithm: str) -> str:
    """Hash a string using the specified algorithm."""
    if algorithm.upper() in ("MD5", "MD5-SESS"):
        return hashlib.md5(data.encode()).hexdigest()
    elif algorithm.upper() in ("SHA-256", "SHA-256-SESS"):
        return hashlib.sha256(data.encode()).hexdigest()
    else:
        raise ValueError(f"Unsupported algorithm: {algorithm}")


def _select_qop(qop: str | None) -> str | None:
    """Select a single qop token from a possibly multi-value challenge.

    Challenges may offer qop="auth,auth-int". We prefer "auth" (most common
    for SIP). Returns None if qop is None.
    """
    if qop is None:
        return None
    tokens = [t.strip() for t in qop.split(",")]
    if "auth" in tokens:
        return "auth"
    if "auth-int" in tokens:
        return "auth-int"
    return tokens[0] if tokens else None


def compute_digest_response(
    username: str,
    realm: str,
    password: str,
    nonce: str,
    method: str,
    uri: str,
    algorithm: str = "MD5",
    qop: str | None = None,
    nc: str | None = None,
    cnonce: str | None = None,
) -> str:
    """Compute the digest response hash per RFC 2617/7616.

    Supports -sess variants (MD5-sess, SHA-256-sess) which incorporate
    nonce and cnonce into HA1 per RFC 2617 §3.2.2.2.

    Returns the hex digest string.
    """
    ha1 = _hash(f"{username}:{realm}:{password}", algorithm)

    # -sess variants: HA1 = H(H(user:realm:pass):nonce:cnonce)
    is_sess = algorithm.upper().endswith("-SESS")
    if is_sess:
        if nc is None or cnonce is None:
            raise ValueError("nc and cnonce required for -sess algorithms")
        ha1 = _hash(f"{ha1}:{nonce}:{cnonce}", algorithm)

    ha2 = _hash(f"{method}:{uri}", algorithm)

    # Select single qop token from multi-value challenge
    selected_qop = _select_qop(qop)

    if selected_qop and selected_qop in ("auth", "auth-int"):
        if nc is None or cnonce is None:
            raise ValueError("nc and cnonce required when qop is set")
        response = _hash(f"{ha1}:{nonce}:{nc}:{cnonce}:{selected_qop}:{ha2}", algorithm)
    else:
        # Legacy (no qop) — RFC 2069 compatibility
        response = _hash(f"{ha1}:{nonce}:{ha2}", algorithm)

    return response


# ---------------------------------------------------------------------------
# Build auth response headers
# ---------------------------------------------------------------------------


def verify_digest(
    auth_value: str,
    expected_username: str,
    expected_password: str,
    method: str,
) -> bool:
    """Verify a Proxy-Authorization or Authorization header value.

    Args:
        auth_value: The header value (after 'Proxy-Authorization: ').
        expected_username: Expected SIP username.
        expected_password: Expected SIP password.
        method: SIP method (e.g., 'INVITE').

    Returns True if credentials are valid, False otherwise.
    """
    try:
        params = parse_challenge(auth_value)
    except AuthParseError:
        return False

    provided_username = params.get("username")
    if (
        not isinstance(provided_username, str)
        or not hmac.compare_digest(provided_username, expected_username)
    ):
        return False

    try:
        expected_response = compute_digest_response(
            username=expected_username,
            realm=params.get("realm", ""),
            password=expected_password,
            nonce=params.get("nonce", ""),
            method=method,
            uri=params.get("uri", ""),
            algorithm=params.get("algorithm", "MD5"),
            qop=_select_qop(params.get("qop")),
            nc=params.get("nc"),
            cnonce=params.get("cnonce"),
        )
    except (ValueError, TypeError):
        return False
    provided_response = params.get("response")
    if not isinstance(provided_response, str):
        return False
    return hmac.compare_digest(expected_response, provided_response)


def build_auth_header(
    status_code: int,
    username: str,
    realm: str,
    password: str,
    nonce: str,
    method: str,
    uri: str,
    algorithm: str = "MD5",
    qop: str | None = None,
    opaque: str | None = None,
) -> str:
    """Build a Proxy-Authorization (407) or Authorization (401) header.

    Generates nc and cnonce automatically when qop is set.
    Selects a single qop token from multi-value challenges.
    """
    # Select single qop from possibly multi-value challenge
    selected_qop = _select_qop(qop)

    nc_val: str | None = None
    cnonce_val: str | None = None

    if selected_qop or algorithm.upper().endswith("-SESS"):
        nc_val = "00000001"
        cnonce_val = os.urandom(8).hex()

    response = compute_digest_response(
        username=username,
        realm=realm,
        password=password,
        nonce=nonce,
        method=method,
        uri=uri,
        algorithm=algorithm,
        qop=selected_qop,
        nc=nc_val,
        cnonce=cnonce_val,
    )

    header_name = "Proxy-Authorization" if status_code == 407 else "Authorization"

    parts = [
        f'{header_name}: Digest username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"',
        f"algorithm={algorithm}",
    ]

    if selected_qop:
        parts.append(f"qop={selected_qop}")
        parts.append(f"nc={nc_val}")
        parts.append(f'cnonce="{cnonce_val}"')

    if opaque:
        parts.append(f'opaque="{opaque}"')

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Build challenge headers (for inbound auth on our SIP server)
# ---------------------------------------------------------------------------


def build_challenge_header(
    status_code: int,
    realm: str,
    algorithm: str = "MD5",
    qop: str = "auth",
) -> str:
    """Build a Proxy-Authenticate (407) or WWW-Authenticate (401) challenge.

    Generates a unique nonce per call.
    """
    nonce = _generate_nonce()
    header_name = "Proxy-Authenticate" if status_code == 407 else "WWW-Authenticate"

    return (
        f'{header_name}: Digest realm="{realm}", '
        f'nonce="{nonce}", '
        f"algorithm={algorithm}, "
        f'qop="{qop}"'
    )


def _generate_nonce() -> str:
    """Generate a unique nonce for digest auth challenges."""
    timestamp = str(time.time_ns())
    random_part = os.urandom(16).hex()
    return hashlib.sha256(f"{timestamp}:{random_part}".encode()).hexdigest()
