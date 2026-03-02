import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DemoStep } from '../../utils/mockData'
import { DEMO_STEPS_BY_TEMPLATE, ELECTRONICS_DEMO_STEPS } from '../../utils/mockData'
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

  it('emits error message type from custom steps', () => {
    const errorSteps: DemoStep[] = [
      {
        delayMs: 0,
        message: { type: 'session_started', sessionId: 's1', industry: 'electronics' },
      },
      {
        delayMs: 100,
        message: { type: 'error', code: 'TOOL_ERROR', message: 'Something went wrong' },
      },
    ]
    const onEmit = vi.fn()
    const { result } = renderHook(() => useDemoMode({ steps: errorSteps, onEmit }))

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(0)
    })
    act(() => {
      vi.advanceTimersByTime(100)
    })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[1].type).toBe('error')
    expect(onEmit).toHaveBeenCalledTimes(2)
  })

  it('emits memory_recall message type from custom steps', () => {
    const memorySteps: DemoStep[] = [
      {
        delayMs: 0,
        message: { type: 'session_started', sessionId: 's1', industry: 'electronics' },
      },
      {
        delayMs: 100,
        message: { type: 'memory_recall', customerName: 'Ada', previousInteractions: 5 },
      },
    ]
    const onEmit = vi.fn()
    const { result } = renderHook(() => useDemoMode({ steps: memorySteps, onEmit }))

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(0)
    })
    act(() => {
      vi.advanceTimersByTime(100)
    })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[1].type).toBe('memory_recall')
  })

  it('completes full electronics 10-step demo', () => {
    const onEmit = vi.fn()
    const { result } = renderHook(() =>
      useDemoMode({ steps: ELECTRONICS_DEMO_STEPS, onEmit }),
    )

    act(() => {
      result.current.play()
      vi.advanceTimersByTime(0)
    })

    // Advance through all delays (steps at 0, 400, 800, 1200, 1600, 2000, 2400, 2800, 3200, 3600)
    for (let i = 1; i < ELECTRONICS_DEMO_STEPS.length; i += 1) {
      act(() => {
        vi.advanceTimersByTime(400)
      })
    }

    expect(result.current.messages).toHaveLength(ELECTRONICS_DEMO_STEPS.length)
    expect(onEmit).toHaveBeenCalledTimes(ELECTRONICS_DEMO_STEPS.length)
    expect(result.current.isPlaying).toBe(false)

    // Verify message types in order
    const expectedTypes = ELECTRONICS_DEMO_STEPS.map(s => s.message.type)
    const actualTypes = result.current.messages.map(m => m.type)
    expect(actualTypes).toEqual(expectedTypes)
  })

  it('resolves template-specific steps by industryTemplateId', () => {
    const templateIds = Object.keys(DEMO_STEPS_BY_TEMPLATE)

    for (const templateId of templateIds) {
      const { result } = renderHook(() => useDemoMode({ industryTemplateId: templateId }))

      act(() => {
        result.current.play()
        vi.advanceTimersByTime(0)
      })

      expect(result.current.messages.length).toBeGreaterThanOrEqual(1)
      expect(result.current.messages[0].type).toBe('session_started')

      act(() => {
        result.current.reset()
      })
    }
  })
})
