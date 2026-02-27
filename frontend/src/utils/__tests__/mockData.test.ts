import { describe, expect, it } from 'vitest'
import {
  DEMO_STEPS_BY_TEMPLATE,
  ELECTRONICS_DEMO_STEPS,
  getDemoStep,
  isServerMessage,
  validateDemoSteps,
} from '../mockData'

describe('mockData', () => {
  it('contains exactly 10 demo steps', () => {
    expect(ELECTRONICS_DEMO_STEPS).toHaveLength(10)
  })

  it('validates each step against ServerMessage runtime shape', () => {
    for (const step of ELECTRONICS_DEMO_STEPS) {
      expect(step.delayMs).toBeGreaterThanOrEqual(0)
      expect(isServerMessage(step.message)).toBe(true)
    }
    expect(validateDemoSteps(ELECTRONICS_DEMO_STEPS)).toBe(true)
  })

  it('returns undefined for invalid step index', () => {
    expect(getDemoStep(-1)).toBeUndefined()
    expect(getDemoStep(999)).toBeUndefined()
    expect(getDemoStep(1.5 as unknown as number)).toBeUndefined()
    expect(getDemoStep(Number.NaN)).toBeUndefined()
    expect(getDemoStep('1' as unknown as number)).toBeUndefined()
  })

  it('rejects invalid runtime message shapes', () => {
    expect(isServerMessage({ type: 'transcription', role: 'agent' })).toBe(false)
    expect(isServerMessage({ type: 'booking_confirmation', confirmationId: 'a' })).toBe(false)
    expect(isServerMessage({ type: 'unknown' })).toBe(false)
  })

  it('all 7 template demo step arrays have valid ServerMessage shapes', () => {
    const entries = Object.entries(DEMO_STEPS_BY_TEMPLATE)
    expect(entries.length).toBe(7)

    for (const [templateId, steps] of entries) {
      expect(steps.length).toBeGreaterThan(0)
      for (const step of steps) {
        expect(step.delayMs).toBeGreaterThanOrEqual(0)
        expect(
          isServerMessage(step.message),
          `Invalid message at template "${templateId}": ${JSON.stringify(step.message)}`,
        ).toBe(true)
      }
      expect(validateDemoSteps(steps)).toBe(true)
    }
  })

  it('electronics demo covers diverse message types', () => {
    const types = new Set(ELECTRONICS_DEMO_STEPS.map(s => s.message.type))
    expect(types.has('session_started')).toBe(true)
    expect(types.has('transcription')).toBe(true)
    expect(types.has('image_received')).toBe(true)
    expect(types.has('valuation_result')).toBe(true)
    expect(types.has('booking_confirmation')).toBe(true)
    expect(types.has('agent_transfer')).toBe(true)
  })

  it('all demo step arrays start with session_started at delayMs 0', () => {
    for (const [templateId, steps] of Object.entries(DEMO_STEPS_BY_TEMPLATE)) {
      const first = steps[0]
      expect(first, `Template "${templateId}" has no steps`).toBeDefined()
      expect(first.delayMs).toBe(0)
      expect(first.message.type).toBe('session_started')
    }
  })
})
