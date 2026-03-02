# Delegation Prompt — CodeRabbit Stream A (UI / Components / Tests Only)

Copy/paste the prompt below to the other agent.

---

You are working on **Stream A** of a production hardening pass for the Ekaette frontend.  
Your job is to fix **only the UI/components/tests issues** from a verified CodeRabbit review.

## Critical Rules (must follow)

1. **Verify each issue against the current code before changing it.**
   - Do not blindly apply all review comments.
   - If a comment is already fixed or not applicable, note that and move on.

2. **Do NOT touch hooks, worklets, socket logic, backend, or voice transport logic.**
   - Do not modify:
     - `frontend/src/hooks/useEkaetteSocket.ts`
     - `frontend/src/hooks/useAudioWorklet.ts`
     - `frontend/src/hooks/useDemoMode.ts`
     - `frontend/public/pcm-player-processor.js`
     - `frontend/public/pcm-recorder-processor.js`
     - `main.py`
     - `app/**`

3. **Do NOT remove `@google/genai` from the frontend.**
   - That CodeRabbit comment is a false positive in this repo because direct-live uses backend-issued ephemeral tokens.

4. **Do not redesign the UI.**
   - This is a correctness/accessibility/test hardening pass.
   - Keep the current visual design.

5. **Treat this like production code.**
   - Add tests for behavior changes.
   - Avoid risky refactors.

## Your Allowed Files (Scope)

You may edit only these areas:

- `frontend/src/components/cards/ProductCard.tsx`
- `frontend/src/components/cards/ValuationCard.tsx`
- `frontend/src/components/layout/Footer.tsx`
- `frontend/src/components/layout/Header.tsx`
- `frontend/src/components/layout/IndustryOnboarding.tsx`
- `frontend/src/components/layout/TranscriptionOverlay.tsx` (only key/scroll/accessibility improvements)
- `frontend/src/components/layout/VoicePanel.tsx`
- `frontend/src/components/ui/ImageUpload.tsx`
- `frontend/src/components/ui/MicButton.tsx`
- `frontend/src/components/ui/TextInput.tsx`
- `frontend/src/components/__tests__/*.test.tsx`
- `frontend/src/lib/format.ts`
- `frontend/src/lib/__tests__/format.test.ts`
- `frontend/src/index.css`
- `frontend/package.json`

If you need to change anything outside this scope, stop and report it.

## Files You Must Not Touch (Hard Exclusion)

- `frontend/src/App.tsx`
- `frontend/src/__tests__/App.test.tsx`
- `frontend/src/hooks/**`
- `frontend/public/pcm-*.js`
- `frontend/src/utils/mockData.ts`
- `frontend/src/lib/transcript.ts`
- any backend files (`main.py`, `app/**`, `tests/**` for backend)

## Tasks to Fix (Verified as Valid)

### 1) Components — Correctness / UX / Accessibility

#### `frontend/src/components/cards/ProductCard.tsx`
- Use explicit locale/currency formatting for non-NGN values (avoid bare `toLocaleString()` defaults).
- Change label from `"Out"` to `"Out of stock"`.

#### `frontend/src/components/cards/ValuationCard.tsx`
- Add `type="button"` to all buttons (`Accept`, `Counter`, `Decline`).
- Sync `counterOffer` state when `price` prop changes (`useEffect`).

#### `frontend/src/components/layout/Footer.tsx`
- Fix status mapping so `connectionState === 'reconnecting'` is not shown as idle.
- Map reconnecting to an existing visual state (`processing`) unless a better supported option already exists.

#### `frontend/src/components/layout/Header.tsx`
- Render a user-friendly connection label (capitalize/map) instead of raw `connectionState`.

#### `frontend/src/components/layout/IndustryOnboarding.tsx`
- Add `aria-pressed={active}` to selection buttons.
- (Optional but good) add `role="radiogroup"` + `role="radio"` if done consistently.
- Update Tailwind v3 variable syntax to Tailwind v4 syntax for the accent background class.

#### `frontend/src/components/layout/TranscriptionOverlay.tsx`
- Replace unstable key using text slice with a stable key (prefer stable identifier if available; otherwise `index` is acceptable here).
- Improve auto-scroll behavior: only auto-scroll if user is near the bottom (or initial render).
  - Keep behavior simple and robust; don’t over-engineer.

#### `frontend/src/components/layout/VoicePanel.tsx`
- Ensure `is-warming` only applies when `isStarting && !isConnected`.
- Update Tailwind v3 variable syntax to Tailwind v4 syntax on accent-colored text.

#### `frontend/src/components/ui/ImageUpload.tsx`
- Add `FileReader.onerror` handling.
- Clear stale preview on errors and before a new read.
- If `reader.onload` data URL parsing fails, set validation error + call `onError` + clear preview (do not silently return).

#### `frontend/src/components/ui/MicButton.tsx`
- Add `type="button"`.
- Update Tailwind v3 variable syntax (`bg-[color:var(--industry-accent)]`) to Tailwind v4-compatible syntax.

#### `frontend/src/components/ui/TextInput.tsx`
- Add `type="button"` to send button.
- Fix Enter path so it respects pending/send guards (same logic as button).
- Remove `startTransition` around `setDraft` (avoid input lag).
- Remove unnecessary `useMemo` for `canSend` if it becomes a simple boolean.

### 2) Component Tests — Coverage Gaps

#### `frontend/src/components/__tests__/BookingConfirmationCard.test.tsx`
- Add assertion that `service` is rendered (e.g. `"Doorstep pickup"`).

#### `frontend/src/components/__tests__/ImageUpload.test.tsx`
Add tests for:
- unsupported type (`text/plain`)
- file size > 10MB
- FileReader error path

Also:
- wrap `FileReader` monkey-patch in `try/finally` so restore always happens.

#### `frontend/src/components/__tests__/MicButton.test.tsx`
- Add disabled-state test:
  - `toBeDisabled()`
  - click does not call `onClick`

#### `frontend/src/components/__tests__/TranscriptionOverlay.test.tsx`
- Empty-state test (`No live transcript yet.` + `0 messages`)
- Message-count badge assertions for non-empty arrays
- Partial-message test should also assert role label includes `• listening`

#### `frontend/src/components/__tests__/ValuationCard.test.tsx`
- Add non-NGN formatting test (e.g. `USD 500`)

#### `frontend/src/components/__tests__/VoicePanel.test.tsx`
- `isStarting={true}` -> orb has `is-warming` and `SYNC`
- `elapsedSeconds={0}` -> `00:00`
- render `audioError` and `callError`

### 3) Shared UI Utility Formatting

#### `frontend/src/lib/format.ts`
- Normalize `formatDuration(totalSeconds)` input:
  - non-negative integer (clamp/floor)
- Hoist `Intl.NumberFormat` instance for `formatNaira`

#### `frontend/src/lib/__tests__/format.test.ts`
- Strengthen currency-indicator assertion to check for `₦` or `NGN` explicitly.

### 4) CSS / Tooling (Frontend Scope Only)

#### `frontend/src/index.css`
- Change top font import to string notation (`@import "https://..."`).
- If lint/style rule requires a blank line near the flagged declaration, fix spacing minimally.

#### `frontend/package.json`
- Add `"test"` script (use `vitest run`).

## Comments / Findings You Should Ignore (Intentional / Out of Scope)

Do **not** implement these in your stream:

- Remove frontend `@google/genai` / move all genai calls server-side (false positive here)
- `App.tsx` large hook extraction refactor
- Hook/socket/worklet race fixes (handled by Codex in another stream)
- Biome Tailwind parser config (`biome.json` not present)

## Coding Standards for This Task

- Keep changes minimal and local.
- Add `type="button"` on buttons rendered inside potentially nested layouts/forms.
- Preserve current UI design and copy unless the review item explicitly requests copy clarity (e.g. `"Out of stock"`).
- Use Tailwind v4-compatible CSS variable syntax consistently where touched.
- Do not add new dependencies.

## Validation You Must Run

Run at least:

```bash
pnpm -C frontend exec vitest run \
  frontend/src/components/__tests__/BookingConfirmationCard.test.tsx \
  frontend/src/components/__tests__/ImageUpload.test.tsx \
  frontend/src/components/__tests__/MicButton.test.tsx \
  frontend/src/components/__tests__/TranscriptionOverlay.test.tsx \
  frontend/src/components/__tests__/ValuationCard.test.tsx \
  frontend/src/components/__tests__/VoicePanel.test.tsx \
  frontend/src/lib/__tests__/format.test.ts
```

Then run:

```bash
pnpm -C frontend build
```

## Expected Output Back to Me

Return a concise report with:

1. **Files changed**
2. **Which CodeRabbit comments were fixed**
3. **Which comments were skipped (and why)**
4. **Test results**
5. **Any conflicts or blockers**

If you had to touch any file outside the allowlist, highlight it explicitly.

---

End of delegation prompt.

