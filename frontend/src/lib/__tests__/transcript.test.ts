import { describe, it, expect } from 'vitest'
import {
  mergePartialText,
  normalizeTranscriptMessages,
  sanitizeTranscriptForDisplay,
} from '../transcript'
import type { TranscriptMessage } from '../transcript'

describe('mergePartialText', () => {
  it('extends when incoming starts with previous', () => {
    expect(mergePartialText('Hello', 'Hello world')).toBe('Hello world')
  })

  it('replaces when incoming is entirely different', () => {
    expect(mergePartialText('Hello', 'Goodbye')).toBe('Hello Goodbye')
  })

  it('returns incoming when previous is empty', () => {
    expect(mergePartialText('', 'Hello world')).toBe('Hello world')
  })

  it('returns previous when incoming is empty', () => {
    expect(mergePartialText('Hello', '')).toBe('Hello')
  })

  it('deduplicates when previous ends with incoming', () => {
    expect(mergePartialText('Hello world', 'world')).toBe('Hello world')
  })

  it('deduplicates when previous contains incoming', () => {
    expect(mergePartialText('Hello great world', 'great')).toBe('Hello great world')
  })

  it('merges at suffix-prefix overlap', () => {
    expect(mergePartialText('Hello wor', 'world')).toBe('Hello world')
  })

  it('merges at multi-word suffix-prefix overlap', () => {
    expect(mergePartialText('Hello world how', 'how are you')).toBe('Hello world how are you')
  })

  it('merges overlapping transcription fragments', () => {
    expect(mergePartialText('I can help you with', 'with that today')).toBe(
      'I can help you with that today',
    )
  })

  it('handles single-character overlap', () => {
    expect(mergePartialText('Hello', 'o world')).toBe('Hello world')
  })

  it('still space-joins genuinely non-overlapping text', () => {
    expect(mergePartialText('Hello', 'Goodbye')).toBe('Hello Goodbye')
  })

  it('does not concatenate dominant-script mismatches in partials', () => {
    expect(mergePartialText('hello booking', 'मुझे मदद चाहिए')).toBe('मुझे मदद चाहिए')
  })
})

describe('normalizeTranscriptMessages', () => {
  it('merges consecutive partial messages from same role', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'agent', text: 'Hello', partial: true },
      { type: 'transcription', role: 'agent', text: 'Hello world', partial: true },
    ]
    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe('Hello world')
    expect(result[0].partial).toBe(true)
  })

  it('appends delta-style partial chunks without corrupting sub-word fragments', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: "I'm", partial: true },
      { type: 'transcription', role: 'user', text: ' thi', partial: true },
      { type: 'transcription', role: 'user', text: 'nking', partial: true },
      { type: 'transcription', role: 'user', text: ' of', partial: true },
      { type: 'transcription', role: 'user', text: ' boo', partial: true },
      { type: 'transcription', role: 'user', text: 'king the hotel.', partial: true },
      { type: 'transcription', role: 'user', text: ' king the hotel.', partial: false },
    ]

    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe("I'm thinking of booking the hotel.")
    expect(result[0].partial).toBe(false)
  })

  it('finalizes partial when final message arrives', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'agent', text: 'Hel', partial: true },
      { type: 'transcription', role: 'agent', text: 'Hello', partial: true },
      { type: 'transcription', role: 'agent', text: 'Hello!', partial: false },
    ]
    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe('Hello!')
    expect(result[0].partial).toBe(false)
  })

  it('preserves fuller partial text when final regresses to a suffix', () => {
    const messages: TranscriptMessage[] = [
      {
        type: 'transcription',
        role: 'user',
        text: 'Good morning how are you doing',
        partial: true,
      },
      {
        type: 'transcription',
        role: 'user',
        text: 'how are you doing',
        partial: false,
      },
    ]

    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe('Good morning how are you doing')
    expect(result[0].partial).toBe(false)
  })

  it('returns empty array for empty input', () => {
    expect(normalizeTranscriptMessages([])).toEqual([])
  })

  it('keeps separate entries for different roles', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'Hi', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hello!', partial: false },
    ]
    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(2)
    expect(result[0].role).toBe('user')
    expect(result[1].role).toBe('agent')
  })

  it('finalizes active partial bubble on role switch', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'agent', text: 'Let me check', partial: true },
      { type: 'transcription', role: 'user', text: 'Thanks', partial: false },
    ]
    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(2)
    expect(result[0].partial).toBe(false)
    expect(result[0].text).toBe('Let me check')
  })

  it('collapses late finals across role switches (prevents duplicated bubbles)', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'he llo good morning', partial: true },
      { type: 'transcription', role: 'agent', text: 'Hello! I am doing great', partial: true },
      { type: 'transcription', role: 'user', text: 'hello good morning', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hello! I’m doing great.', partial: false },
    ]

    const result = normalizeTranscriptMessages(messages)

    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({
      role: 'user',
      partial: false,
      text: 'hello good morning',
    })
    expect(result[1]).toMatchObject({
      role: 'agent',
      partial: false,
      text: 'Hello! I’m doing great.',
    })
  })

  it('ignores late partial rewrites after role switch and keeps finalized prior bubble', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'Good morning how are', partial: true },
      { type: 'transcription', role: 'agent', text: 'Hello! I am well', partial: true },
      { type: 'transcription', role: 'user', text: 'you doing', partial: true }, // stale late chunk
      { type: 'transcription', role: 'agent', text: 'Hello! I am well today.', partial: false },
    ]

    const result = normalizeTranscriptMessages(messages)

    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({
      role: 'user',
      text: 'Good morning how are',
      partial: false,
    })
    expect(result[1]).toMatchObject({
      role: 'agent',
      text: 'Hello! I am well today.',
      partial: false,
    })
  })

  it('skips duplicate finals for the same role and utterance', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'agent', text: 'How can I help?', partial: false },
      { type: 'transcription', role: 'agent', text: 'How can I help', partial: false },
    ]

    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe('How can I help')
  })

  it('does not collapse the same phrase repeated in a later turn', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'hello', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hi there!', partial: false },
      { type: 'transcription', role: 'user', text: 'hello', partial: false },
    ]

    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(3)
    expect(result[0].text).toBe('hello')
    expect(result[2].text).toBe('hello')
  })

  it('suppresses long exact duplicate finals replayed after an agent turn', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'Hi good morning.', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hello! How can I help you today?', partial: false },
      { type: 'transcription', role: 'user', text: 'Hi good morning.', partial: false }, // stale replay
      { type: 'transcription', role: 'user', text: "I'm thinking of booking a hotel.", partial: false },
    ]

    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(3)
    expect(result[0].text).toBe('Hi good morning.')
    expect(result[1].text).toBe('Hello! How can I help you today?')
    expect(result[2].text).toBe("I'm thinking of booking a hotel.")
  })

  it('reopens a short same-role finalized stub when partials continue immediately', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'agent', text: 'Hello! I', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hello! Good', partial: true },
      { type: 'transcription', role: 'agent', text: ' morning.', partial: true },
      {
        type: 'transcription',
        role: 'agent',
        text: 'Hello! Good morning. How can I assist you today?',
        partial: false,
      },
    ]

    const result = normalizeTranscriptMessages(messages)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe('Hello! Good morning. How can I assist you today?')
    expect(result[0].partial).toBe(false)
  })
})

describe('sanitizeTranscriptForDisplay', () => {
  it('suppresses non-latin user transcript anomalies after latin conversation is established', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'I need a booking for next week', partial: false },
      { type: 'transcription', role: 'agent', text: 'Sure, what date works best?', partial: false },
      { type: 'transcription', role: 'user', text: 'मुझे सर दर्द हो रही है', partial: false },
      { type: 'transcription', role: 'agent', text: 'Understood, can you share your preferred date?', partial: false },
    ]

    const result = sanitizeTranscriptForDisplay(messages, { preferredUserScript: 'latin' })

    expect(result).toHaveLength(3)
    expect(result.some(msg => msg.text.includes('मुझे'))).toBe(false)
    expect(result.at(-1)?.role).toBe('agent')
  })

  it('suppresses a non-latin user anomaly once agent replies in stable latin', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'హాయ్ గుడ్ మార్నింగ్', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hello! Good morning! How can I help you today?', partial: false },
    ]

    const result = sanitizeTranscriptForDisplay(messages, { preferredUserScript: 'latin' })
    expect(result).toHaveLength(1)
    expect(result[0].role).toBe('agent')
  })

  it('does not suppress non-latin transcripts when conversation starts non-latin', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'नमस्ते मुझे मदद चाहिए', partial: false },
      { type: 'transcription', role: 'agent', text: 'नमस्ते, मैं कैसे मदद कर सकता हूँ?', partial: false },
    ]

    const result = sanitizeTranscriptForDisplay(messages, { preferredUserScript: 'latin' })
    expect(result).toHaveLength(2)
  })

  it('suppresses non-latin partials after latin conversation lock', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'hello there', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hi, how can I help?', partial: false },
      { type: 'transcription', role: 'user', text: 'जीत', partial: true },
      { type: 'transcription', role: 'user', text: 'hello there', partial: false },
    ]

    const result = sanitizeTranscriptForDisplay(messages, { preferredUserScript: 'latin' })
    expect(result).toHaveLength(3)
    expect(result.some(msg => msg.partial && msg.text === 'जीत')).toBe(false)
  })
})
