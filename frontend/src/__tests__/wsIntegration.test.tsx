import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { getLastSocket, sendServerMessage } from './test-helpers'

// Pre-warm dynamic imports so React.lazy resolves immediately under fake timers
beforeAll(async () => {
  await import('../components/cards/ValuationCard')
  await import('../components/cards/BookingConfirmationCard')
  await import('../components/cards/ProductCard')
})

async function connectCall() {
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
  await act(async () => {
    await Promise.resolve()
  })
}

const INDUSTRY_STORAGE_KEY = 'ekaette:onboarding:industry'

describe('WebSocket → UI integration', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(globalThis.WebSocket as unknown as { instances?: unknown[] }).instances = []
    ;(globalThis as { __lastMockWebSocket?: MockSocket }).__lastMockWebSocket = undefined
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

  it('renders agent transcription in transcript panel', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'agent',
        text: 'Welcome, how can I help?',
        partial: false,
      })
    })

    expect(screen.getAllByText('Welcome, how can I help?').length).toBeGreaterThan(0)
  })

  it('renders user transcription in transcript panel', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'user',
        text: 'I want to trade in my phone',
        partial: false,
      })
    })

    expect(screen.getAllByText('I want to trade in my phone').length).toBeGreaterThan(0)
  })

  it('stores image_received message without crashing', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'image_received',
        status: 'analyzing',
      })
    })

    // image_received without previewUrl doesn't render an ImagePreview,
    // but subsequent messages should still work
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'agent',
        text: 'Analyzing your device image...',
        partial: false,
      })
    })
    expect(screen.getAllByText('Analyzing your device image...').length).toBeGreaterThan(0)
  })

  it('updates active agent on agent_transfer', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'agent_transfer',
        from: 'ekaette_router',
        to: 'valuation_agent',
      })
    })

    // Transfer notice should NOT appear in transcript
    expect(screen.queryByText(/Transferring to/i)).not.toBeInTheDocument()

    // Send an agent_status to confirm the transfer took effect
    await act(async () => {
      sendServerMessage(ws, {
        type: 'agent_status',
        agent: 'valuation_agent',
        status: 'active',
      })
    })
    expect(screen.getByText(/Valuation Agent/i)).toBeInTheDocument()

    // Subsequent messages should still render correctly
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'agent',
        text: 'Let me assess your device.',
        partial: false,
      })
    })

    expect(screen.getAllByText('Let me assess your device.').length).toBeGreaterThan(0)
  })

  it('renders multiple message types in sequence', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()

    // Session started
    await act(async () => {
      sendServerMessage(ws, {
        type: 'session_started',
        sessionId: 'test-session',
        industry: 'electronics',
      })
    })

    // Agent greeting
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'agent',
        text: 'Hello! What device do you have?',
        partial: false,
      })
    })

    // User response
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'user',
        text: 'iPhone 14 Pro',
        partial: false,
      })
    })

    // Valuation result
    await act(async () => {
      sendServerMessage(ws, {
        type: 'valuation_result',
        deviceName: 'iPhone 14 Pro',
        condition: 'Excellent',
        price: 220000,
        currency: 'NGN',
        details: 'Pristine condition',
        negotiable: true,
      })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    // Verify full sequence rendered
    expect(screen.getAllByText('Hello! What device do you have?').length).toBeGreaterThan(0)
    expect(screen.getAllByText('iPhone 14 Pro').length).toBeGreaterThan(0)
    expect(screen.getByText('Excellent')).toBeInTheDocument()
  })
})
