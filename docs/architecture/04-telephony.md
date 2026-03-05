# Telephony & Channel Integration

> Part of [Ekaette System Architecture](../../Ekaette_Architecture.md)

## SIP Bridge Architecture (GCE VM)

```mermaid
graph TB
    subgraph "GCE VM: ekaette-sip (<reserved-static-ip>)"
        subgraph "sip_bridge/ — Standalone Python Process"
            MAIN["main.py<br/>Entry point, signal handlers,<br/>config validation"]
            CONFIG["config.py<br/>BridgeConfig (frozen dataclass)<br/>All config from env vars"]

            subgraph "SIP Signaling Layer"
                SERVER["server.py<br/>SIPServer + SIPProtocol<br/>UDP :6060, session lifecycle"]
                REGISTER["sip_register.py<br/>SIP REGISTER client<br/>Periodic re-registration"]
                AUTH["sip_auth.py<br/>Digest auth (RFC 2617)<br/>MD5/SHA-256 challenge-response"]
                DIALOG["sip_dialog.py<br/>INVITE parser, SDP negotiation,<br/>G.711 SDP answer builder"]
            end

            subgraph "Media Pipeline (per call)"
                SESS["session.py — CallSession<br/>4-task asyncio.TaskGroup"]
                WA_S["wa_session.py — WaSession<br/>4-task asyncio.TaskGroup"]

                subgraph "4-Task Pattern"
                    T1["1. _media_recv_loop<br/>UDP recvfrom → inbound_queue"]
                    T2["2. _media_inbound_loop<br/>RTP parse → codec decode → PCM16 16kHz"]
                    T3["3. _gemini_bidi_loop<br/>PCM16 ↔ Gemini Live WebSocket"]
                    T4["4. _media_outbound_loop<br/>PCM16 24kHz → codec encode → RTP send"]
                end
            end

            subgraph "Codec Layer"
                RTP["rtp.py<br/>RTPPacket parse/serialize,<br/>RTPTimer (20ms pacing)"]
                CODEC_B["codec_bridge.py<br/>Abstract CodecBridge,<br/>G711CodecBridge"]
                AUDIO["audio_codec.py<br/>G.711 μ-law ↔ PCM16,<br/>resample 8k↔16k↔24k"]
                SRTP_CTX["srtp_context.py<br/>SRTP protect/unprotect"]
            end
        end
    end

    subgraph "External"
        AT["AT SIP Registrar<br/>(ng.sip.africastalking.com)"]
        GEMINI["Gemini Live API<br/>(v1alpha, native audio)"]
        FSTORE["Firestore<br/>(wa_calls collection)"]
    end

    MAIN --> CONFIG
    MAIN --> SERVER
    SERVER --> REGISTER
    REGISTER --> AUTH
    SERVER --> DIALOG
    SERVER -->|"creates per call"| SESS
    SERVER -->|"creates per call"| WA_S

    SESS --> T1
    SESS --> T2
    SESS --> T3
    SESS --> T4

    T1 --> RTP
    T2 --> CODEC_B
    CODEC_B --> AUDIO
    T4 --> RTP

    WA_S --> SRTP_CTX

    REGISTER -->|"REGISTER/401/200"| AT
    T3 -->|"PCM16 bidi"| GEMINI
    SESS --> FSTORE
    WA_S --> FSTORE

    classDef entry fill:#E3F2FD,stroke:#1565C0,stroke-width:2px
    classDef signaling fill:#FFF8E1,stroke:#F57F17,stroke-width:2px
    classDef media fill:#E1F5FE,stroke:#0277BD,stroke-width:2px
    classDef codec fill:#E8F5E9,stroke:#2E7D32,stroke-width:2px
    classDef external fill:#FCE4EC,stroke:#C62828,stroke-width:2px

    class MAIN,CONFIG entry
    class SERVER,REGISTER,AUTH,DIALOG signaling
    class SESS,WA_S,T1,T2,T3,T4 media
    class RTP,CODEC_B,AUDIO,SRTP_CTX codec
    class AT,GEMINI,FSTORE external
```

---

## Inbound Phone Call Flow (AT → SIP Bridge → Gemini)

```mermaid
sequenceDiagram
    participant CALLER as Caller (Phone)
    participant AT as Africa's Talking<br/>(SIP Registrar)
    participant SIP as SIPServer<br/>(GCE VM :6060)
    participant SESS as CallSession<br/>(4-task pipeline)
    participant GEMINI as Gemini Live API<br/>(v1alpha)
    participant FS as Firestore

    Note over CALLER,FS: SIP Registration (on startup)
    SIP->>AT: SIP REGISTER (UDP)
    AT->>SIP: 401 Unauthorized (nonce challenge)
    SIP->>SIP: Compute digest auth (MD5/SHA-256)
    SIP->>AT: SIP REGISTER (with Authorization)
    AT->>SIP: 200 OK (registered)
    Note over SIP,AT: Re-registers at 80% of expiry

    Note over CALLER,FS: Inbound Call
    CALLER->>AT: Dial <service-number>
    AT->>SIP: SIP INVITE (SDP: G.711 μ-law, port X)
    SIP->>AT: 100 Trying
    SIP->>SIP: Allocate RTP port (10000-20000)
    SIP->>SIP: Parse SDP, build G.711 SDP answer
    SIP->>AT: 200 OK (SDP: G.711 μ-law, port Y)
    AT->>SIP: ACK

    Note over SIP,FS: Session Setup
    SIP->>SESS: Create CallSession(call_id, codec, rtp_ports)
    SESS->>FS: Write call start record (wa_calls)
    SESS->>GEMINI: Connect Gemini Live (dict config)
    GEMINI-->>SESS: WebSocket connected

    Note over SESS,GEMINI: Proactive Greeting (Pipecat Pattern)
    SESS->>GEMINI: send_client_content("[Call connected]", turn_complete=True)
    GEMINI->>SESS: Audio response (PCM16 24kHz greeting)
    SESS->>SESS: Encode 24kHz → 8kHz → G.711 μ-law → RTP
    SESS->>AT: RTP audio frames (greeting)
    AT->>CALLER: Caller hears AI greeting

    Note over CALLER,GEMINI: Bidirectional Audio (4 concurrent tasks)

    par Task 1+2: Caller speaks
        CALLER->>AT: Voice audio
        AT->>SIP: RTP G.711 μ-law frames
        SESS->>SESS: G.711 decode → resample 8k→16k
        SESS->>GEMINI: send_realtime_input(audio=Blob, PCM16 16kHz)
    and Task 3+4: AI responds
        GEMINI->>SESS: PCM16 24kHz audio chunks
        SESS->>SESS: Resample 24k→8k → G.711 encode → RTP
        SESS->>AT: RTP audio frames
        AT->>CALLER: Caller hears AI response
    end

    Note over SESS,GEMINI: Echo Suppression
    SESS->>SESS: While model speaks: send SILENCE_FRAME<br/>to Gemini (not real mic audio).<br/>Holdoff 0.5s after model stops.

    Note over CALLER,FS: Call Ends
    AT->>SIP: SIP BYE
    SESS->>SESS: Shutdown signal → cancel all tasks
    SESS->>GEMINI: Close WebSocket
    SESS->>FS: Write call end record (duration, frames)
```

---

## WhatsApp Call Flow (Opus/SRTP → Gemini)

```mermaid
sequenceDiagram
    participant WA as WhatsApp Caller
    participant WA_SIP as WaSipClient<br/>(GCE VM)
    participant WA_SESS as WaSession<br/>(4-task pipeline)
    participant GEMINI as Gemini Live API<br/>(v1alpha)
    participant FS as Firestore

    Note over WA,FS: WhatsApp Call Setup
    WA->>WA_SIP: WhatsApp call signaling
    WA_SIP->>WA_SIP: Negotiate Opus codec, SRTP keys (SDES)
    WA_SIP->>WA_SESS: Create WaSession(call_id, srtp_ctx, codec_bridge)
    WA_SESS->>FS: Write call start record (wa_calls)
    WA_SESS->>GEMINI: Connect Gemini Live (v1alpha, proactive_audio=True)

    Note over WA_SESS,GEMINI: Proactive Greeting
    WA_SESS->>GEMINI: send_client_content("[Call connected]", turn_complete=True)
    GEMINI->>WA_SESS: Audio greeting (PCM16 24kHz)

    Note over WA,GEMINI: Bidirectional Audio

    par Inbound: Caller speaks
        WA->>WA_SIP: SRTP Opus frames (UDP)
        WA_SESS->>WA_SESS: SRTP unprotect → Opus decode → PCM16 16kHz
        WA_SESS->>GEMINI: send_realtime_input(audio=Blob, PCM16 16kHz)
    and Outbound: AI responds
        GEMINI->>WA_SESS: PCM16 24kHz audio chunks
        WA_SESS->>WA_SESS: Opus encode → SRTP protect
        WA_SESS->>WA: SRTP Opus frames (UDP)
    end

    Note over WA,FS: Call Ends
    WA_SESS->>GEMINI: Close WebSocket
    WA_SESS->>FS: Write call end record
```

---

## WhatsApp Text/Image → ADK Agent Graph

WhatsApp text and image messages now route through the full ADK agent hierarchy
via `app/channels/adk_text_adapter.py`. This gives WhatsApp users access to all
5 sub-agents (vision, valuation, booking, catalog, support), session state,
tools, and memory — the same capabilities as the voice WebSocket channel.

```mermaid
sequenceDiagram
    participant USER as WhatsApp User
    participant META as Meta Cloud API
    participant CR as Cloud Run<br/>(FastAPI)
    participant CT as Cloud Tasks
    participant ADAPTER as adk_text_adapter<br/>(app/channels/)
    participant RUNNER as ADK Runner<br/>(run_async)
    participant ROOT as ekaette_router<br/>(Root Agent)
    participant SUB as Sub-Agents<br/>(vision, valuation,<br/>booking, catalog, support)
    participant FS as Firestore<br/>(Sessions + State)

    Note over USER,FS: Inbound Text Message
    USER->>META: "I want to swap my iPhone 14"
    META->>CR: POST /whatsapp/webhook<br/>(HMAC verified)
    CR->>CT: Enqueue Cloud Task<br/>(deterministic task ID)
    CT->>CR: POST /whatsapp/process

    CR->>ADAPTER: send_text_message(user_id, text)
    ADAPTER->>FS: Get or create session<br/>(phone number → session ID)
    ADAPTER->>RUNNER: runner.run_async(new_message)
    RUNNER->>ROOT: Route to appropriate agent
    ROOT->>SUB: Transfer to valuation_agent
    SUB-->>ROOT: Trade-in assessment result
    ROOT-->>RUNNER: Text response
    RUNNER-->>ADAPTER: Collect text events
    ADAPTER-->>CR: {text, session_id, channel}
    CR->>META: WhatsApp reply message
    META->>USER: "I can help with that! Your iPhone 14..."

    Note over USER,FS: Inbound Image Message
    USER->>META: 📷 Photo of device + "Check this"
    META->>CR: POST /whatsapp/webhook
    CR->>CT: Enqueue Cloud Task
    CT->>CR: POST /whatsapp/process

    CR->>CR: Download image via Media API
    CR->>ADAPTER: send_image_message(image_bytes, caption)
    ADAPTER->>RUNNER: runner.run_async(image + text content)
    RUNNER->>ROOT: Route to vision_agent
    ROOT->>SUB: vision_agent → analyze_device_image_tool
    SUB->>SUB: Gemini 3 Flash (Standard API)<br/>Structured analysis
    SUB-->>ROOT: DeviceAnalysis result
    ROOT->>SUB: valuation_agent → grade_and_value_tool
    SUB-->>ROOT: Trade-in offer
    ROOT-->>RUNNER: Text response with valuation
    RUNNER-->>ADAPTER: Collect text events
    ADAPTER-->>CR: {text, session_id}
    CR->>META: WhatsApp reply
    META->>USER: "I can see an iPhone 14 Pro in Good condition..."
```

### Key Design Decisions

- **Session continuity**: Phone number → deterministic session ID via SHA-256 hash.
  Same user maintains multi-turn conversation state across messages.
- **Graceful fallback**: If ADK Runner is not initialized (early startup, tests),
  falls back to `bridge_text.py` (standalone Gemini, no agents).
- **Channel limits**: WhatsApp 4096 chars, SMS 160 chars — enforced by adapter.
- **No audio overhead**: Uses `Runner.run_async()` (text mode, `StreamingMode.NONE`)
  instead of `Runner.run_live()` (bidi streaming). Faster, cheaper.

---

## SMS Text Bridge Flow

SMS currently uses `bridge_text.py` (standalone Gemini). Future: route through
`adk_text_adapter` for full agent capabilities (same pattern as WhatsApp).

```mermaid
sequenceDiagram
    participant USER as User (Phone)
    participant AT as Africa's Talking
    participant CR as Cloud Run<br/>(FastAPI)
    participant GEMINI as Gemini<br/>(Text API)
    participant ANALYTICS as Campaign Analytics<br/>(In-memory)

    Note over USER,ANALYTICS: Inbound SMS → AI Response
    USER->>AT: SMS to virtual DID
    AT->>CR: POST /api/v1/at/sms/callback<br/>{from, to, text, id}
    CR->>CR: Deduplicate by message ID
    CR->>GEMINI: Bridge text to Gemini (text model)
    GEMINI->>CR: AI text response
    CR->>CR: Truncate to 160 chars
    CR->>AT: providers.send_sms(reply)
    AT->>USER: SMS reply
    CR->>ANALYTICS: Record inbound reply event

    Note over USER,ANALYTICS: Outbound SMS Campaign
    CR->>AT: POST providers.send_sms(recipients[], message)
    AT->>USER: Bulk SMS delivery
    CR->>ANALYTICS: Record sent/delivered/failed counts
```

---

## AT Channel Module Architecture

```mermaid
graph TB
    subgraph "app/api/v1/at/ — Africa's Talking Channel"
        direction TB
        INIT["__init__.py<br/>Router composition,<br/>AT SDK initialization"]

        subgraph "Voice"
            V_ROUTES["voice.py<br/>POST /voice/callback (AT webhook)<br/>POST /voice/call (outbound)<br/>POST /voice/campaign (bulk)<br/>POST /voice/transfer"]
            V_SVC["service_voice.py<br/>XML builder (Dial/Say),<br/>DID → tenant resolution,<br/>call lifecycle logging"]
        end

        subgraph "SMS"
            S_ROUTES["sms.py<br/>POST /sms/callback (AT webhook)<br/>POST /sms/send (outbound)<br/>POST /sms/campaign (bulk)"]
        end

        subgraph "Payments (Paystack)"
            P_ROUTES["payments.py<br/>POST /payments/paystack/initialize<br/>POST /payments/paystack/virtual-accounts<br/>POST /payments/paystack/webhook<br/>GET /payments/paystack/verify/{ref}"]
            P_SVC["service_payments.py<br/>Transaction lifecycle,<br/>VA provisioning,<br/>webhook HMAC verification,<br/>SMS/WhatsApp notifications"]
        end

        subgraph "Shipping (Topship)"
            SH_ROUTES["shipping.py<br/>POST /shipping/topship/quote<br/>POST /shipping/orders<br/>GET /shipping/orders/{id}/tracking"]
        end

        subgraph "Analytics"
            A_ROUTES["analytics_routes.py<br/>GET /analytics/overview<br/>GET /analytics/campaigns<br/>POST /analytics/events"]
            A_SVC["campaign_analytics.py<br/>In-memory campaign state,<br/>KPI computation,<br/>event deduplication"]
        end

        subgraph "WhatsApp"
            WA_ROUTES["whatsapp.py<br/>GET/POST /whatsapp/webhook<br/>POST /whatsapp/process<br/>POST /whatsapp/send"]
            WA_SVC["service_whatsapp.py<br/>ADK adapter routing,<br/>bridge_text fallback,<br/>service window, idempotency"]
        end

        subgraph "Infrastructure"
            SETTINGS["settings.py<br/>ATSettings (pydantic-settings)<br/>Feature flags, AT creds,<br/>Paystack keys, retention"]
            MODELS["models.py<br/>Request/response DTOs"]
            PROVIDERS["providers.py<br/>AT SDK wrappers,<br/>Paystack httpx client,<br/>WhatsApp Cloud API client"]
            HEALTH["health.py<br/>GET /health, GET /readiness"]
        end
    end

    subgraph "app/channels/ — Channel Adapters"
        ADAPTER["adk_text_adapter.py<br/>Runner.run_async() bridge,<br/>session bootstrap,<br/>text/image routing"]
    end

    INIT --> V_ROUTES
    INIT --> S_ROUTES
    INIT --> WA_ROUTES
    INIT --> P_ROUTES
    INIT --> SH_ROUTES
    INIT --> A_ROUTES
    INIT --> HEALTH

    V_ROUTES --> V_SVC
    V_ROUTES --> PROVIDERS
    V_ROUTES --> A_SVC
    S_ROUTES --> PROVIDERS
    S_ROUTES --> A_SVC
    WA_ROUTES --> WA_SVC
    WA_SVC -->|"ADK path"| ADAPTER
    WA_SVC -->|"Fallback"| PROVIDERS
    P_ROUTES --> P_SVC
    P_SVC --> PROVIDERS
    P_SVC --> A_SVC

    classDef router fill:#E3F2FD,stroke:#1565C0,stroke-width:2px
    classDef service fill:#E8F5E9,stroke:#2E7D32,stroke-width:2px
    classDef infra fill:#FFF8E1,stroke:#F57F17,stroke-width:2px
    classDef adapter fill:#E1F5FE,stroke:#0277BD,stroke-width:2px

    class V_ROUTES,S_ROUTES,WA_ROUTES,P_ROUTES,SH_ROUTES,A_ROUTES,HEALTH router
    class V_SVC,WA_SVC,P_SVC,A_SVC service
    class SETTINGS,MODELS,PROVIDERS,INIT infra
    class ADAPTER adapter
```
