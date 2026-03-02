"""Preflight validation for AT/SIP bridge environment.

Must pass before service boot. Validates required env vars and secrets.
Run: python -m scripts.preflight_at
"""

from __future__ import annotations

import os
import sys


def _check_env(name: str, required: bool = True) -> str | None:
    """Check an env var exists and is non-empty."""
    val = os.getenv(name, "").strip()
    if required and not val:
        return f"MISSING: {name} is required but not set"
    return None


def validate_at_env() -> list[str]:
    """Validate AT-related environment variables."""
    errors: list[str] = []

    # Core AT credentials
    for var in ("AT_USERNAME", "AT_API_KEY"):
        err = _check_env(var)
        if err:
            errors.append(err)

    # Virtual number
    err = _check_env("AT_VIRTUAL_NUMBER")
    if err:
        errors.append(err)

    # AT environment must be sandbox or production
    at_env = os.getenv("AT_ENVIRONMENT", "").strip().lower()
    if at_env and at_env not in ("sandbox", "production"):
        errors.append(f"INVALID: AT_ENVIRONMENT='{at_env}' (must be sandbox|production)")

    # Gemini API key (needed for SMS text bridge)
    err = _check_env("GOOGLE_API_KEY")
    if err:
        errors.append(err)

    return errors


def validate_sip_bridge_env() -> list[str]:
    """Validate SIP bridge environment variables (if bridge is expected)."""
    errors: list[str] = []

    sip_endpoint = os.getenv("SIP_BRIDGE_ENDPOINT", "").strip()
    if not sip_endpoint:
        # SIP bridge is optional — warn but don't fail
        errors.append("WARNING: SIP_BRIDGE_ENDPOINT not set (voice bridge unavailable)")
        return errors

    # If bridge endpoint is set, validate bridge-side vars
    for var in (
        "SIP_BRIDGE_HOST",
        "SIP_BRIDGE_PORT",
        "SIP_PUBLIC_IP",
        "SIP_USERNAME",
        "SIP_PASSWORD",
    ):
        err = _check_env(var)
        if err:
            errors.append(err)

    return errors


def validate_secrets_not_in_env_file() -> list[str]:
    """Warn if secrets appear to be in plaintext .env file in production."""
    errors: list[str] = []
    at_env = os.getenv("AT_ENVIRONMENT", "sandbox").strip().lower()
    if at_env != "production":
        return errors

    # In production, secrets should come from secret manager, not .env
    env_file = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_file):
        content = open(env_file).read()
        sensitive_keys = ["AT_API_KEY=", "GOOGLE_API_KEY=", "AT_WEBHOOK_SHARED_SECRET="]
        for key in sensitive_keys:
            if key in content:
                # Check if the value after = is non-empty
                for line in content.splitlines():
                    if line.startswith(key) and line.split("=", 1)[1].strip():
                        errors.append(
                            f"SECURITY: {key.rstrip('=')} has plaintext value in .env "
                            f"(use secret manager in production)"
                        )
    return errors


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    at_errors = validate_at_env()
    sip_errors = validate_sip_bridge_env()
    sec_errors = validate_secrets_not_in_env_file()

    for err in at_errors + sip_errors + sec_errors:
        if err.startswith("WARNING:"):
            warnings.append(err)
        else:
            errors.append(err)

    if warnings:
        print("Preflight warnings:")
        for w in warnings:
            print(f"  ⚠ {w}")

    if errors:
        print("Preflight validation FAILED:")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print("Preflight validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
