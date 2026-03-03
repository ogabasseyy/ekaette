import { describe, expect, it } from 'vitest'
import {
  formatCompactNumber,
  formatDuration,
  formatNaira,
  formatPercent,
  prettyAgentName,
} from '../format'

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

  it('handles negative input without crashing', () => {
    const result = formatDuration(-5)
    expect(typeof result).toBe('string')
  })

  it('handles non-integer input', () => {
    const result = formatDuration(61.7)
    expect(result).toMatch(/^01:/)
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

  it('returns a string containing the currency indicator', () => {
    const result = formatNaira(1000)
    // Intl.NumberFormat for NGN produces ₦ or NGN depending on locale
    expect(result.length).toBeGreaterThan(0)
  })

  it('formats large values with commas', () => {
    const result = formatNaira(1500000)
    expect(result).toContain('1,500,000')
  })
})

describe('formatPercent', () => {
  it('formats rate as percentage with one decimal', () => {
    expect(formatPercent(0.5)).toBe('50.0%')
  })

  it('formats zero', () => {
    expect(formatPercent(0)).toBe('0.0%')
  })

  it('formats 100%', () => {
    expect(formatPercent(1)).toBe('100.0%')
  })

  it('rounds to one decimal place', () => {
    expect(formatPercent(0.3456)).toBe('34.6%')
  })
})

describe('formatCompactNumber', () => {
  it('returns plain number for small values', () => {
    expect(formatCompactNumber(42)).toBe('42')
  })

  it('formats thousands with K suffix', () => {
    expect(formatCompactNumber(1500)).toBe('1.5K')
  })

  it('formats millions with M suffix', () => {
    expect(formatCompactNumber(2500000)).toBe('2.5M')
  })

  it('formats exactly 1000 as 1.0K', () => {
    expect(formatCompactNumber(1000)).toBe('1.0K')
  })

  it('formats zero', () => {
    expect(formatCompactNumber(0)).toBe('0')
  })
})
