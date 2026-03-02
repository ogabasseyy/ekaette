"""WhatsApp Business Calling demo — local SIP loopback.

Exercises the full call lifecycle against WaSIPServer in sandbox mode:
  1. INVITE → 407 challenge
  2. Re-INVITE with digest auth → 200 OK (session created)
  3. BYE → 200 OK (session terminated)

Usage: python -m scripts.demo_wa_calling
No external dependencies — runs entirely in-process.
"""

from __future__ import annotations

import asyncio
import sys


async def run_demo() -> bool:
    """Run the full SIP call lifecycle demo. Returns True on success."""
    from sip_bridge.sip_auth import build_auth_header, parse_challenge
    from sip_bridge.sip_tls import SipMessage
    from sip_bridge.wa_config import WhatsAppBridgeConfig
    from sip_bridge.wa_main import WaSIPServer

    # Sandbox config — no TLS, no CIDR restrictions
    config = WhatsAppBridgeConfig(
        sip_host="0.0.0.0",
        sip_port=15061,
        sip_username="+2348001234567",
        sip_password="demo-password",
        sip_allowed_cidrs=frozenset(),
        tls_certfile="",
        tls_keyfile="",
        sandbox_mode=True,
        gemini_api_key="",
        live_model_id="gemini-test",
        system_instruction="Demo assistant",
        gemini_voice="Aoede",
        company_id="ekaette-electronics",
        tenant_id="public",
        health_port=18082,
    )
    server = WaSIPServer(config=config, max_concurrent_calls=5)
    peer = ("127.0.0.1", 5061)

    print("=== WhatsApp Business Calling Demo ===\n")

    # Step 1: INVITE (no auth) → expect 407
    print("[1] Sending INVITE (no auth)...")
    invite1 = SipMessage(
        first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
        headers={
            "call-id": "demo-call-001",
            "from": "<sip:+1234567890@wa.meta.vc>;tag=demo1",
            "to": "<sip:+2348001234567@example.com>",
            "via": "SIP/2.0/TLS 127.0.0.1:5061",
            "cseq": "1 INVITE",
            "content-length": "0",
        },
        body="",
    )
    resp1 = await server.handle_sip_message(invite1, peer)
    if resp1 is None or resp1.status_code != 407:
        print(f"  FAIL: expected 407, got {resp1}")
        return False
    print(f"  <- {resp1.status_code} (challenge issued)")

    # Step 2: Re-INVITE with digest auth + SDP → expect 200 OK
    print("[2] Sending INVITE with auth + SDP...")
    challenge = resp1.headers["proxy-authenticate"]
    params = parse_challenge(f"Proxy-Authenticate: {challenge}")
    auth_header = build_auth_header(
        status_code=407,
        username="+2348001234567",
        realm=params["realm"],
        password="demo-password",
        nonce=params["nonce"],
        method="INVITE",
        uri="sip:+2348001234567@example.com",
        algorithm=params.get("algorithm", "MD5"),
        qop=params.get("qop"),
    )
    auth_value = auth_header.split(": ", 1)[1]

    sdp = (
        "v=0\r\n"
        "o=- 0 0 IN IP4 157.240.19.130\r\n"
        "s=WhatsApp\r\n"
        "c=IN IP4 157.240.19.130\r\n"
        "t=0 0\r\n"
        "m=audio 3480 RTP/SAVP 111 126\r\n"
        "a=crypto:1 AES_CM_128_HMAC_SHA1_80 "
        "inline:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        "a=fmtp:111 maxaveragebitrate=20000;maxplaybackrate=16000;"
        "minptime=20;sprop-maxcapturerate=16000;useinbandfec=1\r\n"
        "a=rtpmap:126 telephone-event/8000\r\n"
        "a=ptime:20\r\n"
        "a=maxptime:20\r\n"
    )
    invite2 = SipMessage(
        first_line="INVITE sip:+2348001234567@example.com SIP/2.0",
        headers={
            "call-id": "demo-call-001",
            "from": "<sip:+1234567890@wa.meta.vc>;tag=demo1",
            "to": "<sip:+2348001234567@example.com>",
            "via": "SIP/2.0/TLS 127.0.0.1:5061",
            "cseq": "2 INVITE",
            "proxy-authorization": auth_value,
            "content-type": "application/sdp",
            "content-length": str(len(sdp)),
        },
        body=sdp,
    )
    resp2 = await server.handle_sip_message(invite2, peer)
    if resp2 is None or resp2.status_code != 200:
        print(f"  FAIL: expected 200 OK, got {resp2}")
        return False
    print(f"  <- {resp2.status_code} OK (session created)")
    print(f"  Active sessions: {len(server.active_sessions)}")

    # Verify session state
    session = server.active_sessions.get("demo-call-001")
    if session is None:
        print("  FAIL: session not found in active_sessions")
        return False
    print(f"  Session call_id: {session.call_id}")
    print(f"  Codec bridge: {type(session.codec_bridge).__name__}")
    print(f"  SRTP sender: {'yes' if session.srtp_sender else 'no'}")
    print(f"  SRTP receiver: {'yes' if session.srtp_receiver else 'no'}")
    print(f"  Remote media: {session.remote_media_addr}")

    # Step 3: BYE → expect 200 OK
    print("[3] Sending BYE...")
    bye = SipMessage(
        first_line="BYE sip:+2348001234567@example.com SIP/2.0",
        headers={
            "call-id": "demo-call-001",
            "from": "<sip:+1234567890@wa.meta.vc>;tag=demo1",
            "to": "<sip:+2348001234567@example.com>;tag=to1",
            "via": "SIP/2.0/TLS 127.0.0.1:5061",
            "cseq": "3 BYE",
            "content-length": "0",
        },
        body="",
    )
    resp3 = await server.handle_sip_message(bye, peer)
    if resp3 is None or resp3.status_code != 200:
        print(f"  FAIL: expected 200 OK, got {resp3}")
        return False
    print(f"  <- {resp3.status_code} OK (session terminated)")
    print(f"  Active sessions: {len(server.active_sessions)}")

    print("\n=== Demo PASSED: full call lifecycle verified ===")
    return True


def main() -> None:
    """Entry point."""
    success = asyncio.run(run_demo())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
