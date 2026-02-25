import { describe, it, expect } from 'vitest'
import { formatDuration, prettyAgentName, formatNaira } from '../format'

describe('formatDuration', () => {
  it('formats zero seconds as 00:00', () => {
    expect(formatDuration(0)).toBe('00:00')
  })

  it('formats 61 seconds as 01:01', () => {
    expect(formatDuration(61)).toBe('01:01')
  })

  it('formats 3600 seconds as 60:00', () => {
    expect(formatDuration(3600)).toBe('60:00')
  })

  it('pads single-digit seconds', () => {
    expect(formatDuration(5)).toBe('00:05')
  })

  it('pads single-digit minutes', () => {
    expect(formatDuration(90)).toBe('01:30')
  })

  it('clamps negative values to 00:00', () => {
    expect(formatDuration(-5)).toBe('00:00')
  })

  it('floors fractional seconds', () => {
    expect(formatDuration(61.9)).toBe('01:01')
  })
})

describe('prettyAgentName', () => {
  it('converts snake_case agent name to title case', () => {
    expect(prettyAgentName('valuation_agent')).toBe('Valuation Agent')
  })

  it('returns empty string for empty input', () => {
    expect(prettyAgentName('')).toBe('')
  })

  it('handles single word', () => {
    expect(prettyAgentName('router')).toBe('Router')
  })

  it('handles multiple underscores', () => {
    expect(prettyAgentName('ekaette_support_agent')).toBe('Ekaette Support Agent')
  })
})

describe('formatNaira', () => {
  it('formats a typical price with NGN symbol', () => {
    const result = formatNaira(185000)
    expect(result).toContain('185,000')
  })

  it('formats zero', () => {
    const result = formatNaira(0)
    expect(result).toContain('0')
  })

  it('returns a string containing the ₦ or NGN currency indicator', () => {
    const result = formatNaira(1000)
    expect(result).toMatch(/₦|NGN/)
  })

  it('formats large values with commas', () => {
    const result = formatNaira(1500000)
    expect(result).toContain('1,500,000')
  })
})
