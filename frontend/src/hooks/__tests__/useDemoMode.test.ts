import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DemoStep } from '../../utils/mockData'
import { useDemoMode } from '../useDemoMode'

const STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: { type: 'session_started', sessionId: 's1', industry: 'electronics' },
  },
  {
    delayMs: 100,
    message: { type: 'transcription', role: 'agent', text: 'Hello', partial: false },
  },
  {
    delayMs: 200,
    message: { type: 'agent_status', agent: 'support_agent', status: 'idle' },
  },
]

describe('useDemoMode', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts sequence on play and emits messages in order', () => {
    const onEmit = vi.fn()
    const { result } = renderHook(() => useDemoMode({ steps: STEPS, onEmit }))

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(0)
    })
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].type).toBe('session_started')

    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[1].type).toBe('transcription')

    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(result.current.messages).toHaveLength(3)
    expect(result.current.messages[2].type).toBe('agent_status')
    expect(onEmit).toHaveBeenCalledTimes(3)
  })

  it('pauses and resumes from current position', () => {
    const { result } = renderHook(() => useDemoMode({ steps: STEPS }))

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(0)
    })
    expect(result.current.messages).toHaveLength(1)

    act(() => {
      result.current.pause()
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.isPaused).toBe(true)

    act(() => {
      result.current.resume()
      vi.advanceTimersByTime(100)
    })
    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[1].type).toBe('transcription')
  })

  it('resume preserves remaining delay instead of restarting full step delay', () => {
    const { result } = renderHook(() => useDemoMode({ steps: STEPS }))

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(0)
    })
    expect(result.current.messages).toHaveLength(1)

    act(() => {
      vi.advanceTimersByTime(40)
      result.current.pause()
    })
    expect(result.current.isPaused).toBe(true)

    act(() => {
      result.current.resume()
      vi.advanceTimersByTime(59)
    })
    expect(result.current.messages).toHaveLength(1)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[1].type).toBe('transcription')
  })

  it('reset clears state and goes back to step 0', () => {
    const { result } = renderHook(() => useDemoMode({ steps: STEPS }))

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(100)
    })
    expect(result.current.messages.length).toBeGreaterThan(0)

    act(() => {
      result.current.reset()
    })
    expect(result.current.currentStep).toBe(0)
    expect(result.current.messages).toHaveLength(0)
    expect(result.current.isPlaying).toBe(false)
  })

  it('can replay after reset', () => {
    const onEmit = vi.fn()
    const { result } = renderHook(() => useDemoMode({ steps: STEPS, onEmit }))

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(100)
    })
    expect(result.current.messages.length).toBeGreaterThan(0)

    act(() => {
      result.current.reset()
    })
    expect(result.current.currentStep).toBe(0)
    expect(result.current.messages).toEqual([])
    expect(result.current.isPlaying).toBe(false)

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(0)
      vi.advanceTimersByTime(100)
      vi.advanceTimersByTime(100)
    })
    expect(result.current.messages).toHaveLength(3)
    expect(result.current.messages[0].type).toBe('session_started')
    expect(result.current.messages[1].type).toBe('transcription')
    expect(result.current.messages[2].type).toBe('agent_status')
    expect(onEmit).toHaveBeenCalledTimes(5)
  })
})
