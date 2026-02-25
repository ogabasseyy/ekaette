import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import App from '../App'
import type { ServerMessage } from '../types'

// Pre-warm dynamic import cache so React.lazy resolves immediately under fake timers
beforeAll(async () => {
  await import('../components/cards/ValuationCard')
  await import('../components/cards/BookingConfirmationCard')
  await import('../components/cards/ProductCard')
})

interface MockSocket {
  url: string
  binaryType: string
  sent: Array<string | ArrayBuffer>
  readyState: number
  onopen: ((ev: Event) => void) | null
  onclose: ((ev: CloseEvent) => void) | null
  onerror: ((ev: Event) => void) | null
  onmessage: ((ev: MessageEvent) => void) | null
  close: () => void
  send: (data: string | ArrayBuffer) => void
}

function getLastSocket(): MockSocket {
  const ws = (
    globalThis as {
      __lastMockWebSocket?: MockSocket
    }
  ).__lastMockWebSocket
  if (!ws) {
    throw new Error('Expected mock websocket instance')
  }
  return ws
}

function sendServerMessage(ws: MockSocket, message: ServerMessage) {
  ws.onmessage?.(
    new MessageEvent('message', {
      data: JSON.stringify(message),
    }),
  )
}

/**
 * Establish WebSocket connection through the App UI.
 * Must interleave timer advancement with React re-renders so that
 * socketStateRef gets updated before the polling interval checks it.
 */
async function connectCall() {
  const micButton = screen.getByRole('button', { name: /start call/i })
  await act(async () => {
    micButton.click()
  })
  // Fire MockWebSocket's onopen (queued via setTimeout(0))
  await act(async () => {
    vi.advanceTimersByTime(1)
  })
  // Allow React to re-render (updates socketStateRef.current)
  // Then fire the polling setInterval (50ms) which checks connection state
  await act(async () => {
    vi.advanceTimersByTime(100)
  })
  // Flush remaining microtasks from audio.initPlayer/startRecording
  await act(async () => {
    await Promise.resolve()
  })
}

const INDUSTRY_STORAGE_KEY = 'ekaette:onboarding:industry'

describe('App', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(globalThis.WebSocket as unknown as { instances?: unknown[] }).instances = []
    ;(
      globalThis as {
        __lastMockWebSocket?: MockSocket
      }
    ).__lastMockWebSocket = undefined
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders IndustryOnboarding when no industry in localStorage', () => {
    render(<App />)
    expect(screen.getByText('Choose Your Service Industry')).toBeTruthy()
  })

  it('renders main layout after industry is stored', () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    expect(screen.queryByText('Choose Your Service Industry')).toBeNull()
    expect(screen.getByText('Electronics Trade Desk')).toBeTruthy()
  })

  it('includes company_id in WebSocket URL when connecting', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)

    const micButton = screen.getByRole('button', { name: /start call/i })
    await act(async () => {
      micButton.click()
    })

    const ws = getLastSocket()
    expect(ws.url).toContain('company_id=ekaette-electronics')
  })

  it('renders ValuationCard when valuation_result message received', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'valuation_result',
        deviceName: 'iPhone 14 Pro',
        condition: 'Good',
        price: 185000,
        currency: 'NGN',
        details: 'Minor scratches on screen',
        negotiable: true,
      })
    })
    // Flush lazy import + Suspense re-render
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('iPhone 14 Pro')).toBeTruthy()
    expect(screen.getByText('Good')).toBeTruthy()
  })

  it('renders BookingConfirmationCard when booking_confirmation received', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'booking_confirmation',
        confirmationId: 'BK-12345',
        date: '2026-03-01',
        time: '10:00 AM',
        location: 'Ikeja, Lagos',
        service: 'Device Pickup',
      })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('#BK-12345')).toBeTruthy()
    expect(screen.getByText('Ikeja, Lagos')).toBeTruthy()
  })

  it('renders error toast when error message received', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'error',
        code: 'TOOL_ERROR',
        message: 'Unable to process image',
      })
    })

    expect(screen.getByText('Unable to process image')).toBeTruthy()
  })

  it('auto-dismisses error toast after 8 seconds', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'error',
        code: 'TOOL_ERROR',
        message: 'Temporary error',
      })
    })

    expect(screen.getByText('Temporary error')).toBeTruthy()

    // Advance past the auto-dismiss timeout (8 seconds)
    await act(async () => {
      vi.advanceTimersByTime(9000)
    })

    expect(screen.queryByText('Temporary error')).toBeNull()
  })

  it('renders ProductCards when product_recommendation received', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'product_recommendation',
        products: [
          {
            name: 'iPhone 15',
            price: 450000,
            currency: 'NGN',
            available: true,
            description: 'Latest model',
          },
          {
            name: 'Samsung Galaxy S24',
            price: 380000,
            currency: 'NGN',
            available: false,
            description: 'Flagship Android',
          },
        ],
      })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('iPhone 15')).toBeTruthy()
    expect(screen.getByText('Samsung Galaxy S24')).toBeTruthy()
  })

  it('does not show agent transfer message in transcript', async () => {
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

    expect(screen.queryByText(/Transferring to Valuation Agent/i)).toBeNull()
  })

  it('keeps later transcripts without injecting transfer notice', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'agent_transfer',
        from: 'ekaette_router',
        to: 'booking_agent',
      })
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'agent',
        text: 'I can help you book that.',
        partial: false,
      })
    })

    expect(screen.queryByText(/Transferring to Booking Agent/i)).toBeNull()
    expect(screen.getAllByText(/I can help you book that\./i).length).toBeGreaterThan(0)
  })

  it('surfaces memory recall context in voice panel', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'memory_recall',
        customerName: 'Ada',
        previousInteractions: 3,
      })
    })

    expect(screen.getByText(/Context restored for Ada: 3 prior interactions/i)).toBeTruthy()
  })
})
