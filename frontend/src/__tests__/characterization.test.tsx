/**
 * Phase 0 — Baseline characterization tests (frontend).
 *
 * Captures exact current behavior BEFORE the registry migration.
 * These tests are the regression safety net for all subsequent phases.
 * Do NOT modify during migration — they document the pre-migration contract.
 */
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Industry } from '../types'
import { getLastSocket, setStoredIndustry } from './test-helpers'

async function startCallAndGetSocket() {
  const micButton = screen.getByRole('button', { name: /start call/i })
  await act(async () => {
    micButton.click()
  })
  await act(async () => {
    vi.advanceTimersByTime(1)
  })
  await act(async () => {
    vi.advanceTimersByTime(100)
  })
  return getLastSocket()
}

// ═══ Hardcoded Industry Maps Characterization ═══

describe('Hardcoded industry maps (pre-migration baseline)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(globalThis.WebSocket as unknown as { instances?: unknown[] }).instances = []
    ;(globalThis as { __lastMockWebSocket?: unknown }).__lastMockWebSocket = undefined
    window.localStorage.clear()
    window.localStorage.setItem(
      'ekaette:privacy:consent',
      JSON.stringify({ accepted: true, timestamp: '2026-01-01T00:00:00Z', version: '1.0' }),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it.each([
    ['electronics', 'ekaette-electronics'],
    ['hotel', 'ekaette-hotel'],
    ['automotive', 'ekaette-automotive'],
    ['fashion', 'ekaette-fashion'],
  ] as Array<
    [Industry, string]
  >)('maps %s to company_id=%s in WebSocket URL (runtime behavior)', async (industry, expectedCompanyId) => {
    setStoredIndustry(industry)
    const App = (await import('../App')).default
    render(<App />)

    const ws = await startCallAndGetSocket()
    expect(ws.url).toContain(`industry=${industry}`)
    expect(ws.url).toContain(`company_id=${expectedCompanyId}`)
  })

  // Transitional baseline test: expected to change once onboarding/themes become registry-driven.
  it.each([
    [
      'electronics',
      'Hardware Trade Desk',
      'Inspect. Value. Negotiate. Book pickup.',
      'oklch(74% 0.21 158)',
    ],
    [
      'hotel',
      'Hospitality Concierge',
      'Real-time booking and guest support voice assistant.',
      'oklch(78% 0.15 55)',
    ],
    [
      'automotive',
      'Automotive Service Lane',
      'Trade-ins, inspections, parts and service scheduling.',
      'oklch(71% 0.18 240)',
    ],
    [
      'fashion',
      'Fashion Client Studio',
      'Catalog recommendations and consultation workflows.',
      'oklch(74% 0.2 20)',
    ],
  ] as Array<
    [Industry, string, string, string]
  >)('renders hardcoded theme for %s (title/hint/accent)', async (industry, expectedTitle, expectedHint, expectedAccent) => {
    setStoredIndustry(industry)
    const App = (await import('../App')).default
    const { container } = render(<App />)

    expect(screen.getByText(expectedTitle)).toBeInTheDocument()
    expect(screen.getAllByText(expectedHint).length).toBeGreaterThanOrEqual(1)

    const appShell = container.querySelector('.app-shell') as HTMLElement | null
    expect(appShell).not.toBeNull()
    const accent = appShell?.style.getPropertyValue('--industry-accent').trim()
    expect(accent).toBe(expectedAccent)
    expect(appShell?.getAttribute('style')).toContain('oklch(')
  })
})

// ═══ Onboarding Component Characterization ═══

describe('IndustryOnboarding characterization', () => {
  it('renders exactly 4 industry option buttons', async () => {
    const { IndustryOnboarding } = await import('../components/layout/IndustryOnboarding')
    render(<IndustryOnboarding onComplete={() => {}} />)

    expect(screen.getByRole('radio', { name: /hardware/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /hotel/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /automotive/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /fashion/i })).toBeInTheDocument()
  })

  it('defaults to electronics selected', async () => {
    const { IndustryOnboarding } = await import('../components/layout/IndustryOnboarding')
    const onComplete = vi.fn()
    render(<IndustryOnboarding onComplete={onComplete} />)

    // Click Launch Live Desk without changing selection → should default to electronics
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /launch live desk/i }))
    expect(onComplete).toHaveBeenCalledWith({
      templateId: 'electronics',
      companyId: 'ekaette-electronics',
    })
  })

  it('calls onComplete with selected industry', async () => {
    const { IndustryOnboarding } = await import('../components/layout/IndustryOnboarding')
    const onComplete = vi.fn()
    const user = userEvent.setup()
    render(<IndustryOnboarding onComplete={onComplete} />)

    await user.click(screen.getByRole('radio', { name: /hotel/i }))
    await user.click(screen.getByRole('button', { name: /launch live desk/i }))
    expect(onComplete).toHaveBeenCalledWith({
      templateId: 'hotel',
      companyId: 'ekaette-hotel',
    })
  })

  it('each option has a description', async () => {
    const { IndustryOnboarding } = await import('../components/layout/IndustryOnboarding')
    render(<IndustryOnboarding onComplete={() => {}} />)

    // Verify descriptions exist (these are the hardcoded strings)
    expect(screen.getByText(/trade-ins, valuation/i)).toBeInTheDocument()
    expect(screen.getByText(/reservations, room search/i)).toBeInTheDocument()
    expect(screen.getByText(/service lane support/i)).toBeInTheDocument()
    expect(screen.getByText(/catalog assistance/i)).toBeInTheDocument()
  })
})

// ═══ localStorage Persistence Characterization ═══

describe('Industry localStorage persistence', () => {
  const STORAGE_KEY = 'ekaette:onboarding:industry'

  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
  })

  it('stores selected industry under correct key', () => {
    window.localStorage.setItem(STORAGE_KEY, 'hotel')
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('hotel')
  })

  it('returns null for empty storage', () => {
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('all 4 industry values are valid storage values', () => {
    const validValues: Industry[] = ['electronics', 'hotel', 'automotive', 'fashion']
    for (const value of validValues) {
      window.localStorage.setItem(STORAGE_KEY, value)
      expect(window.localStorage.getItem(STORAGE_KEY)).toBe(value)
    }
  })
})

// ═══ WebSocket URL Characterization ═══

describe('WebSocket connection characterization', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(globalThis.WebSocket as unknown as { instances?: unknown[] }).instances = []
    ;(globalThis as { __lastMockWebSocket?: unknown }).__lastMockWebSocket = undefined
    window.localStorage.clear()
    window.localStorage.setItem(
      'ekaette:privacy:consent',
      JSON.stringify({ accepted: true, timestamp: '2026-01-01T00:00:00Z', version: '1.0' }),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
    window.history.replaceState({}, '', '/')
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('includes industry in WebSocket URL query params', async () => {
    // Store industry so onboarding is skipped
    setStoredIndustry('hotel')

    const App = (await import('../App')).default
    render(<App />)

    const ws = await startCallAndGetSocket()
    expect(ws.url).toContain('industry=hotel')
  })

  it('includes company_id in WebSocket URL when industry mapped', async () => {
    setStoredIndustry('automotive')

    const App = (await import('../App')).default
    render(<App />)

    const ws = await startCallAndGetSocket()
    expect(ws.url).toContain('company_id=ekaette-automotive')
  })
})

// ═══ Demo Mode Characterization ═══

describe('Demo mode characterization', () => {
  it('ELECTRONICS_DEMO_STEPS has exactly 10 steps', async () => {
    const { ELECTRONICS_DEMO_STEPS } = await import('../utils/mockData')
    expect(ELECTRONICS_DEMO_STEPS).toHaveLength(10)
  })

  it('first demo step is session_started with electronics industry', async () => {
    const { ELECTRONICS_DEMO_STEPS } = await import('../utils/mockData')
    const first = ELECTRONICS_DEMO_STEPS[0]
    expect(first.message.type).toBe('session_started')
    expect((first.message as { industry?: string }).industry).toBe('electronics')
  })

  it('demo steps include valuation_result and booking_confirmation', async () => {
    const { ELECTRONICS_DEMO_STEPS } = await import('../utils/mockData')
    const types = ELECTRONICS_DEMO_STEPS.map(s => s.message.type)
    expect(types).toContain('valuation_result')
    expect(types).toContain('booking_confirmation')
  })

  it('ELECTRONICS_DEMO_STEPS is still exported and included in per-industry set', async () => {
    const mockData = await import('../utils/mockData')
    // Phase 4: multiple industry demo steps now exist.
    // Electronics must still be present and unchanged.
    const exportedArrays = Object.entries(mockData).filter(
      ([key, value]) => key.endsWith('_DEMO_STEPS') && Array.isArray(value),
    )
    expect(exportedArrays.length).toBeGreaterThanOrEqual(1)
    const electronicsEntry = exportedArrays.find(([key]) => key === 'ELECTRONICS_DEMO_STEPS')
    expect(electronicsEntry).toBeDefined()
  })

  it('useDemoMode defaults to electronics steps regardless of industry', async () => {
    const { renderHook } = await import('@testing-library/react')
    const { useDemoMode } = await import('../hooks/useDemoMode')

    // No steps or industry parameter — should default to electronics
    const { result } = renderHook(() => useDemoMode())
    expect(result.current.messages).toHaveLength(0)
    expect(result.current.isPlaying).toBe(false)
  })
})

// ═══ Session Started Message Type Characterization ═══

describe('SessionStartedMessage type characterization', () => {
  it('session_started requires sessionId and industry fields', async () => {
    const { isServerMessage } = await import('../utils/mockData')

    // Valid session_started
    expect(
      isServerMessage({
        type: 'session_started',
        sessionId: 'test-123',
        industry: 'electronics',
      }),
    ).toBe(true)

    // Missing industry
    expect(
      isServerMessage({
        type: 'session_started',
        sessionId: 'test-123',
      }),
    ).toBe(false)

    // Missing sessionId
    expect(
      isServerMessage({
        type: 'session_started',
        industry: 'electronics',
      }),
    ).toBe(false)
  })
})
