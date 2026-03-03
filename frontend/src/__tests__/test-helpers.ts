/**
 * Shared test helpers for frontend tests.
 * Phase 0 refactor: centralizes common patterns.
 */
import type { Industry, ServerMessage, SessionStartedMessage } from '../types'

/** Mock WebSocket interface matching the global mock in setup.ts. */
export interface MockSocket {
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

/** Get the most recently created MockWebSocket instance. */
export function getLastSocket(): MockSocket {
  const ws = (globalThis as { __lastMockWebSocket?: MockSocket }).__lastMockWebSocket
  if (!ws) throw new Error('Expected mock websocket instance')
  return ws
}

/** Simulate a server-sent JSON message on a mock socket. */
export function sendServerMessage(ws: MockSocket, message: ServerMessage): void {
  ws.onmessage?.(
    new MessageEvent('message', {
      data: JSON.stringify(message),
    }),
  )
}

/** Pre-store an industry in localStorage so onboarding is skipped. */
export function setStoredIndustry(industry: Industry): void {
  window.localStorage.setItem('ekaette:onboarding:industry', industry)
}

/** Build a session_started ServerMessage for tests. */
export function makeSessionStarted(
  industry: Industry = 'electronics',
  overrides: Partial<Omit<SessionStartedMessage, 'type'>> = {},
): ServerMessage {
  return {
    type: 'session_started',
    sessionId: `test-session-${Date.now()}`,
    industry,
    ...overrides,
  } as ServerMessage
}
