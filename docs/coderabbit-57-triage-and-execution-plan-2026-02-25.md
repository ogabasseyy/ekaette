# CodeRabbit 57 Findings Triage + Execution Plan (2026-02-25)

## Purpose

This document preserves:

- Verified triage of CodeRabbit findings against the **current** code
- What is real vs false positive vs defer
- Production-safe execution plan
- Parallel work split (for another agent + Codex)

Use this as the source of truth if session context is lost.

## Current Baseline / Constraints

- Repo: `Ekaette`
- Current backend/frontend run locally on:
  - Backend: `http://127.0.0.1:8000`
  - Frontend: `http://127.0.0.1:5173`
- Stable checkpoint restored before this planning pass:
  - `main.py` incremental silence nudge backoff + nudge guards
  - `scripts/live_smoke.py` websocket smoke harness
  - safe prompt guidance in `catalog_agent` / `ekaette_router`
- Later runtime fallback changes in `app/agents/callbacks.py` were reverted because they reintroduced bugs.

## Key Triage Principle

Do **not** apply all CodeRabbit comments blindly.

At least one major comment is a false positive:

- `frontend` includes `@google/genai`, but it uses a **backend-issued ephemeral Live token** from `POST /api/token` (`main.py`) for direct-live mode.
- It does **not** expose a long-lived `GOOGLE_API_KEY` client-side.

## Verified Triage Summary

### A. False Positive / No Code Change

1. `frontend/package.json` + `frontend/src/hooks/useEkaetteSocket.ts`
   - Comment: remove `@google/genai` from frontend because API key exposure.
   - Verdict: **No change (false positive)**.
   - Reason: frontend direct-live path uses backend-issued constrained ephemeral token from `POST /api/token`; no static secret is bundled.

### B. Not Applicable (Current Repo)

1. `biome.json` Tailwind parser settings (`tailwindDirectives`)
   - Verdict: **Not applicable**
   - Reason: no `biome.json` exists in this repo.

### C. Valid, but Refactor / Defer (separate PR preferred)

1. `frontend/src/App.tsx` split into `useConnectionManager`, `useDerivedMessages`, `useDebugPanel`
   - Valid architectural improvement.
   - Defer to refactor PR to reduce regression risk while voice behavior is stabilizing.

2. `frontend/src/App.tsx` granular `Suspense` + `ErrorBoundary` around lazy cards
   - Valid resilience improvement.
   - Defer to refactor/resilience PR unless needed immediately.

### D. Valid — Fix in Production Hardening Pass

#### D1. Frontend Core / App

1. `frontend/src/App.tsx`
   - `parseStoredIndustry` should derive valid values from `INDUSTRY_COMPANY_MAP`
   - `handleToggleCall` polling interval cleanup and unmount safety
   - `socket.onSessionEnding` effect depends on unstable `socket` object (fix effect dependency strategy, not `useMemo(useEkaetteSocket(...))`)

2. `frontend/src/__tests__/App.test.tsx`
   - replace `.toBeTruthy()` / `.toBeNull()` with jest-dom matchers
   - add missing edge-case tests:
     - toggle call when already connected
     - connection failure/timeout -> `callError`
     - server `session_ending` resets UI state
     - demo mode path
     - multiple rapid errors -> latest error and timer reset

#### D2. Components (Correctness / Accessibility / UX)

1. `frontend/src/components/cards/ProductCard.tsx`
   - explicit locale/currency formatting for non-NGN
   - replace ambiguous `"Out"` with `"Out of stock"`

2. `frontend/src/components/cards/ValuationCard.tsx`
   - add `type="button"` on all buttons
   - sync `counterOffer` state when `price` prop changes

3. `frontend/src/components/layout/Footer.tsx`
   - handle `connectionState === 'reconnecting'` in status mapping

4. `frontend/src/components/layout/Header.tsx`
   - map raw connection state to user-friendly labels

5. `frontend/src/components/layout/IndustryOnboarding.tsx`
   - add `aria-pressed` (and optional group semantics)
   - Tailwind v4 CSS variable syntax update for CTA background

6. `frontend/src/components/layout/TranscriptionOverlay.tsx`
   - stable key (avoid text-slice key)
   - only autoscroll when near bottom / initial render (improvement)

7. `frontend/src/components/layout/VoicePanel.tsx`
   - `is-warming` only when `isStarting && !isConnected`
   - Tailwind v4 CSS variable syntax update

8. `frontend/src/components/ui/ImageUpload.tsx`
   - FileReader `onerror`
   - clear stale preview on failure / before new read
   - parse-failure path should call `onError` and clear preview

9. `frontend/src/components/ui/MicButton.tsx`
   - add `type="button"`
   - Tailwind v4 variable syntax update

10. `frontend/src/components/ui/TextInput.tsx`
    - `Enter` path should respect `isPending` / `canSend`
    - add `type="button"`
    - remove unnecessary `useMemo` for `canSend` (low priority but fine)
    - remove `startTransition` around `setDraft` (input lag risk)

#### D3. Component Tests (Coverage Gaps)

1. `BookingConfirmationCard.test.tsx`
   - assert `service` field rendered

2. `ImageUpload.test.tsx`
   - unsupported type
   - >10MB size
   - FileReader error path
   - always restore FileReader in `try/finally`

3. `MicButton.test.tsx`
   - disabled prop behavior (`toBeDisabled`, no click call)

4. `TranscriptionOverlay.test.tsx`
   - empty state + count badge
   - count badge for non-empty
   - partial label includes `• listening`

5. `ValuationCard.test.tsx`
   - non-NGN branch test (e.g., USD)

6. `VoicePanel.test.tsx`
   - `isStarting=true` / `SYNC`
   - `elapsedSeconds=0` -> `00:00`
   - error badge rendering (`audioError`, `callError`)

#### D4. Hooks / Worklets / Socket Reliability (High Risk, Important)

1. `frontend/public/pcm-player-processor.js`
   - playback stats emission should be opt-in
   - `endOfAudio` buffer clear logic should preserve frames enqueued after end signal

2. `frontend/public/pcm-recorder-processor.js`
   - VAD timing comments/constants mismatch with AudioWorklet process quantum
   - make comments and frame constants consistent with intended timing

3. `frontend/src/hooks/useAudioWorklet.ts`
   - document `Float32Array` branch as fallback path
   - lint warning on implicit return in `forEach(track => track.stop())`
   - `stop()` delayed timeout can disconnect newly initialized player node/context (capture local refs)

4. `frontend/src/hooks/useDemoMode.ts`
   - `resume()` should preserve remaining delay rather than restarting full delay

5. `frontend/src/hooks/useEkaetteSocket.ts`
   - `arrayBufferToBase64` spread can exceed engine arg limits
   - `connectDirectLive` race/cleanup across awaits (`requestEphemeralToken`, `ai.live.connect`)
   - token response should propagate `manualVadActive` and `vadMode`
   - direct-live `session_started` should use server-provided VAD mode fields
   - remove unnecessary callback dependency (`mutateDebugMetrics`) in `handleDirectLiveMessage` (low-risk cleanup)

#### D5. Hook Tests

1. `useDemoMode.test.ts`
   - replay after reset

2. `useEkaetteSocket.test.ts`
   - negative `sendActivityEnd` before manual VAD handshake
   - `fetch` mock restore hygiene (`vi.spyOn` or `try/finally`)
   - MAX_MESSAGES cap
   - rapid disconnect protection (`RAPID_DISCONNECT`)
   - `clearMessages()`
   - `sendAudio` binary path and drop/backpressure counters

#### D6. Utilities / Runtime Validation / Formatting

1. `frontend/src/utils/mockData.ts`
   - `isServerMessage` must include and validate:
     - `session_ending`
     - `telemetry`
     - `ping`

2. `frontend/src/utils/__tests__/mockData.test.ts`
   - non-integer `getDemoStep` inputs: float, `NaN`, numeric string

3. `frontend/src/lib/format.ts`
   - `formatDuration` should normalize negative/fractional input
   - hoist `Intl.NumberFormat` in `formatNaira`

4. `frontend/src/lib/__tests__/format.test.ts`
   - assert presence of currency marker (`₦` or `NGN`) explicitly

5. `frontend/src/lib/transcript.ts`
   - simplify redundant overlap checks in `hasMeaningfulTextOverlap` (low-priority cleanup)

6. `frontend/src/lib/__tests__/transcript.test.ts`
   - add early-return test for `sanitizeTranscriptForDisplay` when no `preferredUserScript`
   - remove/replace duplicate `mergePartialText('Hello', 'Goodbye')` test case

#### D7. Tooling / CSS

1. `frontend/package.json`
   - add `"test"` script (`vitest run`)

2. `frontend/src/index.css`
   - convert `@import url(...)` to string import notation
   - fix blank-line / stylelint formatting issue if lint is enabled for this rule

## Parallel Execution Strategy (Production-Safe)

### Stream A (Delegate) — UI / Components / Tests

**Owner:** Other agent  
**Risk:** Low-to-medium  
**Do not touch:** `useEkaetteSocket`, `useAudioWorklet`, worklet processors, backend, `main.py`

Files (allowlist):

- `frontend/src/components/cards/ProductCard.tsx`
- `frontend/src/components/cards/ValuationCard.tsx`
- `frontend/src/components/layout/Footer.tsx`
- `frontend/src/components/layout/Header.tsx`
- `frontend/src/components/layout/IndustryOnboarding.tsx`
- `frontend/src/components/layout/TranscriptionOverlay.tsx` (only key/scroll/accessibility adjustments if assigned)
- `frontend/src/components/layout/VoicePanel.tsx`
- `frontend/src/components/ui/ImageUpload.tsx`
- `frontend/src/components/ui/MicButton.tsx`
- `frontend/src/components/ui/TextInput.tsx`
- `frontend/src/components/__tests__/*.test.tsx`
- `frontend/src/lib/format.ts`
- `frontend/src/lib/__tests__/format.test.ts`
- `frontend/src/index.css`
- `frontend/package.json`

### Stream B (Codex) — Hooks / Worklets / Reliability

**Owner:** Codex  
**Risk:** High  
Files:

- `frontend/src/hooks/useEkaetteSocket.ts`
- `frontend/src/hooks/useAudioWorklet.ts`
- `frontend/src/hooks/useDemoMode.ts`
- `frontend/public/pcm-player-processor.js`
- `frontend/public/pcm-recorder-processor.js`
- related hook tests

### Stream C (Codex / Integrator) — App + Validators + Integration Tests

**Owner:** Codex  
**Risk:** Medium  
Files:

- `frontend/src/App.tsx`
- `frontend/src/__tests__/App.test.tsx`
- `frontend/src/utils/mockData.ts`
- `frontend/src/utils/__tests__/mockData.test.ts`
- `frontend/src/lib/transcript.ts`
- `frontend/src/lib/__tests__/transcript.test.ts`

## Batch Order (Recommended)

1. **Batch A1 (Delegate):** component correctness + component tests + CSS syntax + package test script
2. **Batch C1 (Codex):** utility/runtime validators + format/transcript test fixes
3. **Batch B1 (Codex):** hook/socket race fixes + hook tests
4. **Batch B2 (Codex):** worklet bug fixes + local manual QA
5. **Batch C2 (Codex):** `App.tsx` lifecycle fixes + `App.test.tsx` edge cases
6. **Optional PR-2:** App decomposition + per-card Suspense/ErrorBoundary refactor

## Validation / Acceptance Criteria

### Required Automated Checks

Run after each batch touching frontend:

```bash
pnpm -C frontend build
pnpm -C frontend test
```

Targeted runs during development:

```bash
pnpm -C frontend exec vitest run frontend/src/__tests__/App.test.tsx
pnpm -C frontend exec vitest run frontend/src/hooks/__tests__/useEkaetteSocket.test.ts
pnpm -C frontend exec vitest run frontend/src/hooks/__tests__/useDemoMode.test.ts
```

### Manual QA (Voice Critical)

1. Start/stop call
2. Greeting + follow-up response latency sanity
3. Transcript still stable (no duplicate bubble regressions)
4. Interruption / playback stop behavior
5. Silence nudge timing still increments (with restored backoff)
6. Direct-live optional path still works or cleanly falls back

## CodeRabbit Response Policy (per comment)

Each comment should be marked as:

- `Fixed`
- `No change (false positive / intentional architecture)`
- `Deferred (separate refactor PR)`
- `Not applicable`

### Required rationale for `@google/genai` comment

Document in PR reply:

- Direct-live frontend path uses backend-issued short-lived constrained Gemini Live auth tokens from `POST /api/token`
- No long-lived API key is bundled client-side
- Backend controls TTL, uses, model, and config constraints

## Commit / Merge Guidance

- Do **not** commit `.data/`
- Prefer separate commits per stream/batch
- Merge Stream A first (low conflict), then Stream B/C in controlled batches

Suggested commit naming style:

- `frontend: harden ui components and tests (coderabbit batch a1)`
- `frontend: fix socket/audio races and worklets (coderabbit batch b1/b2)`
- `frontend: tighten app lifecycle and runtime validators (coderabbit batch c1/c2)`

## Notes for Future Context Recovery

- The direct-live `@google/genai` dependency is intentional in this architecture (ephemeral token flow).
- `app/agents/callbacks.py` runtime fallback transfer logic was reverted due regressions.
- `main.py` silence nudge backoff/guard logic is currently part of the restored stable baseline.
- `scripts/live_smoke.py` exists for backend websocket smoke checks and can be used to reproduce routing/latency behavior without UI.

