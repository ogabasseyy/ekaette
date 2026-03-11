# Cross-Channel Live Media Injection Plan — 2026-03-11

## Purpose

Add a safe, production-grade cross-channel capability so Ekaette can continue a live voice conversation while receiving customer media on WhatsApp.

Target experience:
1. Customer is on a live voice call.
2. Ekaette asks for a photo or video on WhatsApp.
3. Customer sends the media in WhatsApp chat.
4. The active live voice session receives a structured media event.
5. Ekaette continues the voice conversation using that media context without forcing the customer to restart the flow.

This document is the canonical implementation reference for that feature.

## Current State

What exists today:
- Voice and WhatsApp already share a canonical phone identity.
- Voice can request media on WhatsApp using durable Firestore-backed handoff context.
- WhatsApp media can continue the business flow in a separate text/media session.

What does not exist yet:
- Injection of newly received WhatsApp media into an already-running live voice session.
- A shared conversation record above channel-specific ADK session IDs.
- A runtime live-session registry that can accept external media events.

## Non-Goals

- Do not force all channels into one ADK session.
- Do not fake cross-channel continuity by injecting prior context as fake customer speech.
- Do not enable this everywhere at once.
- Do not replace the current durable handoff path until the new path is proven.

## 2026 Best-Practice Decisions

### 1. Shared conversation state, separate channel sessions

Best practice is:
- one shared conversation record
- separate live/text sessions per channel
- explicit event bridging between them

This avoids brittle coupling across transport/runtime boundaries.

### 2. Firestore is the durable source of truth

Cross-channel linkage and pending handoff state should live in a durable store.

Do not rely on ADK `user:*` state inheritance across channels because:
- session/user state is scoped by `app_name + user_id`
- WhatsApp text/media sessions and live voice sessions are intentionally separate app/session contexts

### 3. Media injection must be structured

When media arrives from another channel, inject it as:
- typed event metadata
- provenance
- actual media payload or reference

Do not inject it as if the customer just “said” the context in a normal user turn.

### 4. Feature-flag rollout

The mechanism can be generic, but rollout must be channel-scoped.

Initial enablement:
- `whatsapp_voice <- whatsapp_chat_media`

Deferred:
- `at_voice <- whatsapp_chat_media`
- `web_voice <- whatsapp_chat_media`

### 5. Fallback path remains mandatory

If active-session injection fails or no live session exists:
- keep the current durable WhatsApp handoff flow
- do not drop the customer’s media or context

### 6. Privacy and encryption handling

WhatsApp end-to-end encryption is not a blocker to this design.

Implementation should remain:
- webhook/media-event driven
- server-side
- short-retention
- minimal logging of sensitive payloads

## Why This Should Not Be Enabled Immediately

This feature adds real-time coordination between:
- active live voice sessions
- WhatsApp webhook/media arrival
- business logic/tool routing
- session ownership and turn arbitration

It can break:
- transfer behavior
- callback flows
- greeting behavior
- live turn ordering
- duplicate tool execution

So the correct approach is:
- stabilize the current voice architecture first
- implement behind feature flags
- enable only after baseline reliability is acceptable

## Stabilization Gate

Do not enable live media injection until these are stable:

1. AT call reachability is stable.
2. WhatsApp call speech path is stable.
3. Transfer to specialist agents is stable.
4. Callback acknowledgement and hangup work reliably.
5. No recurring `1000` / `1008` live-session failure loops.
6. Greeting state no longer causes repeated blocked-transfer loops.

If these are still moving, design and implementation can proceed behind flags, but rollout should wait.

## Proposed Runtime Model

### Canonical entities

- `conversation_id`
- `tenant_id`
- `company_id`
- canonical user phone
- active channels
- active live session metadata
- pending cross-channel events
- last known agent state

### Distinction from existing ADK sessions

ADK session IDs remain channel-local and transport-specific.

The new `conversation_id` becomes the cross-channel coordination key.

## Phase 1 — Conversation Identity Layer

### Goal

Introduce a durable `conversation_id` layer above ADK session IDs.

### Required behavior

For every voice or WhatsApp interaction:
- resolve canonical phone identity
- resolve or create active `conversation_id`
- associate channel session with that conversation

### Suggested storage

Firestore collection group, for example:
- `tenants/{tenantId}/companies/{companyId}/conversations/{conversationId}`

### Suggested fields

- `conversation_id`
- `tenant_id`
- `company_id`
- `user_phone`
- `active_channel`
- `active_channels[]`
- `last_activity_at`
- `status`
- `current_live_channel`
- `last_agent_name`
- `last_user_intent`

### Likely code touchpoints

- `app/api/v1/realtime/session_init.py`
- `app/api/v1/at/service_voice.py`
- `app/api/v1/at/service_whatsapp.py`
- new `app/runtime/conversations/`

### TDD requirements

Write failing tests for:
- new conversation created on first voice call
- existing conversation reused on later WhatsApp media event
- canonical phone resolves to same conversation across channels

## Phase 2 — Active Live Session Registry

### Goal

Track which conversation currently has a live voice session that can accept out-of-band events.

### Required behavior

Maintain a runtime registry:
- `conversation_id -> active live session controller`

### Track

- `conversation_id`
- active channel
- ADK/live session id
- session state handle
- websocket status
- last heartbeat
- model speaking status
- current agent
- whether external media injection is allowed

### Design note

This is runtime state, not the durable source of truth.

It should be rebuildable after restart.

### Likely code touchpoints

- `app/api/v1/realtime/ws_stream.py`
- `app/api/v1/realtime/stream_tasks.py`
- `app/api/v1/realtime/orchestrator.py`
- new `app/api/v1/realtime/live_session_registry.py`

### TDD requirements

Write failing tests for:
- active live session registration on connect
- cleanup on disconnect
- rebind on reconnect/session resumption
- no stale active session after close

## Phase 3 — Cross-Channel Media Event Model

### Goal

Create a typed event model for media arriving from another channel.

### Event shape

- `type=external_media_received`
- `conversation_id`
- `source_channel=whatsapp_chat`
- `target_channel`
- `tenant_id`
- `company_id`
- `user_phone`
- `media_kind=image|video|audio|document`
- `mime_type`
- `caption`
- `provenance_text`
- `handoff_summary`
- `received_at`
- `media_reference`

### Important rule

`provenance_text` must make the channel boundary explicit, for example:

> The caller sent this image on WhatsApp during the current call.

Never disguise channel metadata as ordinary user speech.

### Likely code touchpoints

- new `app/runtime/conversations/events.py`
- `app/api/v1/at/service_whatsapp.py`

### TDD requirements

Write failing tests for:
- event object creation from WhatsApp image webhook
- event object creation from WhatsApp video webhook
- provenance preserved and explicit

## Phase 4 — WhatsApp Media Intake to Conversation Event

### Goal

Extend the WhatsApp media handler so incoming media can either:
- follow the current async WhatsApp continuation path
- or be forwarded into an active live voice session

### Required behavior

On WhatsApp media receipt:
1. Resolve canonical phone identity.
2. Resolve conversation.
3. Look up active live session for that conversation.
4. If no active eligible live session:
   - continue current WhatsApp media flow
5. If active eligible live session exists:
   - create `external_media_received` event
   - enqueue for live injection

### Use existing durable handoff work

Build on:
- `app/tools/cross_channel_tools.py`
- `load_and_consume_cross_channel_context(...)`

Do not throw away the existing handoff mechanism.

### Likely code touchpoints

- `app/api/v1/at/service_whatsapp.py`
- `app/channels/adk_text_adapter.py`
- `app/tools/cross_channel_tools.py`

### TDD requirements

Write failing tests for:
- media with no active session -> current WhatsApp fallback path
- media with active WhatsApp voice session -> event queued
- media with active AT session while feature disabled -> no injection

## Phase 5 — Inject Into Active Live Session

### Goal

Deliver the received WhatsApp media into the currently active live voice session without restarting the call.

### Required behavior

For an eligible active session:
- inject structured context
- inject media itself
- preserve channel provenance

### Initial rollout

Only enable:
- `whatsapp_voice <- whatsapp_chat_media`

Do not enable AT in the same phase.

### Injection contract

The live session should receive:
- controlled preamble / system metadata
- media bytes or approved media reference
- optional short summary from earlier handoff context

### Important rule

This must not be implemented as:
- fake user text
- fake transcript
- synthetic customer speech

It should be injected as a system-controlled multimodal event.

### Likely code touchpoints

- `app/api/v1/realtime/stream_tasks.py`
- `app/api/v1/realtime/ws_stream.py`
- new `app/api/v1/realtime/cross_channel_injection.py`

### TDD requirements

Write failing tests for:
- injection payload assembled correctly
- provenance preserved
- media event reaches active session handler
- no duplicate injection for same media object

## Phase 6 — Turn Arbitration and Channel Policy

### Goal

Prevent overlapping voice/chat behavior and race conditions.

### Required behavior

If the model is currently speaking:
- queue the media event
- deliver it when current speech finishes

If the model is idle:
- inject immediately

Agent response pattern should be short and controlled:
- “I’ve received the photo now, give me a second.”

### Channel policy

Start with:
- `whatsapp_voice <- whatsapp_chat_media`: enabled

Keep disabled:
- `at_voice <- whatsapp_chat_media`
- `web_voice <- whatsapp_chat_media`

### Design note

The implementation should be generic enough to support AT later, but rollout should stay scoped.

### Likely code touchpoints

- `app/api/v1/realtime/stream_tasks.py`
- active session registry
- session speaking-state tracking

### TDD requirements

Write failing tests for:
- event queued while model speaking
- event injected immediately while idle
- same event not replayed twice
- disabled channel path refuses injection

## Phase 7 — Observability, Security, Rollout

### Goal

Make the feature diagnosable and safe before broad rollout.

### Required logs/metrics

- `external_media_received`
- `media_injection_attempted`
- `media_injection_succeeded`
- `media_injection_failed`
- `media_injection_deferred`
- source channel
- target channel
- conversation id
- upload to spoken-response latency
- fallback-used flag

### Security and privacy

- minimal logging of captions and media URLs
- short-lived media references
- keep durable store TTLs where possible
- explicit channel provenance in event records
- consume-once semantics where appropriate

### Rollout

1. Keep feature flag off by default.
2. Enable in local/dev only.
3. Enable for WhatsApp voice only.
4. Validate.
5. Later consider AT voice.

### Likely code touchpoints

- logging in `service_whatsapp.py`
- realtime stream/injection logging
- analytics/voice operations surface

### TDD requirements

Write failing tests for:
- success path logs
- failure path logs
- fallback path logs
- metrics emitted once per event

## Feature Flags

Recommended flags:

- `LIVE_CROSS_CHANNEL_MEDIA_INJECTION_ENABLED=false`
- `LIVE_CROSS_CHANNEL_MEDIA_INJECTION_WHATSAPP_VOICE=true`
- `LIVE_CROSS_CHANNEL_MEDIA_INJECTION_AT_VOICE=false`
- `LIVE_CROSS_CHANNEL_MEDIA_INJECTION_WEB_VOICE=false`

Optional:
- `LIVE_CROSS_CHANNEL_MEDIA_INJECTION_REQUIRE_ACTIVE_CALL=true`
- `LIVE_CROSS_CHANNEL_MEDIA_INJECTION_MAX_EVENT_AGE_SECONDS=300`

## Risks

### Technical risks

- duplicate tool execution
- race conditions during active speech
- media arrives after call has ended
- greeting/transfer regressions if the injection role semantics are wrong
- stale active-session registry entries
- longer sessions stressing live connection limits

### Product risks

- customer may not realize the photo was successfully received on-call
- agent may over-talk instead of briefly acknowledging
- inconsistent behavior across AT vs WhatsApp if rollout policy is unclear

### Security/privacy risks

- over-logging media provenance or URLs
- stale durable handoff state reused later
- channel confusion if provenance is omitted

## Why “One Session Across Channels” Is Not Recommended

That model sounds simpler but is not the right architecture here.

Problems:
- transport/runtime mismatch
- ADK session scoping mismatch
- failure recovery becomes harder
- voice and WhatsApp lifecycle semantics differ

Best practice is:
- one conversation
- multiple channel sessions
- explicit event bridge

## TDD Sequence

Mandatory discipline:
1. Write failing tests first.
2. Implement minimum code to pass.
3. Refactor.
4. Keep current fallback path intact.
5. Enable only behind flags.

## Recommended Rollout Order

1. Stabilize current AT and WhatsApp voice architecture.
2. Implement Phases 1-4 with feature flags off.
3. Implement Phase 5 injection path with feature flags still off.
4. Add Phase 6 arbitration.
5. Add Phase 7 observability.
6. Enable only for `whatsapp_voice <- whatsapp_chat_media`.
7. Validate before considering AT.

## Recommendation

For the current stage of the product:
- design now
- implement now if desired
- keep behind feature flags
- do not make it the default demo path until the current architecture is stable

This is the safest 2026-best-practice path.
