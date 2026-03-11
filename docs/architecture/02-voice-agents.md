# Voice & Agent Flows

> Part of [Ekaette System Architecture](../../Ekaette_Architecture.md)

## Voice Conversation Flow (Dedicated Live Voice Service)

```mermaid
sequenceDiagram
    participant C as Voice Client<br/>(Browser or SIP Bridge)
    participant WS as /ws Gateway
    participant RT as Realtime Module<br/>(session_init + stream_tasks)
    participant LRQ as LiveRequestQueue
    participant RUN as Runner.run_live()
    participant ROOT as ekaette_router
    participant API as Vertex Gemini Live

    Note over C,API: Same live path serves browser voice directly and AT/WhatsApp voice via the SIP bridge GatewayClient
    C->>WS: Connect /ws/{user_id}/{session_id}?tenantId=...&companyId=...
    WS->>RT: session_init.initialize_session()
    RT->>RT: Validate origin, tenant, company
    RT->>RT: Resolve registry config (template + capabilities)
    RT->>RT: Get/Create ADK Session
    RT->>RT: Build RunConfig (BIDI, AUDIO, transcription,<br/>compression, Affective Dialog)
    RT->>LRQ: Create LiveRequestQueue()
    RT->>RUN: Start run_live(user_id, session_id, queue, config)
    RUN->>API: Connect to Live API (native audio model)
    RUN->>RUN: PreloadMemoryTool: fetch past memories

    Note over C,API: Bidi-Streaming (concurrent tasks)

    par upstream_task: Customer speaks
        C->>WS: Audio blob (PCM 16kHz)
        WS->>RT: receive audio
        RT->>LRQ: send_realtime(audio_blob)
        LRQ->>RUN: Buffered audio
        RUN->>ROOT: Process input
        ROOT->>API: Stream audio to Live API
    and downstream_task: Agent responds
        API->>ROOT: Streaming audio response (PCM 24kHz)
        ROOT->>RUN: yield Event (audio chunks)
        RUN->>RT: Event stream
        RT->>WS: Send audio binary frames
        WS->>C: Play audio via AudioWorklet
    and keepalive_task: Connection health
        RT->>C: Periodic ping (25s interval)
    and silence_nudge_task: Engagement & Filler
        RT->>RUN: Customer silence nudge (30s timeout)
        RT->>RUN: Response latency nudge (3s/15s filler during transfers)
    end

    Note over C,API: Customer interrupts (barge-in)
    C->>WS: New audio (interruption)
    API-->>ROOT: [cancelled generation discarded]
    ROOT->>API: Process new input immediately

    Note over C,API: Disconnect
    C->>WS: Disconnect
    RT->>LRQ: close()
    RUN->>API: Disconnect Live API
    RT->>RT: after_agent_callback: save to Memory Bank (async)
```

### Affective Dialog

The Live `RunConfig`'s `Affective Dialog` setting lets the native-audio model adapt prosody, pacing, and phrasing to the caller's emotional tone without changing routing, safety policy, or tool permissions.

In this architecture it should improve:
- warmer or calmer spoken delivery when the caller sounds frustrated or uncertain
- more natural acknowledgement language during transfers and callbacks
- smoother voice UX without changing business logic

It should not be treated as:
- a replacement for routing rules
- a safety or policy mechanism
- permission to improvise outside the configured agent and tool boundaries

---

## Multi-Agent Transfer Flow (Image During Voice Call)

```mermaid
sequenceDiagram
    participant C as Customer
    participant ROOT as ekaette_router<br/>(Live API Voice)
    participant VA as vision_agent<br/>(Gemini 3 Flash)
    participant VLA as valuation_agent<br/>(Gemini 3 Flash)
    participant FS as Firestore
    participant CS as Cloud Storage

    C->>ROOT: "I want to swap my phone"
    ROOT->>C: "Sure! Please send me photos of your device"

    C->>ROOT: [3 photos uploaded via WebSocket]
    ROOT->>CS: Store images in Cloud Storage
    ROOT->>ROOT: Detect intent: visual analysis needed

    Note over ROOT,VA: ADK Agent Transfer
    ROOT->>VA: Transfer with context + image references
    Note over ROOT: Audio transcription enabled automatically

    VA->>CS: Fetch images
    VA->>VA: Gemini 3 Flash Vision + Visual Thinking
    Note over VA: Zooms into screen scratch<br/>Crops corner dent<br/>Annotates damage areas
    VA->>VA: Result: iPhone 14 Pro, Good condition

    VA->>ROOT: Transfer back with analysis

    ROOT->>VLA: Transfer with vision results
    VLA->>FS: Load electronics pricing rubric
    VLA->>VLA: Grade: Screen 7/10, Body 6/10, Battery 8/10
    VLA->>VLA: Calculate: N185,000 trade-in value
    VLA->>ROOT: Transfer back with valuation

    ROOT->>C: "Your iPhone 14 Pro is in Good condition.<br/>I can offer N185,000 for the trade-in.<br/>Would you like to schedule a swap appointment?"

    C->>ROOT: "Yes, tomorrow afternoon"
    Note over ROOT: Routes to booking_agent...
```

---

## Learning Layer Flow (Memory Bank)

```mermaid
sequenceDiagram
    participant C as Customer
    participant ROOT as ekaette_router
    participant MB as Memory Bank<br/>(Agent Engine)
    participant GEMINI as Gemini<br/>(Extraction)

    Note over C,GEMINI: Session 1: New Customer
    C->>ROOT: "Hi, I want to swap my iPhone 14 Pro"
    ROOT->>MB: PreloadMemoryTool.search_memory()
    MB-->>ROOT: (no memories found)

    ROOT->>C: "Welcome! Send me photos of your device"
    Note over C,ROOT: ...vision, valuation, booking flow...
    ROOT->>C: "Booked! Pickup tomorrow at 10 AM at Lekki"

    Note over ROOT,GEMINI: After session ends (async)
    ROOT->>GEMINI: after_agent_callback: add_events_to_memory()
    GEMINI->>GEMINI: Extract key facts:<br/>Customer name: Chidi,<br/>Traded iPhone 14 Pro Good N185K,<br/>Prefers morning pickups,<br/>Located in Lekki
    GEMINI->>MB: Store memories (scoped to user_id)

    Note over C,GEMINI: Session 2: Returning Customer (days later)
    C->>ROOT: "Hi, I'm back!"
    ROOT->>MB: PreloadMemoryTool.search_memory()
    MB-->>ROOT: Memories:<br/>Chidi traded iPhone 14 Pro,<br/>Prefers morning pickups,<br/>Lekki location

    ROOT->>C: "Welcome back Chidi!<br/>Looking to swap something else today?"

    Note over C,GEMINI: Session 3: Updated info
    C->>ROOT: "I moved to Victoria Island now"
    ROOT->>MB: after_agent_callback
    MB->>GEMINI: Consolidation check
    GEMINI->>MB: UPDATE memory:<br/>Lekki -> Victoria Island<br/>(contradiction resolved)

    Note over C,GEMINI: Session 4: Cross-Channel Continuity (Phone → WhatsApp)
    C->>ROOT: [WhatsApp text: photos + "Here are the photos"]
    ROOT->>MB: PreloadMemoryTool.search_memory(user_id=phone-{hash})
    MB-->>ROOT: Memories from phone call:<br/>Chidi wants iPhone 14 Pro swap
    ROOT->>C: "Hi Chidi! I can see your iPhone photos.<br/>Let me analyze them for the trade-in we discussed."
```

---

## Latency Mitigation Architecture

```mermaid
graph TB
    subgraph "Latency Sources"
        L1["Agent Transfer<br/>5-10 sec dead air"]
        L2["Tool Execution<br/>2-5 sec blocking"]
        L3["Audio Token Bloat<br/>~25 tok/sec accumulation"]
        L4["Long-lived Voice Streams<br/>Competing with short-lived HTTP ingress"]
        L5["Session Saves<br/>200ms per Firestore write"]
        L6["Thinking Overhead<br/>Default thinking budget"]
    end

    subgraph "Mitigations"
        M1["Response Latency Watchdog<br/>Server-side: 3s nudge + 15s follow-up<br/>Model-side: mandatory filler instructions<br/>Channel-aware: voice only, not text"]
        M2["NON_BLOCKING Functions<br/>behavior: NON_BLOCKING<br/>scheduling: WHEN_IDLE"]
        M3["Context Compression<br/>trigger: 80k tokens<br/>target: 40k sliding window"]
        M4["Dedicated Live Voice Service<br/>Keeps long-lived /ws audio off the main HTTP control plane"]
        M5["Async Session Saves<br/>asyncio background tasks<br/>Never block audio stream"]
        M6["Thinking Budget Control<br/>Router: 256 (fast)<br/>Valuation: 2048 (accurate)"]
    end

    L1 --> M1
    L2 --> M2
    L3 --> M3
    L4 --> M4
    L5 --> M5
    L6 --> M6

    classDef danger fill:#FFEBEE,stroke:#C62828,stroke-width:2px
    classDef fix fill:#E8F5E9,stroke:#2E7D32,stroke-width:2px

    class L1,L2,L3,L4,L5,L6 danger
    class M1,M2,M3,M4,M5,M6 fix
```
