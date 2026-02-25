import type { ServerMessage } from '../types'

export type TranscriptMessage = Extract<ServerMessage, { type: 'transcription' }>
type TranscriptScript = 'latin' | 'nonLatin' | 'mixed' | 'none'

interface ScriptStats {
  letters: number
  latinLetters: number
  nonLatinLetters: number
  script: TranscriptScript
}

export function mergePartialText(previous: string, next: string): string {
  const prev = previous.trim()
  const incoming = next.trim()
  if (!prev) return incoming
  if (!incoming) return prev

  // Gemini Live can occasionally emit a wrong-script partial (for example a short
  // Devanagari fragment) before correcting back to English. Never concatenate
  // dominant-script mismatches into one bubble; prefer the longer fragment and let
  // a later final transcript replace it.
  const prevScript = getScriptStats(prev)
  const incomingScript = getScriptStats(incoming)
  const dominantScriptConflict =
    (prevScript.script === 'latin' && incomingScript.script === 'nonLatin') ||
    (prevScript.script === 'nonLatin' && incomingScript.script === 'latin')
  if (dominantScriptConflict && prevScript.letters >= 2 && incomingScript.letters >= 2) {
    return incoming.length >= prev.length ? incoming : prev
  }

  if (incoming.startsWith(prev)) return incoming
  if (prev.endsWith(incoming)) return prev
  if (prev.includes(incoming)) return prev

  // Suffix-prefix overlap: find the longest suffix of prev that matches
  // a prefix of incoming, then merge at the overlap point. This handles
  // real-time transcription fragments like "Hello wor" + "world" → "Hello world".
  const maxOverlap = Math.min(prev.length, incoming.length)
  for (let len = maxOverlap; len >= 1; len--) {
    if (prev.slice(-len) === incoming.slice(0, len)) {
      return prev + incoming.slice(len)
    }
  }

  return `${prev} ${incoming}`.replace(/\s+/g, ' ').trim()
}

function mergeStreamingPartialChunk(previous: string, nextChunk: string): string {
  const prevRaw = previous
  const nextRaw = nextChunk
  const prevTrim = prevRaw.trim()
  const nextTrim = nextRaw.trim()
  if (!prevTrim) return nextRaw.replace(/^\s+/, '')
  if (!nextTrim) return prevRaw

  // If the stream sends cumulative text, replace with the fuller chunk.
  if (nextTrim.startsWith(prevTrim)) return nextTrim
  if (prevTrim.startsWith(nextTrim)) return prevRaw
  if (isEquivalentTranscriptText(prevTrim, nextTrim)) return nextTrim

  // If the delta chunk is already present at the tail, ignore duplicate resend.
  if (prevRaw.endsWith(nextRaw) || prevTrim.endsWith(nextTrim)) return prevRaw

  // Overlap-aware append for fragmented deltas (for example "boo" + "king hotel").
  const maxOverlap = Math.min(prevRaw.length, nextRaw.length)
  for (let len = maxOverlap; len >= 1; len -= 1) {
    if (prevRaw.slice(-len) === nextRaw.slice(0, len)) {
      return prevRaw + nextRaw.slice(len)
    }
  }

  // Default for streaming partials: append exactly as emitted. This preserves
  // word boundaries when the chunk carries leading spaces, and avoids inserting
  // artificial spaces that can corrupt sub-word fragments.
  return prevRaw + nextRaw
}

function canonicalTranscriptText(text: string): string {
  return text
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '')
}

function isEquivalentTranscriptText(a: string, b: string): boolean {
  const left = canonicalTranscriptText(a)
  const right = canonicalTranscriptText(b)
  return Boolean(left) && left === right
}

function canonicalTranscriptLength(text: string): number {
  return canonicalTranscriptText(text).length
}

function hasMeaningfulTextOverlap(a: string, b: string): boolean {
  const left = a.trim()
  const right = b.trim()
  if (!left || !right) return false

  if (left.includes(right) || right.includes(left)) {
    return true
  }

  const leftCanonical = canonicalTranscriptText(left)
  const rightCanonical = canonicalTranscriptText(right)
  if (!leftCanonical || !rightCanonical) return false
  return leftCanonical.includes(rightCanonical) || rightCanonical.includes(leftCanonical)
}

function finalizePartialTranscriptText(previousPartial: string, incomingFinal: string): string {
  const prev = previousPartial.trim()
  const next = incomingFinal.trim()
  if (!prev) return next
  if (!next) return prev
  if (isEquivalentTranscriptText(prev, next)) return next

  // Some Live transcription streams regress to a shorter suffix right before the
  // final event (for example "Good morning how are you doing" -> "how are you doing").
  // Preserve the fuller utterance when the new final clearly overlaps.
  if (hasMeaningfulTextOverlap(prev, next)) {
    return mergePartialText(prev, next)
  }

  return next
}

function getScriptStats(text: string): ScriptStats {
  let letters = 0
  let latinLetters = 0
  let nonLatinLetters = 0

  for (const char of text.normalize('NFKC')) {
    if (!/\p{L}/u.test(char)) continue
    letters += 1
    if (/\p{Script=Latin}/u.test(char)) {
      latinLetters += 1
    } else {
      nonLatinLetters += 1
    }
  }

  if (letters < 2) {
    return {
      letters,
      latinLetters,
      nonLatinLetters,
      script: 'none',
    }
  }

  const latinRatio = latinLetters / letters
  const nonLatinRatio = nonLatinLetters / letters
  let script: TranscriptScript = 'mixed'
  if (latinRatio >= 0.75) script = 'latin'
  else if (nonLatinRatio >= 0.75) script = 'nonLatin'

  return { letters, latinLetters, nonLatinLetters, script }
}

export interface TranscriptDisplaySanitizerOptions {
  // When set, suppress obvious user transcript script anomalies after a stable
  // conversation script has been established (useful for Gemini Live auto-language
  // mis-detections on short utterances).
  preferredUserScript?: 'latin' | null
}

export function sanitizeTranscriptForDisplay(
  messages: TranscriptMessage[],
  options: TranscriptDisplaySanitizerOptions = {},
): TranscriptMessage[] {
  const preferredUserScript = options.preferredUserScript ?? null
  if (!preferredUserScript) return messages

  const sanitized: TranscriptMessage[] = []
  let seenLatinUserTurns = 0
  let seenLatinAgentTurns = 0
  let seenNonLatinAgentTurns = 0

  const hasStableLatinAgent = messages.some(message => {
    if (message.role !== 'agent' || message.partial) return false
    const stats = getScriptStats(message.text)
    return stats.letters >= 6 && stats.script === 'latin'
  })

  for (const message of messages) {
    const stats = getScriptStats(message.text)
    const isSubstantialFinal = !message.partial && stats.letters >= 6

    const latinConversationEstablished = seenLatinUserTurns >= 1 && seenLatinAgentTurns >= 1
    const likelyEnglishAgentSession = hasStableLatinAgent && seenNonLatinAgentTurns === 0
    const shouldSuppressUserScriptAnomaly =
      preferredUserScript === 'latin' &&
      message.role === 'user' &&
      stats.script === 'nonLatin' &&
      stats.letters >= 2 &&
      (latinConversationEstablished || likelyEnglishAgentSession)

    if (!shouldSuppressUserScriptAnomaly) {
      sanitized.push(message)
    }

    if (isSubstantialFinal && stats.script === 'latin') {
      if (message.role === 'user') {
        seenLatinUserTurns += 1
      } else {
        seenLatinAgentTurns += 1
      }
    } else if (isSubstantialFinal && stats.script === 'nonLatin' && message.role === 'agent') {
      seenNonLatinAgentTurns += 1
    }
  }

  return sanitized
}

export function normalizeTranscriptMessages(messages: TranscriptMessage[]): TranscriptMessage[] {
  type Role = TranscriptMessage['role']
  interface TranscriptItemMeta {
    closedByRoleSwitch: boolean
    explicitFinalSeen: boolean
  }

  const normalized: TranscriptMessage[] = []
  const meta: TranscriptItemMeta[] = []
  const activeByRole: Record<Role, number | null> = {
    user: null,
    agent: null,
  }

  const findLatestSameRoleIndex = (role: Role): number => {
    for (let i = normalized.length - 1; i >= 0; i -= 1) {
      if (normalized[i].role === role) return i
    }
    return -1
  }

  const hasInterveningFinal = (index: number): boolean => {
    for (let i = index + 1; i < normalized.length; i += 1) {
      if (!normalized[i].partial || meta[i]?.explicitFinalSeen) return true
    }
    return false
  }

  const closeActiveRole = (role: Role) => {
    const idx = activeByRole[role]
    if (idx == null) return
    if (normalized[idx].partial) {
      normalized[idx].partial = false
      meta[idx].closedByRoleSwitch = true
    }
    activeByRole[role] = null
  }

  for (const message of messages) {
    const role = message.role
    const otherRole: Role = role === 'user' ? 'agent' : 'user'
    const rawText = message.text ?? ''
    if (!rawText.trim()) continue

    // Role switch is treated as a transcript boundary. Close the other role's
    // active partial bubble so late events from that role cannot keep mutating it.
    closeActiveRole(otherRole)

    const activeIndex = activeByRole[role]
    if (activeIndex != null) {
      const active = normalized[activeIndex]
      if (message.partial) {
        active.text = mergeStreamingPartialChunk(active.text, rawText)
      } else {
        active.text = finalizePartialTranscriptText(active.text, rawText)
        active.partial = false
        meta[activeIndex].explicitFinalSeen = true
        meta[activeIndex].closedByRoleSwitch = false
        activeByRole[role] = null
      }
      continue
    }

    const latestSameRoleIndex = findLatestSameRoleIndex(role)
    const latestSameRole = latestSameRoleIndex >= 0 ? normalized[latestSameRoleIndex] : undefined
    const latestSameRoleMeta = latestSameRoleIndex >= 0 ? meta[latestSameRoleIndex] : undefined

    if (message.partial) {
      if (latestSameRole && isEquivalentTranscriptText(latestSameRole.text, rawText)) {
        // Ignore stale duplicate partials/finals.
        continue
      }

      const latestOverallIndex = normalized.length - 1
      const latestOverall = latestOverallIndex >= 0 ? normalized[latestOverallIndex] : undefined
      const latestOverallMeta = latestOverallIndex >= 0 ? meta[latestOverallIndex] : undefined

      // Gemini/ADK transfer races can emit a tiny finalized same-role stub
      // (for example "Hello! I") and then continue the real response as partials.
      // If a same-role partial arrives immediately after a very short final bubble,
      // reopen that bubble instead of rendering a duplicate stub + full response.
      if (
        latestSameRole &&
        latestSameRoleIndex === latestOverallIndex &&
        !latestSameRole.partial &&
        latestSameRoleMeta?.explicitFinalSeen &&
        canonicalTranscriptLength(latestSameRole.text) <= 12
      ) {
        latestSameRole.text = mergeStreamingPartialChunk('', rawText)
        latestSameRole.partial = true
        latestSameRoleMeta.explicitFinalSeen = false
        latestSameRoleMeta.closedByRoleSwitch = false
        activeByRole[role] = latestSameRoleIndex
        continue
      }

      // Ignore late partials that arrive after the other speaker has already taken
      // over but before that speaker produced an explicit final. This is a common
      // stream ordering issue that otherwise creates duplicated/truncated bubbles.
      if (
        latestSameRole &&
        latestSameRoleMeta?.closedByRoleSwitch &&
        latestOverall &&
        latestOverall.role !== role &&
        latestOverallMeta?.closedByRoleSwitch &&
        !latestOverallMeta.explicitFinalSeen
      ) {
        continue
      }

      normalized.push({
        ...message,
        text: rawText.replace(/^\s+/, ''),
        partial: true,
      })
      meta.push({
        closedByRoleSwitch: false,
        explicitFinalSeen: false,
      })
      activeByRole[role] = normalized.length - 1
      continue
    }

    if (latestSameRole) {
      if (latestSameRoleMeta?.closedByRoleSwitch) {
        latestSameRole.text = finalizePartialTranscriptText(latestSameRole.text, rawText)
        latestSameRole.partial = false
        latestSameRoleMeta.closedByRoleSwitch = false
        latestSameRoleMeta.explicitFinalSeen = true
        continue
      }

      if (
        isEquivalentTranscriptText(latestSameRole.text, rawText) &&
        !hasInterveningFinal(latestSameRoleIndex)
      ) {
        latestSameRole.text = rawText.trim() || latestSameRole.text
        latestSameRole.partial = false
        latestSameRoleMeta!.explicitFinalSeen = true
        continue
      }

      // Heuristic: Gemini can replay an already-finalized user transcript as an
      // exact duplicate after the agent has started/finished responding. Suppress
      // long exact duplicates to avoid duplicate bubbles in the transcript UI.
      if (
        isEquivalentTranscriptText(latestSameRole.text, rawText) &&
        hasInterveningFinal(latestSameRoleIndex) &&
        canonicalTranscriptLength(rawText) >= 8
      ) {
        continue
      }
    }

    normalized.push({
      ...message,
      text: rawText.trim(),
      partial: false,
    })
    meta.push({
      closedByRoleSwitch: false,
      explicitFinalSeen: true,
    })
  }

  return normalized
}
