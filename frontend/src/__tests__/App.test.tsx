import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { getLastSocket, type MockSocket, sendServerMessage } from './test-helpers'

// Pre-warm dynamic import cache so React.lazy resolves immediately under fake timers
beforeAll(async () => {
  await import('../components/cards/ValuationCard')
  await import('../components/cards/BookingConfirmationCard')
  await import('../components/cards/ProductCard')
  // Wizard step components (lazy-loaded in VendorSetupWizard)
  await import('../components/layout/wizard/StepIndustry')
  await import('../components/layout/wizard/StepKnowledge')
  await import('../components/layout/wizard/StepConnectors')
  await import('../components/layout/wizard/StepCatalog')
  await import('../components/layout/wizard/StepLaunch')
})

/**
 * Establish WebSocket connection through the App UI.
 * The socket mock opens on setTimeout(0), then we flush audio startup microtasks.
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

  it('renders runtime bootstrap loading state when no industry in localStorage', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      return await new Promise<Response>(() => {})
    })
    render(<App />)
    expect(screen.getByText('Preparing your workspace')).toBeInTheDocument()
  })

  it('applies runtime bootstrap response and skips onboarding', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          apiVersion: 'v1',
          tenantId: 'public',
          companyId: 'ekaette-telecom',
          industryTemplateId: 'telecom',
          industry: 'telecom',
          voice: 'Charon',
          capabilities: ['policy_qa'],
          onboardingRequired: false,
          sessionPolicy: {
            industryLocked: true,
            companyLocked: true,
            switchRequiresDisconnect: true,
          },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )

    render(<App />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/runtime/bootstrap?tenantId=public',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
    expect(screen.queryByText('Configure Your Business')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start call/i })).toBeInTheDocument()
  })

  it('falls back to compatibility vendor setup when runtime bootstrap fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'))

    render(<App />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    // Wizard renders "Vendor Setup" label and step indicator; "Configure Your Business" was the old text
    expect(screen.getByText('Vendor Setup')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Using local configuration because the backend setup service is unavailable.',
      ),
    ).toBeInTheDocument()
  })

  it('renders main layout after industry is stored (skips to Live Desk)', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      return await new Promise<Response>(() => {})
    })
    window.localStorage.setItem(INDUSTRY_STORAGE_KEY, 'electronics')
    render(<App />)
    // With startup prompt removed, stored industry goes straight to Live Desk
    expect(screen.getByRole('button', { name: /start call/i })).toBeInTheDocument()
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
