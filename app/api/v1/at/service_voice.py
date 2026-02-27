"""Voice channel business logic.

XML building, DID→tenant/company resolution, call lifecycle logging.
Routes delegate here — no business logic in voice.py.
"""

from __future__ import annotations

import logging

from .settings import (
    SIP_BRIDGE_ENDPOINT,
    AT_VIRTUAL_NUMBER,
    AT_RECORDING_ENABLED,
    AT_RECORDING_DISCLOSURE,
)

logger = logging.getLogger(__name__)


def resolve_tenant_context(destination_number: str) -> tuple[str, str]:
    """Resolve tenant_id and company_id from the called virtual number.

    For now, returns defaults. In production, this will look up a
    DID→tenant/company mapping table.
    """
    # TODO: DID mapping table (Phase 2 production)
    return "public", "ekaette-electronics"


def build_dial_xml(sip_endpoint: str, caller_id: str) -> str:
    """Build AT XML to bridge caller to SIP-to-AI server.

    When recording is enabled, prepends a <Say> disclosure per data governance.
    """
    record_attr = 'record="true"' if AT_RECORDING_ENABLED else 'record="false"'
    disclosure = ""
    if AT_RECORDING_ENABLED and AT_RECORDING_DISCLOSURE:
        disclosure = f'    <Say>{AT_RECORDING_DISCLOSURE}</Say>\n'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f"{disclosure}"
        f'    <Dial phoneNumbers="{sip_endpoint}" '
        f'{record_attr} sequential="true" '
        f'callerId="{caller_id}"/>\n'
        "</Response>"
    )


def build_end_xml() -> str:
    """Build empty AT XML response for ended calls."""
    return "<Response/>"


def log_call_bridged(session_id: str, caller: str, direction: str) -> None:
    """Structured log for call bridge initiation."""
    tenant_id, company_id = resolve_tenant_context(AT_VIRTUAL_NUMBER)
    logger.info(
        "AT call bridged",
        extra={
            "at_session_id": session_id,
            "caller": caller,
            "direction": direction,
            "tenant_id": tenant_id,
            "company_id": company_id,
            "sip_endpoint": SIP_BRIDGE_ENDPOINT,
        },
    )


def log_call_ended(
    session_id: str,
    caller: str,
    duration_seconds: str,
    amount: str,
) -> None:
    """Structured log for call completion."""
    logger.info(
        "AT call ended",
        extra={
            "at_session_id": session_id,
            "caller": caller,
            "duration_seconds": duration_seconds,
            "amount": amount,
        },
    )
