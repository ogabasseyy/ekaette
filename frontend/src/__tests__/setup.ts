import '@testing-library/jest-dom/vitest'

// ═══ Mock WebSocket ═══

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: MockWebSocket[] = []

  url: string
  readyState = MockWebSocket.CONNECTING
  binaryType = 'blob'
  sent: Array<string | ArrayBuffer> = []
  onopen: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    ;(
      globalThis as {
        __lastMockWebSocket?: MockWebSocket
      }
    ).__lastMockWebSocket = this
    // Simulate async connection
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN
      this.onopen?.(new Event('open'))
    }, 0)
  }

  send(data: string | ArrayBuffer) {
    this.sent.push(data)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  }
}

Object.assign(MockWebSocket, {
  CONNECTING: 0,
  OPEN: 1,
  CLOSING: 2,
  CLOSED: 3,
  instances: [],
})

globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket

// ═══ Mock AudioContext ═══

class MockAudioWorkletNode {
  port = {
    onmessage: null as ((ev: MessageEvent) => void) | null,
    postMessage: (_data: unknown) => {},
  }
  parameters = new Map<string, { value: number }>()
  processorOptions: Record<string, unknown> | undefined
  constructor(
    _context: AudioContext,
    _name: string,
    options?: { processorOptions?: Record<string, unknown> },
  ) {
    this.processorOptions = options?.processorOptions
  }
  connect() {}
  disconnect() {}
}

class MockAudioContext {
  sampleRate: number
  state = 'running'

  constructor(options?: { sampleRate?: number }) {
    this.sampleRate = options?.sampleRate ?? 44100
  }

  async resume() {
    this.state = 'running'
  }

  createMediaStreamSource() {
    return { connect: () => {} }
  }

  get audioWorklet() {
    return {
      addModule: async () => {},
    }
  }

  async close() {
    this.state = 'closed'
  }
}

globalThis.AudioContext = MockAudioContext as unknown as typeof AudioContext
globalThis.AudioWorkletNode = MockAudioWorkletNode as unknown as typeof AudioWorkletNode

// ═══ Mock navigator.mediaDevices ═══

Object.defineProperty(globalThis.navigator, 'mediaDevices', {
  value: {
    getUserMedia: async () => {
      return {
        getTracks: () => [{ stop: () => {} }],
      } as unknown as MediaStream
    },
  },
  configurable: true,
})

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}
