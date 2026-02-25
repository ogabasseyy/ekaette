import { describe, expect, it } from 'vitest'
import {
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
})
