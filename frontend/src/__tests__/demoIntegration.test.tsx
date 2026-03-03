import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'

// Pre-warm dynamic imports so React.lazy resolves immediately under fake timers
beforeAll(async () => {
  await import('../components/cards/ValuationCard')
  await import('../components/cards/BookingConfirmationCard')
  await import('../components/cards/ProductCard')
})

const INDUSTRY_STORAGE_KEY = 'ekaette:onboarding:industry'

describe('Demo mode integration', () => {
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
  })

  it('plays full electronics demo and renders valuation + booking cards', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    window.history.replaceState({}, '', '/?demo=1')
    render(<App />)

    // Start the demo
    const startButton = screen.getByRole('button', { name: /start call/i })
    await act(async () => {
      startButton.click()
      vi.advanceTimersByTime(0)
    })

    // Should be in call mode (end call button visible)
    expect(screen.getByRole('button', { name: /end call/i })).toBeInTheDocument()

    // Advance through greeting transcription (400ms from session_started)
    await act(async () => {
      vi.advanceTimersByTime(500)
    })
    expect(screen.getAllByText(/Hello, I am Ekaette/i).length).toBeGreaterThan(0)

    // Advance to valuation result (1600ms total = 1100ms more)
    await act(async () => {
      vi.advanceTimersByTime(1200)
    })
    // Flush lazy import rendering
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('iPhone 14 Pro')).toBeInTheDocument()
    expect(screen.getByText('Good')).toBeInTheDocument()

    // Advance to booking confirmation (2800ms total = 1100ms more)
    await act(async () => {
      vi.advanceTimersByTime(1200)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('#EKA-2026-0421')).toBeInTheDocument()
    expect(screen.getByText('Lekki Phase 1, Lagos')).toBeInTheDocument()

    // Advance past all remaining steps (3600ms total)
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
  })

  it('does not create a WebSocket in demo mode', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    window.history.replaceState({}, '', '/?demo=1')
    render(<App />)

    const startButton = screen.getByRole('button', { name: /start call/i })
    await act(async () => {
      startButton.click()
      vi.advanceTimersByTime(0)
    })

    // No WebSocket should have been created
    const ws = (globalThis as { __lastMockWebSocket?: unknown }).__lastMockWebSocket
    expect(ws).toBeUndefined()
  })
})
