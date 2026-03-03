"""WhatsApp SIP + Gemini Live integration demo.

Exercises the full media pipeline with a real Gemini Live connection:
  1. SIP handshake (INVITE → 407 → auth → 200 OK)
  2. Session connects to Gemini Live API
  3. Sends silence (simulating caller) to trigger Gemini greeting
  4. Captures Gemini audio response frames
  5. BYE terminates session

Usage: python -m scripts.demo_wa_gemini
Requires: GOOGLE_API_KEY and WA_* vars in .env
"""

from __future__ import annotations

import asyncio
import os
import sys


async def run_demo() -> bool:
    """Run the SIP + Gemini Live demo. Returns True on success."""
    from dotenv import load_dotenv

    load_dotenv()

    from sip_bridge.sip_auth import build_auth_header, parse_challenge
    from sip_bridge.sip_tls import SipMessage
    from sip_bridge.wa_config import WhatsAppBridgeConfig
    from sip_bridge.wa_main import WaSIPServer

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set in .env")
        return False

    config = WhatsAppBridgeConfig.from_env()
    # Override gemini_api_key from main env (wa_config may not have it)
    if not config.gemini_api_key:
        config = WhatsAppBridgeConfig(
            sip_host=config.sip_host,
            sip_port=config.sip_port,
            sip_username=config.sip_username,
            sip_password=config.sip_password,
            sip_allowed_cidrs=config.sip_allowed_cidrs,
            tls_certfile=config.tls_certfile,
            tls_keyfile=config.tls_keyfile,
            sandbox_mode=config.sandbox_mode,
            gemini_api_key=api_key,
            live_model_id=config.live_model_id,
            system_instruction=config.system_instruction,
            gemini_voice=config.gemini_voice,
            company_id=config.company_id,
            tenant_id=config.tenant_id,
            health_port=config.health_port,
        )

    server = WaSIPServer(config=config, max_concurrent_calls=5)
    peer = ("127.0.0.1", 5061)

    print("=== WhatsApp SIP + Gemini Live Demo ===\n")

    # --- Step 1: SIP Handshake ---
    print("[1] SIP handshake...")
    invite1 = SipMessage(
        first_line=f"INVITE sip:{config.sip_username}@example.com SIP/2.0",
        headers={
            "call-id": "gemini-demo-001",
            "from": "<sip:+1234567890@wa.meta.vc>;tag=g1",
            "to": f"<sip:{config.sip_username}@example.com>",
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
    print(f"  <- {resp1.status_code} (challenge)")

    challenge = resp1.headers["proxy-authenticate"]
    params = parse_challenge(f"Proxy-Authenticate: {challenge}")
    auth_header = build_auth_header(
        status_code=407,
        username=config.sip_username,
        realm=params["realm"],
        password=config.sip_password,
        nonce=params["nonce"],
        method="INVITE",
        uri=f"sip:{config.sip_username}@example.com",
        algorithm=params.get("algorithm", "MD5"),
        qop=params.get("qop"),
    )
    auth_value = auth_header.split(": ", 1)[1]

    sdp = (
        "v=0\r\n"
        "o=- 0 0 IN IP4 127.0.0.1\r\n"
        "s=WhatsApp\r\n"
        "c=IN IP4 127.0.0.1\r\n"
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
        first_line=f"INVITE sip:{config.sip_username}@example.com SIP/2.0",
        headers={
            "call-id": "gemini-demo-001",
            "from": "<sip:+1234567890@wa.meta.vc>;tag=g1",
            "to": f"<sip:{config.sip_username}@example.com>",
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
        print(f"  FAIL: expected 200, got {resp2}")
        return False
    print(f"  <- {resp2.status_code} OK (session created)")

    session = server.active_sessions.get("gemini-demo-001")
    if session is None:
        print("  FAIL: session not found")
        return False

    print(f"  Codec: {type(session.codec_bridge).__name__}")
    print(f"  SRTP: sender={'yes' if session.srtp_sender else 'no'}, "
          f"receiver={'yes' if session.srtp_receiver else 'no'}")

    # --- Step 2: Wait for Gemini connection ---
    print("\n[2] Waiting for Gemini Live connection...")
    # Give the session's run() task time to connect to Gemini
    for i in range(30):  # up to 15 seconds
        await asyncio.sleep(0.5)
        if session.gemini_session is not None:
            break
    if session.gemini_session is None:
        print("  WARN: Gemini session not established (may need more time)")
        print("  Continuing anyway — will check for response frames...\n")
    else:
        print(f"  Gemini Live connected!")
        print(f"  Model: {config.live_model_id}")
        print(f"  Voice: {config.gemini_voice}")

    # --- Step 3: Send text prompt to trigger AI greeting ---
    # On a real call, the system instruction auto-greets on connection.
    # In demo mode, we send a text prompt to trigger the greeting since
    # VAD won't fire on silence.
    print("\n[3] Sending text prompt to trigger Gemini greeting...")
    if session.gemini_session is None:
        print("  FAIL: Gemini session not established — cannot send prompt")
        return False
    try:
        await session.gemini_session.send_client_content(
            turns=[{
                "role": "user",
                "parts": [{"text": "Hello, a customer just called."}],
            }],
            turn_complete=True,
        )
        print("  Text prompt sent — waiting for audio greeting...")
    except Exception as exc:
        print(f"  Send failed: {exc}")
        return False

    # --- Step 4: Wait for Gemini response ---
    # The session's _media_outbound_loop consumes from outbound_queue,
    # so we monitor session.frames_sent (incremented by that loop)
    print("\n[4] Waiting for Gemini audio response (up to 20s)...")
    print("  (monitoring session.frames_sent — outbound loop encodes + sends)")
    start_sent = session.frames_sent
    last_sent = start_sent

    for tick in range(40):  # 40 × 0.5s = 20s
        await asyncio.sleep(0.5)
        current = session.frames_sent
        if current > last_sent:
            delta = current - last_sent
            total = current - start_sent
            if total == delta:
                print(f"  First response frames detected! (+{delta})")
            else:
                print(f"  +{delta} frames (total: {total})")
            last_sent = current
        elif current > start_sent and current == last_sent:
            # Gemini stopped sending — done speaking
            print(f"  Gemini finished speaking.")
            break

    response_frames = session.frames_sent - start_sent
    if response_frames > 0:
        duration_est = response_frames * 0.02
        print(f"\n  Gemini responded: {response_frames} outbound frames "
              f"(~{duration_est:.1f}s of audio)")
    else:
        print("  No audio response detected")

    # --- Step 5: BYE ---
    print(f"\n[5] Sending BYE...")
    session.shutdown()  # signal session shutdown first
    bye = SipMessage(
        first_line=f"BYE sip:{config.sip_username}@example.com SIP/2.0",
        headers={
            "call-id": "gemini-demo-001",
            "from": "<sip:+1234567890@wa.meta.vc>;tag=g1",
            "to": f"<sip:{config.sip_username}@example.com>;tag=to1",
            "via": "SIP/2.0/TLS 127.0.0.1:5061",
            "cseq": "3 BYE",
            "content-length": "0",
        },
        body="",
    )
    resp3 = await server.handle_sip_message(bye, peer)
    bye_ok = resp3 is not None and resp3.status_code == 200
    print(f"  <- {resp3.status_code if resp3 else 'None'} {'OK' if bye_ok else 'UNEXPECTED'}")
    if not bye_ok:
        print(f"  WARN: BYE expected 200, got {resp3.status_code if resp3 else 'None'}")
    print(f"  Active sessions: {len(server.active_sessions)}")

    # --- Summary ---
    print("\n" + "=" * 50)
    print("  SIP handshake:     PASSED (407 → auth → 200)")
    print(f"  Gemini connected:  {'YES' if session.gemini_session else 'NO'}")
    print(f"  Response frames:   {response_frames}")
    print(f"  BYE teardown:      {'PASSED' if bye_ok else 'FAILED'}")
    print(f"  Session metrics:   recv={session.frames_received} "
          f"sent={session.frames_sent}")
    success = response_frames > 0 and bye_ok
    print(f"  Result:            {'PASSED' if success else 'PARTIAL'}")
    print("=" * 50)

    return success


def main() -> None:
    """Entry point."""
    success = asyncio.run(run_demo())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
