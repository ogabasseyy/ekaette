"""AT SDK/httpx wrappers for voice and SMS.

All external calls go through here. Wrapped with asyncio.to_thread()
because the AT Python SDK is synchronous.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def make_call(from_: str, to: list[str]) -> dict:
    """Initiate an outbound voice call via AT SDK."""
    import africastalking
    return await asyncio.to_thread(africastalking.Voice.call, callFrom=from_, callTo=to)


async def send_sms(message: str, recipients: list[str]) -> dict:
    """Send SMS via AT SDK."""
    import africastalking
    return await asyncio.to_thread(africastalking.SMS.send, message, recipients)


async def transfer_call(session_id: str, phone_number: str, call_leg: str = "callee") -> dict:
    """Transfer an active call via AT SDK."""
    import africastalking
    return await asyncio.to_thread(
        africastalking.Voice.transfer,
        sessionId=session_id,
        phoneNumber=phone_number,
        callLeg=call_leg,
    )
