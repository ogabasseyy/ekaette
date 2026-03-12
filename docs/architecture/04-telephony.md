# Telephony & Channel Integration

> Part of [Ekaette System Architecture](../../Ekaette_Architecture.md)

## SIP Bridge Architecture (Reference Deployment)

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
                    T2["2. _media_inbound_loop<br/>RTP/SRTP parse → codec decode →<br/>PCM16 16kHz → denoise / gain normalization"]
                    T3["3. _gateway_bidi_loop<br/>PCM16 ↔ dedicated live voice service"]
                    T4["4. _media_outbound_loop<br/>PCM16 24kHz → codec encode → RTP send"]
                end
            end

            subgraph "Codec Layer"
                RTP["rtp.py<br/>RTPPacket parse/serialize,<br/>RTPTimer (20ms pacing)"]
                CODEC_B["codec_bridge.py<br/>Abstract CodecBridge,<br/>G711CodecBridge"]
                AUDIO["audio_codec.py<br/>G.711 μ-law ↔ PCM16,<br/>resample 8k↔16k↔24k"]
                SRTP_CTX["srtp_context.py<br/>SRTP protect/unprotect"]
            end

            subgraph "Voice Gateway & Call Control"
                GW_CLIENT["gateway_client.py<br/>GatewayClient WSS → live voice service<br/>reconnect + session tokens"]
                PREWARM["callback prewarm<br/>warm live session before outbound callback"]
                BYE["Explicit in-dialog SIP BYE<br/>after the acknowledgement and audio drain"]
            end
        end
    end

    subgraph "External"
        AT["Africa's Talking<br/>voice callback + SIP registrar"]
        MAIN_HTTP["Cloud Run main service<br/>AT/WA webhooks, XML call control,<br/>text channels, callback orchestration"]
        LIVE_HTTP["Cloud Run live voice service<br/>/ws only, Runner.run_live,<br/>voice ADK graph"]
        GEMINI["Vertex Gemini Live<br/>native audio"]
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
    PREWARM --> GW_CLIENT
    BYE --> SERVER

    REGISTER -->|"REGISTER/401/200"| AT
    MAIN_HTTP -->|"Dial XML / callback jobs"| AT
    T3 -->|"PCM16 via WSS"| GW_CLIENT
    GW_CLIENT -->|"WSS"| LIVE_HTTP
    LIVE_HTTP -->|"Live API"| GEMINI
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
    class AT,MAIN_HTTP,LIVE_HTTP,GEMINI,FSTORE external
```

---

## Split Telephony Control Plane + Shared AI Brain

The reference deployment uses a deliberate split:

```text
Main HTTP service:       Webhooks, XML call control, text channels, callback orchestration
Live voice service:      Long-lived /ws audio streams and Runner.run_live voice sessions
SIP bridge VM:           Codec conversion, denoise, callback prewarm, explicit BYE
```

This keeps one ADK agent brain for all channels while separating short-lived HTTP control traffic from long-lived voice media streams.

Key implementation:
- `voice.py` + `service_voice.py`: AT webhook returns `<Dial sip:...>` XML and manages callbacks
- `gateway_client.py`: WebSocket client connecting to the live voice service `/ws/{user_id}/{session_id}`
- `caller_phone` travels in the signed WS auth token → stored as `user:caller_phone` in ADK session state
- `send_whatsapp_message` is exposed only on voice-channel agents for during-call follow-up
- The direct-to-Gemini bridge remains as a fallback path, but the reference deployment keeps telephony on the split Cloud Run services

---

## Inbound Phone Call Flow

### Reference Deployment — AT Control Plane + SIP Media Plane

```mermaid
sequenceDiagram
    participant CALLER as Caller (Phone)
    participant ATC as Africa's Talking<br/>(voice callback)
    participant MAIN as Cloud Run main service<br/>(/api/v1/at/voice/callback)
    participant ATS as Africa's Talking<br/>(SIP registrar)
    participant SIP as SIPServer<br/>(GCE VM :6060)
    participant SESS as CallSession<br/>(4-task pipeline)
    participant GW as GatewayClient<br/>(WSS)
    participant LIVE as Cloud Run live voice service<br/>(ADK voice runtime)

    Note over CALLER,LIVE: SIP Registration (on startup)
    SIP->>ATS: SIP REGISTER (UDP)
    ATS->>SIP: 401 Unauthorized (nonce challenge)
    SIP->>SIP: Compute digest auth (MD5/SHA-256)
    SIP->>ATS: SIP REGISTER (with Authorization)
    ATS->>SIP: 200 OK (registered)
    Note over SIP,ATS: Re-registers at 80% of expiry

    Note over CALLER,LIVE: Inbound Call
    CALLER->>ATC: Dial <service-number>
    ATC->>MAIN: POST /api/v1/at/voice/callback (isActive=1)
    MAIN-->>ATC: XML Dial sip:...
    ATC->>ATS: Execute Dial to SIP endpoint
    ATS->>SIP: SIP INVITE (SDP: G.711 μ-law, port X)
    SIP->>ATS: 100 Trying
    SIP->>SIP: Allocate RTP port, extract caller phone from From header
    SIP->>SIP: Derive namespaced gateway IDs from tenant/company + caller/call context
    SIP->>SESS: Create CallSession + GatewayClient

    Note over SIP,LIVE: Live voice path comes up before the AT leg is answered
    SESS->>GW: connect() → WSS to live voice service /ws/{user_id}/{session_id}?token=...
    GW->>LIVE: WebSocket handshake
    LIVE-->>GW: session_started {sessionId}
    SIP->>ATS: 200 OK (SDP: G.711 μ-law, port Y)
    ATS->>SIP: ACK

    Note over CALLER,LIVE: Bidirectional Audio (4 concurrent tasks)

    par Task 1+2: Caller speaks
        CALLER->>ATC: Voice audio
        ATS->>SIP: RTP G.711 μ-law frames
        SESS->>SESS: G.711 decode → resample 8k→16k<br/>→ denoise / gain normalization
        SESS->>GW: send_audio(PCM16 16kHz)
        GW->>LIVE: Binary WebSocket frame
    and Task 3+4: AI responds
        LIVE->>GW: Binary WebSocket frame (PCM16 24kHz)
        GW->>SESS: GatewayFrame(audio)
        SESS->>SESS: Resample 24k→8k → G.711 encode → RTP
        SESS->>ATS: RTP audio frames
        ATS->>CALLER: Caller hears AI response
    end

    Note over SESS,LIVE: While the model speaks, the bridge suppresses loopback and can end callback legs with explicit SIP BYE after the acknowledgement and audio drain

    Note over CALLER,LIVE: Call Ends
    ATS->>SIP: SIP BYE or bridge originates BYE on callback legs
    SESS->>SESS: Shutdown signal → cancel all tasks
    GW->>LIVE: Close WebSocket
```

### Fallback Direct Mode (GATEWAY_MODE=false) — Hardcoded Prompt

```mermaid
sequenceDiagram
    participant CALLER as Caller (Phone)
    participant AT as Africa's Talking
    participant SESS as CallSession
    participant GEMINI as Gemini Live API

    Note over CALLER,GEMINI: Simplified — SIP registration and signaling same as above

    SESS->>GEMINI: Connect Gemini Live (dict config, 4-line system instruction)
    GEMINI-->>SESS: WebSocket connected

    Note over SESS,GEMINI: Proactive Greeting (Pipecat Pattern)
    SESS->>GEMINI: send_client_content("[Call connected]", turn_complete=True)
    GEMINI->>SESS: Audio response (PCM16 24kHz greeting)

    par Caller speaks
        CALLER->>AT: Voice audio
        AT->>SESS: RTP G.711 frames
        SESS->>GEMINI: send_realtime_input(audio=Blob, PCM16 16kHz)
    and AI responds
        GEMINI->>SESS: PCM16 24kHz audio chunks
        SESS->>AT: RTP audio frames
        AT->>CALLER: Caller hears AI response
    end

    Note over CALLER,GEMINI: Call Ends
    SESS->>GEMINI: Close WebSocket
```

> **Note:** Phone CallSession does not write Firestore call records (unlike WaSession which persists to `wa_calls`). Phone call persistence is planned but not yet implemented.

---

## WhatsApp Call Flow (Opus/SRTP)

### WhatsApp Voice — Shared Live Voice Service

```mermaid
sequenceDiagram
    participant WA as WhatsApp Caller
    participant WA_SIP as WaSipClient<br/>(GCE VM)
    participant WA_SESS as WaSession<br/>(4-task pipeline)
    participant GW as GatewayClient<br/>(WSS)
    participant LIVE as Cloud Run live voice service<br/>(ADK voice runtime)
    participant MAIN as Cloud Run main service<br/>(WhatsApp send + webhook control)
    participant FS as Firestore

    Note over WA,FS: WhatsApp Call Setup
    WA->>WA_SIP: WhatsApp call signaling
    WA_SIP->>WA_SIP: Negotiate Opus codec, SRTP keys (SDES)
    WA_SIP->>WA_SIP: Extract caller phone, mint opaque gateway user_id/session_id
    WA_SIP->>WA_SESS: Create WaSession + GatewayClient
    WA_SESS->>FS: Write call start record (wa_calls)
    WA_SESS->>GW: connect() → WSS to live voice service
    GW->>LIVE: WebSocket handshake
    LIVE-->>GW: session_started {sessionId}

    Note over WA,LIVE: Bidirectional Audio

    par Inbound: Caller speaks
        WA->>WA_SIP: SRTP Opus frames (UDP)
        WA_SESS->>WA_SESS: SRTP unprotect → Opus decode → PCM16 16kHz
        WA_SESS->>GW: send_audio(PCM16 16kHz)
        GW->>LIVE: Binary WebSocket frame
    and Outbound: AI responds
        LIVE->>GW: Binary WebSocket frame (PCM16 24kHz)
        GW->>WA_SESS: GatewayFrame(audio)
        WA_SESS->>WA_SESS: Opus encode → SRTP protect
        WA_SESS->>WA: SRTP Opus frames (UDP)
    end

    Note over WA,FS: Call Ends
    MAIN->>WA: WhatsApp follow-up / payment / utility messages
    GW->>LIVE: Close WebSocket
    WA_SESS->>FS: Write call end record
```

### Fallback Direct Mode (WA_GATEWAY_MODE=false) — Local Tool Handling

```mermaid
sequenceDiagram
    participant WA as WhatsApp Caller
    participant WA_SIP as WaSipClient
    participant WA_SESS as WaSession
    participant GEMINI as Gemini Live API
    participant FS as Firestore

    WA_SIP->>WA_SESS: Create WaSession(call_id, srtp_ctx, codec_bridge)
    WA_SESS->>FS: Write call start record (wa_calls)
    WA_SESS->>GEMINI: Connect Gemini Live (v1alpha, proactive_audio=True, SEND_WA_MESSAGE_TOOL)

    Note over WA_SESS,GEMINI: Proactive Greeting
    WA_SESS->>GEMINI: send_client_content("[Call connected]", turn_complete=True)
    GEMINI->>WA_SESS: Audio greeting (PCM16 24kHz)

    par Caller speaks
        WA->>WA_SESS: SRTP Opus → PCM16 16kHz
        WA_SESS->>GEMINI: send_realtime_input(audio=Blob)
    and AI responds
        GEMINI->>WA_SESS: PCM16 24kHz
        WA_SESS->>WA: Opus encode → SRTP → UDP
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
    SUB->>SUB: VISION_MODEL (Standard API)<br/>Structured analysis
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
