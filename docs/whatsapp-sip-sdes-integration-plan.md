# WhatsApp Business Calling — SIP/SDES Integration Plan (V4.2)

## Context

Ekaette has 3 voice/messaging channels:
1. **Browser voice** — AudioWorklet PCM16 → WebSocket → Gemini Live (working)
2. **AT Phone voice** — AT Voice → SIP bridge (G.711→PCM16) → Gemini Live (built)
3. **AT SMS** — AT SMS → Gemini text bridge → AT SMS reply (working, deployed)

Meta's WhatsApp Business Calling API (July 2025) enables a 4th channel. After research, the **SIP/SDES path** was chosen over WebRTC because:
- Ekaette already has a SIP bridge on a VM
- SIP+SDES avoids ICE/DTLS/PeerConnection complexity
- Simpler signaling (standard SIP INVITE/200/BYE)
- Less new code, lower risk

**Key constraint**: Even with SIP signaling, WhatsApp requires **Opus codec** (not G.711) and **SRTP encryption** (not plain RTP). So the SIP bridge needs codec + transport upgrades, not just routing config.

**CRITICAL (from official Meta docs)**: "When SIP is enabled, you cannot use calling related Graph API endpoints and calling related webhooks are not sent." This means:
- **NO call webhooks** sent to Cloud Run (no `connect`, `status`, `terminate` events)
- **NO Graph API** `accept`/`pre_accept`/`terminate` endpoints available
- **Pure SIP signaling**: INVITE/200 OK/BYE handles entire call lifecycle
- **Simplifies architecture**: Cloud Run does NOT need a call webhook handler; SIP bridge handles everything

### 2026 Library Audit (verified)

| Concern | Choice | Why |
|---------|--------|-----|
| Opus codec | `opuslib_next` >=1.1.4 | Maintained fork, Python 3.13, native multi-rate decode/encode. Original `opuslib` dead since 2018. |
| SRTP | `pylibsrtp` v1.0.0+ | aiortc team, healthy, 22k weekly downloads, no vulns. |
| SIP client | stdlib `asyncio` + `ssl` | `aiosip` is DEPRECATED. PJSIP/Sippy are overkill (carrier-grade B2BUA). Minimal SIP UA (INVITE/407/200/BYE) needs ~200 LOC, not a framework. |
| SIP digest auth | stdlib `hashlib` | MD5/SHA-256 digest computation per RFC 2617/7616. No external dep needed. |

---

## Architecture

```text
Cloud Run (main FastAPI) — NO WhatsApp call webhooks (SIP mode disables them)
  ├── /api/v1/at/          — AT callbacks (existing)
  ├── /ws/                 — Browser voice (existing)
  └── /api/                — HTTP API (existing)

GCE VM (SIP Bridge — shared by AT + WhatsApp)
  ├── python -m sip_bridge.main      — AT calls (UDP :6060, G.711/RTP)
  └── python -m sip_bridge.wa_main   — WA calls (TLS :5061, Opus/SRTP)
      ├── Inbound:  Meta INVITE → [407 digest auth] → 200 OK → media → BYE
      └── Outbound: INVITE → 407 → re-INVITE with auth → 200 OK → media → BYE
```

**Key architectural simplification**: With SIP enabled, Meta sends NO call webhooks and the Graph API `accept`/`pre_accept`/`terminate` endpoints are UNAVAILABLE. The SIP bridge handles the entire call lifecycle via SIP signaling. No `app/api/v1/wa/` package needed.

### Audio Pipeline Per Channel

| Channel | Inbound (to Gemini) | Outbound (from Gemini) |
|---------|-------------------|----------------------|
| WhatsApp | SRTP → **Opus decode @16kHz** → PCM16 16kHz | PCM16 24kHz → **resample to SDP-negotiated rate** → Opus encode → SRTP |
| AT Phone | RTP → G.711 8kHz → **decode → resample → PCM16 16kHz** | PCM16 24kHz → **resample → encode → G.711 8kHz** → RTP |
| Browser | PCM16 16kHz (direct) | PCM16 24kHz (direct) |

**Key insight**: libopus natively supports decoding/encoding at 8k/12k/16k/24k/48kHz. Decoder at 16kHz outputs PCM16 16kHz directly. Encoder rate is **SDP-negotiated** — Meta's SDP typically sets `maxplaybackrate=16000`, so encoder runs at 16kHz (requiring resample from Gemini's 24kHz output). If Meta ever negotiates a different rate, the encoder adapts automatically. Manual G.711 resampling only needed for AT phone path.

---

## WhatsApp SIP Technical Requirements (Official Meta Docs)

**Source**: `developers.facebook.com` — Cloud API Calling docs (Dec 2025 – Feb 2026)

| Requirement | Detail |
|-------------|--------|
| Codec | **Opus only**. G.711 in alpha for select partners, not GA. |
| Signaling (SIP) | SIP over TLS. Needs explicit enablement via `POST /<PHONE_NUMBER_ID>/settings`. |
| Media encryption | SDES SRTP (needs explicit enablement) — avoids ICE+DTLS complexity. |
| Authentication | SIP digest auth. Username=phone number, password=Meta-generated (retrieve via `GET /<PHONE_NUMBER_ID>/settings?include_sip_credentials=true`). |
| DTMF | RFC 4733, 8000 clock rate only. Injected into RTP stream. |
| Call lifecycle (SIP) | Pure SIP signaling: INVITE/200 OK/ACK starts call, BYE terminates. No webhook accept window, no Graph API terminate call. |
| Maiden SRTP | Business side MUST send first SRTP packet for both inbound and outbound calls (Meta requirement). |
| Gemini format | `audio/pcm` PCM16 — input 16kHz, output 24kHz (confirmed in codebase + Google AI docs). |

### SIP + SDES Configuration (official API, two steps)

**Step 1**: Enable calling + SIP

```json
POST /<PHONE_NUMBER_ID>/settings
{
  "calling": {
    "status": "ENABLED",
    "sip": {
      "status": "ENABLED",
      "servers": [{
        "hostname": "SIP_SERVER_HOSTNAME",
        "port": 5061
      }]
    }
  }
}
```

**Step 2**: Enable SDES (separate setting, default is DTLS)

```json
POST /<PHONE_NUMBER_ID>/settings
{
  "calling": {
    "srtp_key_exchange_protocol": "SDES"
  }
}
```

**Note**: "Meta still expects the business side to send the maiden SRTP packet for both user and business initiated calls" — our `wa_session.py` handles this with `_send_initial_srtp_packet()`.

### Fallback: Graph API + SDES (if SIP blocked)

Meta docs note: "You can use SDES instead of ICE+DTLS with Graph API + Webhook signaling." If SIP enablement is blocked/delayed, we can fall back to Graph API signaling + SDES media — same media codec path, different signaling. This would require adding `app/api/v1/wa/` webhook handlers (deferred to M4 if needed).

### Business-Initiated Calling Restrictions

- **NOT available in Nigeria** (also USA, Canada, Egypt, Vietnam)
- Requires user permission first (via permission request message or callback)
- Temporary permissions: 7 days. Permanent permissions: user-granted.
- Max 100 connected calls per 24h per business number (not per user pair — this is a business-number-level concurrency limit; verify exact scope in sandbox)
- 4 consecutive unanswered calls → permission auto-revoked

### User-Initiated Call Flow (SIP mode — official)

1. User calls business WhatsApp number from WhatsApp client
2. Meta sends **SIP INVITE** to our SIP bridge (TLS, port 5061) — NO webhook sent
3. SIP bridge challenges with **407 digest auth** (MANDATORY — see Security Hardening)
4. Meta re-sends INVITE with `Proxy-Authorization` header
5. SIP bridge responds **200 OK** with SDP answer (Opus/SDES)
6. Meta sends **ACK** → media starts flowing
7. Media flows: SRTP (Opus) ↔ SIP bridge (PCM16) ↔ Gemini Live
8. On hangup: SIP **BYE** from either side terminates the call
9. SIP bridge logs call state to Firestore directly (no webhook dedup needed)

**Key differences from Graph API mode**: No webhooks. No accept/terminate API calls. Pure SIP.

### SIP Dialog State Machine & Idempotency

The SIP bridge MUST implement a proper dialog state machine per RFC 3261 to handle edge cases:

**Dialog states**: `IDLE → INVITED → EARLY → CONFIRMED → TERMINATED`

| Scenario | Behavior |
|----------|----------|
| **Duplicate INVITE** (retransmission) | If in `INVITED`/`EARLY` state, resend last provisional/final response. Do NOT create a new session. |
| **CANCEL before 200 OK** | Respond `200 OK` to CANCEL, then `487 Request Terminated` to original INVITE. Tear down session. |
| **Late 200 OK after CANCEL** | If we already sent 487, ignore. If Meta sends ACK after our 200, send BYE immediately. |
| **Duplicate BYE** | Respond `200 OK` again. Do NOT write duplicate Firestore termination record. |
| **ACK timeout** (no ACK after 200 OK) | App-level watchdog (10s). Over TLS, RFC 3261 Timer H does not apply (reliable transport). If no ACK within 10s, tear down session, log error. |
| **Re-INVITE** (mid-dialog) | Reject with `488 Not Acceptable Here` (no mid-call codec renegotiation in M1-M3). |

**Call ID resolution** (canonical key for Firestore + in-memory state):
- Primary: `x-wa-meta-wacid` custom header (Meta's WhatsApp call ID) — present on INVITE and BYE
- Fallback: SIP `Call-ID` header (RFC 3261, always present on every SIP message)
- Resolution: `call_id = headers.get("x-wa-meta-wacid") or headers["call-id"]`
- Once resolved at INVITE time, the chosen ID is stored on the dialog and reused for all subsequent messages (BYE, Firestore writes) — never re-resolved mid-dialog
- **SIP header normalization**: `sip_tls.py` parser MUST normalize all header names to lowercase on parse (SIP headers are case-insensitive per RFC 3261 §7.3.1). Also handle compact forms: `i` → `call-id`, `f` → `from`, `t` → `to`, `v` → `via`, `m` → `contact`, `l` → `content-length`. All downstream code accesses headers by lowercase key only.

**Firestore write guards**:
- Use resolved `call_id` as Firestore document ID for natural idempotency
- Call start: `set()` with `merge=True` — safe on retransmission
- Call end: conditional update only if `status != "terminated"` — prevents duplicate termination records
- State transitions are atomic: check-and-set in a single Firestore transaction

**Implementation**: `wa_sip_client.py` tracks dialog state as an enum. All SIP message handlers check current state before acting. State transitions are logged at DEBUG level for troubleshooting.

### Business-Initiated Call Flow (SIP mode — official)

1. SIP bridge sends **SIP INVITE** to `sip:+{USER_PHONE}@wa.meta.vc;transport=tls`
2. Meta responds **407 Proxy Authentication Required** with digest challenge
3. SIP bridge re-sends INVITE with `Proxy-Authorization` (username=business phone, password=Meta-generated)
4. Meta responds **200 OK** with SDP answer → media starts
5. Either side sends **BYE** to terminate

**Not needed for M1-M3** (Nigeria can't do outbound). Documented for future use.

### Custom SIP Headers (official)

| Header | Present On | Description |
|--------|-----------|-------------|
| `x-wa-meta-wacid` | INVITE (inbound), BYE | WhatsApp call ID — preferred for call state tracking. If absent, fall back to SIP `Call-ID`. |
| `x-wa-meta-call-duration` | BYE | Call duration in seconds |
| `x-wa-meta-cta-payload` | INVITE (inbound) | Business-specified payload from call button |
| `x-wa-meta-deeplink-payload` | INVITE (inbound) | Business-specified payload from deep link |

### Actual SDP from Meta (SDES mode, official sample)

```
m=audio 3480 RTP/SAVP 111 126
c=IN IP4 157.240.19.130
a=crypto:*****(SDES key material)*****
a=rtpmap:111 opus/48000/2
a=fmtp:111 maxaveragebitrate=20000;maxplaybackrate=16000;minptime=20;sprop-maxcapturerate=16000;useinbandfec=1
a=rtpmap:126 telephone-event/8000
a=maxptime:20
a=ptime:20
```

**Key SDP observations**:
- `RTP/SAVP` profile (SRTP with SDES, NOT `UDP/TLS/RTP/SAVPF` which is DTLS)
- Opus at 48000/2 (stereo) but `maxplaybackrate=16000` + `sprop-maxcapturerate=16000` → effectively 16kHz
- Telephone-event at 8000 (DTMF)
- `ptime=20` (20ms frames)
- Payload type 111 for Opus (confirmed from real Meta SDP)

### TLS Certificate Requirements (official)

- Meta presents valid cert for subject `wa.meta.vc`
- Our SIP server MUST have valid TLS cert covering our configured hostname
- Meta does NOT support mTLS (if we request client cert, Meta presents one but with random hostname)
- Meta adds `transport=TLS` to request URI
- Verify cert with: `openssl s_client -quiet -verify_hostname {hostname} -connect {hostname}:5061`

### Security Hardening (MANDATORY for M2)

| Control | Implementation | Detail |
|---------|---------------|--------|
| **Digest auth** | `sip_auth.py` | MANDATORY on all inbound INVITEs. Challenge with 407 (proxy) or 401 (UAS) — use 407 by default for Meta interop. Reject unauthenticated requests. |
| **IP allowlist** | `wa_config.py` | Configurable via `WA_SIP_ALLOWED_CIDRS` env var (comma-separated CIDRs). **No defaults — must be explicitly set.** Behavior: if `WA_SANDBOX_MODE=true` and CIDRs empty → allow all + log WARN per connection (sandbox only). If `WA_SANDBOX_MODE` is false/absent and CIDRs empty → **refuse to start** (fail-closed). Reloaded on SIGHUP without restart. Dropped connections logged at WARN with source IP. |
| **SIP message size cap** | `sip_tls.py` | Max 64KB per SIP message (headers + SDP body). Reject oversized messages with `413 Request Entity Too Large`. Prevents memory exhaustion from malformed streams. |
| **Header count limit** | `sip_tls.py` | Max 100 headers per SIP message. Reject with `400 Bad Request` if exceeded. |
| **Content-Length validation** | `sip_tls.py` | Reject if `Content-Length` header is missing on requests with body, or if declared length exceeds size cap. |
| **Rate limiting** | `wa_main.py` | Max 10 concurrent calls per bridge instance. Max 5 new INVITEs/second (token bucket). Reject excess with `503 Service Unavailable`. |
| **Abnormal dialog throttle** | `wa_sip_client.py` | If >3 failed auth attempts from same key in 60s, block for 2 minutes. **Key**: `(source_ip, auth_username)` compound — avoids blocking all calls behind shared Meta egress IP. **Counting**: Only requests bearing `Proxy-Authorization`/`Authorization` credentials that fail validation. An initial unauthenticated INVITE (normal challenge flow) is NOT a failed attempt. 2-minute decay, not 5, to limit blast radius. |
| **TLS minimum version** | `sip_tls.py` | TLS 1.2+ only (`ssl.TLSVersion.TLSv1_2`). No SSLv3/TLS 1.0/1.1. |

**Meta IP ranges**: No hardcoded defaults. During M3 sandbox testing (`WA_SANDBOX_MODE=true`, CIDRs empty), inspect source IPs of actual SIP INVITEs from Meta. Set confirmed CIDRs in `WA_SIP_ALLOWED_CIDRS` before disabling sandbox mode. SIGHUP-reloadable so new ranges can be added without restart. Every dropped connection logs source IP at WARN level — monitor to detect Meta range changes early.

### Sandbox Testing

- Public test numbers + sandbox accounts available
- Relaxed limits: 25 call permissions/day, 100/week
- No 2000-message-limit requirement for test numbers
- Calling disabled by default on test numbers — must enable via settings API

---

## CodecBridge Abstraction

The core new abstraction that makes sessions channel-agnostic.

**File**: `sip_bridge/codec_bridge.py`

```python
class CodecBridge(abc.ABC):
    def decode_to_pcm16_16k(self, encoded: bytes) -> bytes: ...
    def encode_from_pcm16_24k(self, pcm16_24k: bytes) -> bytes: ...
    rtp_payload_type: int   # From SDP a=rtpmap: (not hardcoded)
    rtp_clock_rate: int     # From SDP a=rtpmap:
    frame_duration_ms: int  # 20

class G711CodecBridge(CodecBridge):
    # Wraps existing audio_codec.py functions (pure Python, no audioop dep)
    # G.711 μ-law 8kHz → resample → PCM16 16kHz/24kHz

class OpusCodecBridge(CodecBridge):
    # Encoder/decoder rates derived from remote SDP fmtp, NOT hardcoded
    # Decoder(fs=16000) → outputs PCM16 16kHz directly (Gemini input rate)
    # Encoder rate from SDP maxplaybackrate (typically 16000 for WA)
    #   → Gemini outputs 24kHz, resample to encoder rate if different
    # libopus internally stores at 48kHz, decodes/encodes at requested rate
    encode_rate: int  # From SDP fmtp maxplaybackrate (e.g., 16000)
```

**RTP payload type**: Parsed from SDP `a=rtpmap:` line per session, not hardcoded. Opus is typically `111` but must be SDP-negotiated for interop.

### Existing `session.py` Refactor

```python
# BEFORE: hardcoded codec
class CallSession:
    call_id: str
    tenant_id: str
    company_id: str

# AFTER: injected codec
class CallSession:
    call_id: str
    tenant_id: str
    company_id: str
    codec_bridge: CodecBridge  # NEW — injected by server
```

`server.py` passes `G711CodecBridge()` on AT SIP INVITE.

---

## New SIP Bridge Modules

### `sip_bridge/srtp_context.py` — SDES/SRTP

- Parse `a=crypto:` lines from SDP → extract SRTP keys
- Wrap `pylibsrtp`: `protect(rtp) → srtp`, `unprotect(srtp) → rtp`
- No ICE/DTLS needed with SDES

### RTP/SRTP Timing & DTMF Requirements

**RTP timestamps** (critical for Opus interop):
- Opus RTP clock rate is **48000** regardless of actual audio bandwidth (RFC 7587)
- Timestamp increment per 20ms frame = `48000 × 0.020 = 960`
- Sequence numbers increment by 1 per packet
- SSRC must be consistent for the duration of a call (random 32-bit, set at session start)
- `wa_session.py` uses `RTPTimer` from `rtp.py` for 20ms frame pacing (timing only — clock-rate-agnostic)
- **RTP timestamp increment** computed from `codec_bridge.rtp_clock_rate * codec_bridge.frame_duration_ms / 1000` (= 960 for Opus 48kHz/20ms). Lives in `wa_session.py`, NOT in `rtp.py`. `rtp.py` is NOT modified — its `SAMPLES_PER_FRAME=160` constant remains G.711-specific and is only used by existing AT call paths.

**DTMF (RFC 4733)**:
- Telephone-event payload type from SDP `a=rtpmap:126 telephone-event/8000`
- DTMF packets use clock rate **8000** (NOT 48000), separate from Opus stream
- 3 duplicate packets per event (start + 2 retransmissions with End bit)
- **Dedup**: Track `(timestamp, event_code)` tuples. Only forward to Gemini on first packet of each event (where End bit = 0 and no prior packet with same timestamp+event). Subsequent packets with same timestamp are retransmissions — drop silently. Forward one `"DTMF: {digit}"` text per unique keypress.
- Outbound DTMF: not needed for M1-M3 (Gemini doesn't generate DTMF)

**Tests required**:
- RTP timestamp monotonicity across 100+ packets
- Timestamp increment = 960 per 20ms frame for Opus (48kHz clock)
- SSRC consistency within a session
- DTMF event parsing (start/end bit, duration, digit extraction)
- DTMF packets with telephone-event/8000 clock rate (not Opus 48k)

### `sip_bridge/sip_auth.py` — Digest Auth

- Parse **both** 407 `Proxy-Authenticate` and 401 `WWW-Authenticate` challenges (Meta typically sends 407, but RFC 3261 allows either; other SIP endpoints may use 401)
- Generate matching response header: `Proxy-Authorization` for 407, `Authorization` for 401
- Support algorithm negotiation from challenge (MD5 default per RFC 2617, SHA-256 per RFC 7616 if offered)
- Pure stdlib (`hashlib`)

### `sip_bridge/sip_tls.py` — TLS Transport

- `asyncio.open_connection(host, port, ssl=ctx)` to `wa.meta.vc:5061`
- **Strict TCP stream-parsing state machine**: read lines until `\r\n\r\n`, extract `Content-Length` from headers, then `readexactly(content_length)` for SDP body. Never assume one `read()` = one SIP message.
- Python stdlib only

### `sip_bridge/wa_sip_client.py` — WhatsApp SIP UA

Two call flows:
- **Outbound**: INVITE → 407 → re-INVITE with auth → 200 OK → media
- **Inbound**: receive INVITE → **407 challenge** → receive re-INVITE with auth → validate credentials → 200 OK → media (auth is MANDATORY per Security Hardening)

Generates SDP with Opus codec + SDES crypto attribute. Handles BYE for termination.

### `sip_bridge/wa_session.py` — WhatsApp Call Session

Same 3-task TaskGroup pattern as `session.py`:
1. `_media_inbound_loop`: SRTP unprotect → Opus decode → PCM16 → Gemini
2. `_gemini_bidi_loop`: Gemini Live WebSocket (shared client)
3. `_media_outbound_loop`: PCM16 → Opus encode → SRTP protect → send

Plus `_send_initial_srtp_packet()` (WhatsApp requirement).

### ~~`sip_bridge/wa_graph_api.py`~~ — REMOVED

**NOT NEEDED with SIP mode.** When SIP is enabled, Graph API call endpoints (`accept`/`pre_accept`/`terminate`) are UNAVAILABLE. Call lifecycle is entirely SIP (INVITE/200/BYE). Call state is logged to Firestore directly from the SIP bridge.

### `sip_bridge/wa_config.py` — Config

`WhatsAppBridgeConfig` (frozen dataclass, env vars, `WA_*` prefix). Same pattern as existing `BridgeConfig`.

### `sip_bridge/wa_main.py` — Entry Point

`python -m sip_bridge.wa_main`

---

## Cloud Run Control Plane — SIMPLIFIED

**`app/api/v1/wa/` is NOT needed for call handling** (SIP mode disables call webhooks).

The entire WhatsApp voice call lifecycle lives on the SIP bridge VM. Cloud Run only needs:
- Existing endpoints (AT callbacks, browser WebSocket, HTTP API)
- Optional future: `account_settings_update` webhook for monitoring SIP config changes

**Call state**: The SIP bridge writes Firestore call records directly (call_id from `x-wa-meta-wacid` header with SIP `Call-ID` fallback, start/end timestamps, duration from `x-wa-meta-call-duration`).

---

## Resampling Strategy

**WhatsApp (Opus)**: Decoder runs at fixed 16kHz (Gemini input). Encoder rate is **SDP-derived**:
- `Decoder(fs=16000, channels=1)` → decodes any Opus packet directly to PCM16 16kHz
- `Encoder(fs=encode_rate, channels=1)` where `encode_rate` = remote SDP `maxplaybackrate` (typically 16000)
- If `encode_rate` ≠ 24000 (Gemini output rate), `OpusCodecBridge.encode_from_pcm16_24k()` resamples 24kHz→encode_rate using **linear interpolation** (same method as existing `audio_codec.py`). This is the locked choice — simple, deterministic, <0.1ms per 20ms frame, and acceptable for voice (not music). No external resampler dependency.
- **Rule**: Never hardcode 24kHz as encoder rate. Always parse from SDP fmtp. If fmtp absent, default to 16000 (Meta's standard).
- **Test**: roundtrip resample 24k→16k→24k must preserve speech intelligibility (SNR >20dB on synthetic sine wave).

**AT Phone (G.711)**: Existing manual resampling in `audio_codec.py` stays as-is:
- `resample_8k_to_16k` (linear interpolation) — acceptable for 8kHz telephony audio
- `resample_24k_to_8k` (decimation) — acceptable given G.711's 4kHz bandwidth limit

**No new resampling functions needed.** The `audio_codec.py` file is NOT modified.

**Note**: `audio_codec.py` uses pure Python lookup tables + `struct.pack`, NOT the removed `audioop` stdlib module. No `audioop-lts` dependency required.

---

## Dependencies

### Python packages (`requirements-sip.txt`, separate runtime)

```
opuslib-next==1.1.4           # Opus codec — pinned, tested. Maintained fork, Python 3.13.
pylibsrtp==1.0.0              # SRTP — pinned, tested. aiortc team, wraps libsrtp2.
google-cloud-firestore==2.23.0  # Firestore — ALIGNED with main backend requirements.txt
google-genai==1.64.0          # Gemini Live API — ALIGNED with main backend requirements.txt
websockets==15.0.1            # WebSocket — ALIGNED with main backend requirements.txt
```

**Pinning rationale**: Telephony stack must not drift between deploys. Shared deps (Firestore, genai, websockets) are pinned to the SAME versions as the main backend `requirements.txt` to avoid API/behavior drift. SIP-specific deps (opuslib-next, pylibsrtp) are pinned to tested releases. Bump deliberately with re-test, never auto-upgrade.

**Note**: `opuslib` (original) is dead — last release Jan 2018, Python 2.x only. `opuslib_next` is the actively maintained fork with Python 3.13 support. `opuspy` (ElevenLabs) is file-I/O only, not real-time streaming.

### System packages (Dockerfile)

```
libopus0 libsrtp2-1
```

---

## File Layout Summary

### New files (11)

```
sip_bridge/
  codec_bridge.py           # CodecBridge ABC + G711 + Opus implementations
  srtp_context.py           # SDES parsing + pylibsrtp wrapper
  sip_auth.py               # SIP digest auth (RFC 2617/7616)
  sip_tls.py                # Async TLS TCP transport
  wa_sip_client.py          # WhatsApp SIP UA (INVITE/407/200/BYE)
  wa_session.py             # WhatsApp call session (Opus/SRTP/Gemini)
  wa_config.py              # WhatsApp bridge config
  wa_main.py                # Entry point

requirements-sip.txt          # SIP bridge runtime dependencies (pinned)

scripts/
  check_wa_architecture.py  # Architecture enforcement
```

### Deploy artifacts (modified)
- `Dockerfile` or VM deploy script: add `apt-get install libopus0 libsrtp2-1` for system packages

**Removed** (not needed with SIP mode):
- ~~`app/api/v1/wa/`~~ — SIP disables call webhooks, no webhook handler needed
- ~~`wa_graph_api.py`~~ — SIP disables Graph API call endpoints

### Modified files (2)

```
sip_bridge/session.py       # Add codec_bridge parameter
sip_bridge/server.py        # Pass G711CodecBridge on INVITE
```

(Note: `main.py` does NOT need modification — no wa_router to mount)
(Note: `audio_codec.py` is NOT modified — Opus native resampling eliminates the need)

---

## Architecture Enforcement (`scripts/check_wa_architecture.py`)

### Import & Size Rules
- `sip_bridge/` must NOT import from `app.*`
- `sip_bridge/wa_*.py` files ≤ 400 LOC each
- `codec_bridge.py` ≤ 250 LOC, `srtp_context.py` ≤ 200 LOC
- Add to CI pipeline

### State-Boundary Ownership
Each module owns a specific concern. Cross-boundary access is forbidden:

| Module | Owns | Must NOT touch |
|--------|------|---------------|
| `wa_sip_client.py` | SIP dialog state (IDLE→TERMINATED), SIP message parsing/generation | Codec, SRTP keys, Gemini, Firestore |
| `wa_session.py` | Media pipeline (encode/decode/Gemini loops), Firestore call records | SIP signaling, TLS transport |
| `srtp_context.py` | SRTP protect/unprotect, SDES key parsing | SIP headers, codecs, Gemini |
| `codec_bridge.py` | Codec encode/decode, resampling | SIP, SRTP, network I/O |
| `sip_tls.py` | TLS connection, TCP stream framing, security limits | SIP semantics, dialog state |
| `sip_auth.py` | Digest auth computation | SIP transport, dialog state |

**Enforcement**: `check_wa_architecture.py` verifies these boundaries by scanning imports and attribute access patterns. Violations fail CI.

### SIP Layer Contracts
- `sip_tls.py` exposes: `connect()`, `send(bytes)`, `recv() -> SipMessage`, `close()`
- `wa_sip_client.py` exposes: `handle_invite(SipMessage) -> WaSession`, `send_bye(call_id)`, `dialog_state(call_id) -> DialogState`
- `wa_session.py` exposes: `start()`, `stop()`, `is_active -> bool`
- No module may bypass these interfaces (e.g., `wa_session` must NOT directly read from the TLS socket)

---

## Testing Strategy

### Everything testable offline (no Meta production needed)

| Component | Test Approach | Target Tests |
|-----------|--------------|-------------|
| CodecBridge (G.711 + Opus) | Encode/decode roundtrip, size assertions | ~25 |
| SRTP Context | protect/unprotect roundtrip with generated keys | ~15 |
| SIP Digest Auth | Known-answer tests: RFC 2617 (MD5, 407), RFC 7616 (SHA-256), 401 WWW-Authenticate path | ~12 |
| SIP TLS Transport | Mock asyncio streams, size/header limits, TLS version | ~12 |
| WhatsApp SIP Client | Mock TLS transport, 407 flow, SDP parsing, dialog state machine | ~20 |
| SIP Dialog Idempotency | Duplicate INVITE/BYE, CANCEL flows, ACK timeout, re-INVITE reject | ~10 |
| RTP Timestamps | Monotonicity, 960-increment (Opus 48kHz), SSRC consistency | ~6 |
| DTMF Handling | RFC 4733 event parsing, 8kHz clock rate, digit extraction, dedup across retransmissions | ~7 |
| WhatsApp Session | Mock codec + mock Gemini + mock SRTP + maiden packet | ~10 |
| Security Controls | IP allowlist, rate limits, message size caps, auth throttle | ~8 |
| Architecture Invariants | Static analysis + state-boundary ownership | ~8 |
| **Total** | | **~135** |

**Removed**: Meta Webhook Security tests (~8) — not needed with SIP mode (no call webhooks).

### All existing tests (currently 36 SIP bridge, full backend suite) must continue passing after refactor. Do not gate on a fixed total count — run `pytest tests/ -v` and verify zero failures.

---

## Milestones

### M1: Codec + Crypto Spike

- `codec_bridge.py` — ABC + G711CodecBridge + OpusCodecBridge with native resampling (TDD)
- `srtp_context.py` — SDES parsing from SDP + pylibsrtp (TDD)
- `sip_auth.py` — digest auth with algorithm negotiation (TDD)
- Refactor `session.py` to accept `codec_bridge` parameter
- All 36 existing SIP bridge tests still pass
- **Exit**: ~56 new tests green (codec ~25, SRTP ~15, auth ~10, RTP timing ~6), Opus encode/decode verified at 16kHz/24kHz

### M2: SIP Client + Session + Call State

- `sip_tls.py` — async TLS transport with stream-parsing state machine (TDD)
- `wa_sip_client.py` — full SIP UA with SDP payload type parsing, 407 digest auth flow (TDD)
- `wa_session.py` — WhatsApp call session (TDD)
- `wa_config.py` + `wa_main.py`
- **Firestore call state** — SIP bridge writes call records directly (call_id from `x-wa-meta-wacid` with SIP `Call-ID` fallback, timestamps, duration from `x-wa-meta-call-duration`)
- `check_wa_architecture.py` + CI integration
- ~~`app/api/v1/wa/`~~ — NOT needed (SIP disables call webhooks)
- ~~Mount in `main.py`~~ — NOT needed (no wa_router)
- **Exit**: ~135 new tests green, architecture passes, security controls verified, call state persisted with idempotency guards

### M3: Sandbox E2E

- Deploy SIP bridge to VM with public IP + valid TLS cert
- Configure Meta SIP settings (Steps 4-6 from Meta Account Setup) pointing to VM hostname:5061
- Confirm Meta SIP egress CIDRs and set `WA_SIP_ALLOWED_CIDRS` env var (not a code edit — runtime config)
- Test with WhatsApp test number (sandbox)
- Verify call state records in Firestore
- **Exit**: Real WhatsApp call reaches Gemini, AI responds, state logged

### M4: Production Hardening

- Health/readiness endpoints
- Error recovery (TLS disconnect, SRTP errors)
- Call state TTL cleanup
- `.env.example` updated
- Demo script
- **Production deployment gate**: `wa_main.py` MUST refuse to start if `WA_SIP_ALLOWED_CIDRS` is empty/unset AND `WA_SANDBOX_MODE` is not true. Additionally, `check_wa_architecture.py` (CI) verifies that deploy manifests / Cloud Run configs do NOT set `WA_SANDBOX_MODE=true` — sandbox mode is local/VM-only and must never reach production infrastructure. If both `WA_SANDBOX_MODE=true` and a production-like environment is detected (e.g., `K_SERVICE` env var set by Cloud Run), log FATAL and refuse to start.
- **Exit**: Production-ready with monitoring, fail-closed security verified

---

## Key Reuse Points

| Existing Code | Reused For |
|---------------|-----------|
| `sip_bridge/audio_codec.py` | G711CodecBridge wraps existing functions |
| `sip_bridge/session.py` TaskGroup pattern | WhatsApp session structured concurrency |
| `sip_bridge/rtp.py` RTPPacket + RTPTimer | WhatsApp SRTP packet serialization + timing |
| `sip_bridge/config.py` BridgeConfig pattern | WhatsAppBridgeConfig (frozen dataclass, env vars) |
| `scripts/check_at_architecture.py` | Template for check_wa_architecture.py |

---

## Pricing

- **Inbound calls (user → business): FREE**
- **Outbound calls**: Per-minute billing (~$0.007/min, varies by country)
- **Outbound NOT available in Nigeria** — inbound only
- **Requires**: ≥2,000 messaging limit in 24h period

---

## Meta Account Setup

### Step 1: Create Meta Developer App
1. `developers.facebook.com` → "Create App" → "Business" type
2. Note App ID + App Secret

### Step 2: Add WhatsApp Product
1. App dashboard → "Add Product" → WhatsApp → "Set Up"
2. Note Phone Number ID + test phone number

### Step 3: System User Access Token
1. `business.facebook.com` → Settings → System Users
2. Create admin user → generate token with `whatsapp_business_messaging` + `whatsapp_business_management`

### Step 4: Enable Calling + SIP

```bash
curl -X POST "https://graph.facebook.com/{WA_GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/settings" \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "calling": {
      "status": "ENABLED",
      "sip": {
        "status": "ENABLED",
        "servers": [{
          "hostname": "YOUR_VM_PUBLIC_HOSTNAME",
          "port": 5061
        }]
      }
    }
  }'
```

### Step 5: Enable SDES (separate call)

```bash
curl -X POST "https://graph.facebook.com/{WA_GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/settings" \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "calling": {
      "srtp_key_exchange_protocol": "SDES"
    }
  }'
```

### Step 6: Retrieve SIP Credentials

```bash
curl "https://graph.facebook.com/{WA_GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/settings?include_sip_credentials=true" \
  -H "Authorization: Bearer {TOKEN}"
```

Response includes `sip_user_password` field. Store in env vars: `WA_SIP_USERNAME` (business phone number), `WA_SIP_PASSWORD` (Meta-generated).

### Step 7: Configure Webhook (optional, for account settings monitoring only)

With SIP enabled, **no call webhooks are sent**. You may still want to subscribe to `account_settings_update` field for monitoring SIP config changes.

~~Step 8: Accept/Terminate API Calls~~ — **NOT APPLICABLE with SIP mode.** Call lifecycle is entirely SIP signaling.

**Note**: Graph API version is env-configurable via `WA_GRAPH_API_VERSION` (not hardcoded).

### Source Reliability Note

Core requirements (SIP enablement API, SDES configuration, SIP call lifecycle, digest auth, call permissions, sandbox testing) are sourced from **official Meta developer docs** (`developers.facebook.com` — Cloud API Calling, Dec 2025 – Feb 2026). SDP-level details (Opus payload type, crypto line format) are cross-referenced with BSP documentation (360dialog, nimblea.pe). During M1 spike, verify SDP/SIP wire-format constraints by inspecting actual SIP INVITE from Meta sandbox.

---

## Explicit Non-Goals

- No WhatsApp messaging (text/media) — voice calling only
- No outbound calls (unavailable in Nigeria)
- No video calls (Meta doesn't support)
- No PSTN bridging (Meta prohibits)
- No DTLS/ICE (using SDES instead)
- No removal of existing SIP bridge (proven fallback for AT)
- No Pipecat/LiveKit dependency (overkill for hackathon)

---

## Verification

1. **Unit tests**: `pytest tests/test_codec_bridge.py tests/test_srtp_context.py tests/test_sip_auth.py tests/test_wa_*.py -v`
2. **Existing tests**: `pytest tests/test_sip_bridge_*.py -v` (all 36 still pass)
3. **Architecture**: `python -m scripts.check_wa_architecture`
4. **Full gate**: `pytest tests/ -v && cd frontend && pnpm exec vitest run`
5. **TLS cert**: `openssl s_client -quiet -verify_hostname {hostname} -connect {hostname}:5061`
6. **Manual E2E** (M3): WhatsApp test call → SIP INVITE → SIP bridge → Gemini → AI voice response
