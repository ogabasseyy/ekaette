---
title: "Building Ekaette: A Multimodal AI Agent That Sees, Hears, and Negotiates"
published: true
tags: GeminiLiveAgentChallenge, ai, googlecloud, webdev
cover_image: https://YOUR_COVER_IMAGE_URL/cover-1000x420.png
---

*This post was created as part of my submission to the [Gemini Live Agent Challenge](https://geminiliveagentchallenge.devpost.com/). #GeminiLiveAgentChallenge*

## TL;DR

I built **Ekaette**, a multimodal AI customer service agent where customers can speak naturally, show products on camera, negotiate prices by voice, and book appointments — all in one real-time conversation. Built with Google ADK, Gemini Live API, FastAPI, React 19, and deployed on Cloud Run.

[Try the live demo](https://ekaette-XXXXX.run.app) | [GitHub Repository](https://github.com/YOUR_USERNAME/ekaette) | [Devpost Submission](https://devpost.com/software/ekaette)

---

## The Problem: Why Text Chatbots Fail

Walk into any electronics market in Lagos, and you'll see how buying and selling actually works: a customer pulls out their phone, shows it to the seller, describes what's wrong with it in conversation, they negotiate a price back and forth, and finally shake hands on a deal. It's multimodal, conversational, and deeply human.

Now try doing that through a text chatbot. You're typing descriptions of scratches you could just *show*. You're selecting prices from dropdown menus instead of making a counter-offer. You're filling out booking forms that should take 10 seconds of speech.

The gap between how people naturally transact and how "AI customer service" works today is massive. I wanted to close it.

## What Ekaette Does

Ekaette is named after a common Ibibio name meaning "mother" — fitting for an agent that guides customers through complex trade-in workflows with patience and warmth.

Here's the full flow: a customer starts a voice call, says "I want to trade in my phone," shows the device on camera, and Ekaette's vision agent identifies it as an iPhone 14 Pro in Good condition. The valuation agent calculates a price of ₦185,000. The customer says "I was hoping for ₦200,000" — and Ekaette negotiates, countering at ₦190,000. The customer accepts, and the booking agent schedules a pickup for the next morning.

All through voice. All in under 4 minutes.

<!-- TODO: Replace with your actual YouTube video ID -->
{% youtube VIDEO_ID %}

## Architecture: Six Agents, Two Models, One Voice

The key architectural insight is the **dual-model strategy**. Real-time voice needs to be fast — you can't have 5-second pauses in a conversation. But vision analysis and pricing calculations need to be thorough. So Ekaette uses two Gemini models:

- **Gemini 2.5 Flash Native Audio** (via Live API) — handles the root agent's real-time voice I/O. Bidirectional audio streaming with sub-second responses.
- **Gemini 3 Flash** (via Standard API) — powers the five specialist agents that handle reasoning-heavy tasks like image analysis, condition grading, and price calculation.

![Architecture Diagram](https://YOUR_IMAGE_URL/architecture.png)

### Multi-Agent Orchestration with Google ADK

Google's Agent Development Kit (ADK 1.25.1) orchestrates six specialized agents:

| Agent | What It Does |
|---|---|
| **ekaette_router** | Voice I/O, intent detection, routes to specialists |
| **vision_agent** | Analyzes customer photos with Gemini 3 Flash vision |
| **valuation_agent** | Grades device condition, calculates trade-in price |
| **booking_agent** | Checks availability, creates pickup appointments |
| **catalog_agent** | Searches product inventory, makes recommendations |
| **support_agent** | Handles FAQs with Google Search grounding |

The router agent is the only one connected to the Live API. When a customer sends a photo, the router detects the intent and transfers to the vision agent. The vision agent runs its analysis (using the Standard API for deeper reasoning), then transfers back with results. The router speaks the results to the customer in natural language.

The critical UX trick: **voice fillers**. During agent transfers (which take 5-10 seconds), the router says things like "Let me take a closer look at that..." — keeping the conversation alive while specialist agents work in the background with `NON_BLOCKING` tool behavior.

### Memory That Persists Across Sessions

Ekaette uses a 3-tier memory architecture:

1. **Session State** (Firestore) — current call context, booking drafts, assessment results
2. **Memory Bank** (Vertex AI Agent Engine) — long-term customer memory. After each session, Gemini extracts key facts ("Customer name: Chidi", "Prefers morning pickups", "Located in Lekki") and stores them. On the next session, `PreloadMemoryTool` retrieves these at the start of each turn.
3. **Industry Knowledge** (Firestore configs) — shared pricing rubrics, voice personas, booking rules

The result: when Chidi calls back a week later, Ekaette says "Welcome back, Chidi! How's life after the iPhone trade-in?" instead of treating them as a stranger.

## The Hard Parts

### Challenge 1: AudioWorklet Echo Feedback

The starter code from the ADK bidi-demo had a critical bug: it connected the microphone recorder to `ctx.destination`, creating a feedback loop where the mic picked up its own output. The fix was using **separate AudioContexts** — 16kHz for recording, 24kHz for playback — and never connecting the recorder to any output node.

```typescript
// WRONG: Creates echo feedback
source.connect(recorder)
recorder.connect(ctx.destination) // mic → speaker → mic loop!

// CORRECT: Separate contexts, no output connection
recorderCtx = new AudioContext({ sampleRate: 16000 })
playerCtx = new AudioContext({ sampleRate: 24000 })
source.connect(recorder) // recorder only, no destination
```

### Challenge 2: ADK Duplicate Responses After Agent Transfers

After multiple agent transfers, ADK bug #3395 causes earlier responses to replay. I mitigated this with a `before_agent_callback` that tracks content hashes and suppresses duplicates — essentially a deduplication layer at the agent boundary.

### Challenge 3: Latency During Agent Transfers

The biggest UX threat to a voice-first app is silence. Agent transfers take 5-10 seconds. My mitigations:

- **Voice fillers** baked into the root agent's system instructions
- **`NON_BLOCKING` tool behavior** — the agent keeps talking while tools execute
- **`WHEN_IDLE` scheduling** — tool results are surfaced only when the agent isn't speaking
- **Thinking budget control** — router gets 256 tokens (fast routing), valuation agent gets 2048 (accurate calculations)

## Key Learnings

**The Live API is incredibly powerful but demands careful session management.** Sessions time out at ~10 minutes, so I implemented session resumption tokens and context compression (trigger at 80k tokens, slide to 40k). GoAway events from the server need graceful handling — show a countdown, cache the resumption token, reconnect seamlessly.

**TDD saved me repeatedly.** With 293 tests (212 backend, 81 frontend), I could refactor aggressively. When I rewrote the message extraction from four separate array scans to a single-pass loop, the tests caught a regression in transcript ordering immediately.

**Multi-industry support was cheaper than expected.** The core agent architecture is industry-agnostic. Switching from electronics to hotel changes the voice persona (Aoede → Puck), the pricing rubric, and the conversation style — but the agent topology, memory system, and streaming pipeline are identical. One codebase, four industries.

## The Stack

- **Backend**: Python 3.13, FastAPI, Google ADK 1.25.1, Gemini Live API
- **Frontend**: React 19, Vite 7, Tailwind CSS v4, TypeScript 5.9
- **Infrastructure**: Cloud Run, Firestore, Cloud Storage, Vertex AI Agent Engine, Terraform
- **Testing**: pytest (212 tests), Vitest (81 tests)

## Try It Yourself

- **Live Demo**: [https://ekaette-XXXXX.run.app](https://ekaette-XXXXX.run.app)
- **GitHub**: [https://github.com/YOUR_USERNAME/ekaette](https://github.com/YOUR_USERNAME/ekaette)
- **Devpost**: [https://devpost.com/software/ekaette](https://devpost.com/software/ekaette)

The full source is open — clone it, swap in your Gemini API key, and you'll have a working multimodal voice agent in minutes.

---

*Built by Bassey (Baci Technologies Limited) for the [Gemini Live Agent Challenge](https://geminiliveagentchallenge.devpost.com/). Built with Python 3.13, FastAPI, Google ADK 1.25.1, Gemini Live API, React 19, Vite 7, Tailwind v4, Firestore, and Cloud Run.*
