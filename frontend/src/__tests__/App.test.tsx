import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
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
 * The socket mock opens on setTimeout(0), then we flush audio startup microtasks.
 */
async function connectCall() {
  await dismissStartupSelectionPromptIfPresent()
  const micButton = screen.getByRole('button', { name: /start call/i })
  await act(async () => {
    micButton.click()
  })
  // Fire MockWebSocket's onopen (queued via setTimeout(0))
  await act(async () => {
    vi.advanceTimersByTime(1)
  })
  // Flush remaining microtasks from audio.initPlayer/startRecording
  await act(async () => {
    await Promise.resolve()
  })
}

async function dismissStartupSelectionPromptIfPresent() {
  const continueButton = screen.queryByRole('button', { name: /continue with last setup/i })
  if (!continueButton) return
  await act(async () => {
    continueButton.click()
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
    window.history.replaceState({}, '', '/')
    vi.restoreAllMocks()
  })

  it('renders IndustryOnboarding when no industry in localStorage', () => {
    render(<App />)
    expect(screen.getByText('Choose Your Service Industry')).toBeInTheDocument()
  })

  it('renders main layout after industry is stored', () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    expect(screen.getByText(/Continue with your last workspace\?/i)).toBeInTheDocument()
    expect(screen.queryByText('Electronics Trade Desk')).not.toBeInTheDocument()
  })

  it('can re-select industry from startup prompt and clears persisted onboarding selection', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    window.localStorage.setItem('ekaette:onboarding:templateId', 'electronics')
    window.localStorage.setItem('ekaette:onboarding:companyId', 'ekaette-electronics')
    render(<App />)

    const resetButton = screen.getByRole('button', { name: /re-select industry/i })
    await act(async () => {
      resetButton.click()
    })

    expect(screen.getByText('Choose Your Service Industry')).toBeInTheDocument()
    expect(window.localStorage.getItem('ekaette:onboarding:industry')).toBeNull()
    expect(window.localStorage.getItem('ekaette:onboarding:templateId')).toBeNull()
    expect(window.localStorage.getItem('ekaette:onboarding:companyId')).toBeNull()
  })

  it('includes company_id in WebSocket URL when connecting', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)

    await dismissStartupSelectionPromptIfPresent()

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

    expect(screen.getByText('iPhone 14 Pro')).toBeInTheDocument()
    expect(screen.getByText('Good')).toBeInTheDocument()
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

    expect(screen.getByText('#BK-12345')).toBeInTheDocument()
    expect(screen.getByText('Ikeja, Lagos')).toBeInTheDocument()
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

    expect(screen.getByText('Unable to process image')).toBeInTheDocument()
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

    expect(screen.getByText('Temporary error')).toBeInTheDocument()

    // Advance past the auto-dismiss timeout (8 seconds)
    await act(async () => {
      vi.advanceTimersByTime(9000)
    })

    expect(screen.queryByText('Temporary error')).not.toBeInTheDocument()
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

    expect(screen.getByText('iPhone 15')).toBeInTheDocument()
    expect(screen.getByText('Samsung Galaxy S24')).toBeInTheDocument()
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

    expect(screen.queryByText(/Transferring to Valuation Agent/i)).not.toBeInTheDocument()
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

    expect(screen.queryByText(/Transferring to Booking Agent/i)).not.toBeInTheDocument()
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

    expect(screen.getByText(/Context restored for Ada: 3 prior interactions/i)).toBeInTheDocument()
  })

  it('clears transcript and resets state when ending a connected call', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'user',
        text: 'Testing call teardown',
        partial: false,
      })
    })
    expect(screen.getAllByText('Testing call teardown').length).toBeGreaterThan(0)

    const endButton = screen.getByRole('button', { name: /end call/i })
    await act(async () => {
      endButton.click()
    })
    await act(async () => {
      vi.advanceTimersByTime(10)
    })

    expect(screen.getByRole('button', { name: /start call/i })).toBeInTheDocument()
    expect(screen.queryByText('Testing call teardown')).not.toBeInTheDocument()
    expect(screen.getByText('No live transcript yet.')).toBeInTheDocument()
  })

  it('handles session_ending by clearing transcript state for go_away', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'agent',
        text: 'This will be cleared',
        partial: false,
      })
    })
    expect(screen.getAllByText('This will be cleared').length).toBeGreaterThan(0)

    await act(async () => {
      sendServerMessage(ws, {
        type: 'session_ending',
        reason: 'go_away',
      })
    })

    expect(screen.queryByText('This will be cleared')).not.toBeInTheDocument()
    expect(screen.getByText('No live transcript yet.')).toBeInTheDocument()
  })

  it('accepts messages that arrive before websocket open without crashing', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await dismissStartupSelectionPromptIfPresent()

    const micButton = screen.getByRole('button', { name: /start call/i })
    await act(async () => {
      micButton.click()
    })

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'transcription',
        role: 'agent',
        text: 'Early message',
        partial: false,
      })
    })

    await act(async () => {
      vi.advanceTimersByTime(1)
      vi.advanceTimersByTime(100)
      await Promise.resolve()
    })

    expect(screen.getAllByText('Early message').length).toBeGreaterThan(0)
  })

  it('shows websocket timeout callError and recovers after connection failure', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    const OriginalWebSocket = globalThis.WebSocket

    class NeverOpenWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      static instances: NeverOpenWebSocket[] = []
      url: string
      readyState = NeverOpenWebSocket.CONNECTING
      binaryType = 'blob'
      sent: Array<string | ArrayBuffer> = []
      onopen: ((ev: Event) => void) | null = null
      onclose: ((ev: CloseEvent) => void) | null = null
      onerror: ((ev: Event) => void) | null = null
      onmessage: ((ev: MessageEvent) => void) | null = null
      constructor(url: string) {
        this.url = url
        NeverOpenWebSocket.instances.push(this)
        ;(globalThis as { __lastMockWebSocket?: MockSocket }).__lastMockWebSocket =
          this as unknown as MockSocket
      }
      send(data: string | ArrayBuffer) {
        this.sent.push(data)
      }
      close() {
        this.readyState = NeverOpenWebSocket.CLOSED
        this.onclose?.(new CloseEvent('close'))
      }
    }

    globalThis.WebSocket = NeverOpenWebSocket as unknown as typeof WebSocket
    try {
      render(<App />)
      await dismissStartupSelectionPromptIfPresent()
      const micButton = screen.getByRole('button', { name: /start call/i })
      await act(async () => {
        micButton.click()
      })

      const ws = getLastSocket()
      await act(async () => {
        ws.onerror?.(new Event('error'))
        vi.advanceTimersByTime(15100)
        await Promise.resolve()
      })

      expect(screen.getByText(/Connection: WebSocket connection timeout/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /start call/i })).toBeInTheDocument()
    } finally {
      globalThis.WebSocket = OriginalWebSocket
    }
  })

  it('restarts the error auto-dismiss timer for rapid consecutive errors', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'error',
        code: 'FIRST',
        message: 'First error',
      })
    })
    expect(screen.getByText('First error')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(4000)
      sendServerMessage(ws, {
        type: 'error',
        code: 'SECOND',
        message: 'Second error',
      })
    })
    expect(screen.queryByText('First error')).not.toBeInTheDocument()
    expect(screen.getByText('Second error')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(screen.getByText('Second error')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(3100)
    })
    expect(screen.queryByText('Second error')).not.toBeInTheDocument()
  })

  it('supports demo mode via query param without creating a websocket', async () => {
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    window.history.replaceState({}, '', '/?demo=1')
    render(<App />)
    await dismissStartupSelectionPromptIfPresent()

    const startButton = screen.getByRole('button', { name: /start call/i })
    await act(async () => {
      startButton.click()
      vi.advanceTimersByTime(0)
    })

    expect(screen.getByRole('button', { name: /end call/i })).toBeInTheDocument()
    expect(() => getLastSocket()).toThrow()

    await act(async () => {
      vi.advanceTimersByTime(900)
    })
    expect(
      screen.getAllByText(/Hello, I am Ekaette\. What device would you like to trade in today\?/i)
        .length,
    ).toBeGreaterThan(0)
  })
})
