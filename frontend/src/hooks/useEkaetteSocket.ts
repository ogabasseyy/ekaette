import {
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import type { ClientMessage, ConnectionState, ServerMessage, TransportMode } from '../types'

const MAX_RECONNECT_ATTEMPTS = 3
const BASE_RECONNECT_DELAY = 1000
const MAX_MESSAGES = 500
const DEFAULT_CONNECT_TIMEOUT_MS = 15000
const HEARTBEAT_INTERVAL_MS = 5000
const HEARTBEAT_TIMEOUT_MS = 15000
const AUDIO_TX_BACKPRESSURE_BYTES = 256 * 1024
const AUDIO_RX_GAP_SUSPECT_MS = 220
const DEBUG_METRICS_FLUSH_MS = 200
const IS_TEST_ENV = import.meta.env.MODE === 'test'

export type SocketConnectErrorCode = 'CONNECT_TIMEOUT' | 'CONNECT_FAILED' | 'CONNECT_CANCELLED'

export class SocketConnectError extends Error {
  readonly code: SocketConnectErrorCode
  readonly retryable: boolean

  constructor(
    code: SocketConnectErrorCode,
    message: string,
    options: { retryable?: boolean } = {},
  ) {
    super(message)
    this.name = 'SocketConnectError'
    this.code = code
    this.retryable = options.retryable ?? code !== 'CONNECT_CANCELLED'
  }
}

export interface SocketDebugMetrics {
  transport: TransportMode
  heartbeatRttMs: number | null
  heartbeatJitterMs: number | null
  heartbeatTimeouts: number
  heartbeatPending: number
  audioTxChunks: number
  audioTxBytes: number
  audioTxDropCount: number
  audioTxBackpressureCount: number
  audioTxBufferedAmountBytes: number
  audioTxBufferedAmountMax: number
  audioRxChunks: number
  audioRxBytes: number
  audioRxDropSuspectCount: number
  audioRxLastGapMs: number
}

interface UseEkaetteSocketReturn {
  state: ConnectionState
  messages: ServerMessage[]
  debugMetrics: SocketDebugMetrics
  connect: (options?: ConnectOptions) => Promise<void>
  disconnect: () => void
  sendAudio: (data: ArrayBuffer) => void
  sendText: (text: string) => void
  sendImage: (base64: string, mimeType: string) => void
  sendNegotiate: (counterOffer: number, action: 'accept' | 'decline' | 'counter') => void
  sendActivityStart: () => void
  sendActivityEnd: () => void
  onAudioData: MutableRefObject<((data: ArrayBuffer) => void) | null>
  onSessionEnding: MutableRefObject<((reason: string) => void) | null>
  clearMessages: () => void
  injectDemoMessage: (message: ServerMessage) => void
}

interface UseEkaetteSocketOptions {
  demoMode?: boolean
  industry?: string
  companyId?: string
  tenantId?: string
  transportMode?: TransportMode
  connectTimeoutMs?: number
}

interface ConnectOptions {
  timeoutMs?: number
}

interface PendingConnectRequest {
  promise: Promise<void>
  resolve: () => void
  reject: (error: SocketConnectError) => void
  timeoutTimer: ReturnType<typeof setTimeout> | null
}

interface EphemeralTokenResponse {
  token: string
  wsToken?: string
  model?: string
  industry?: string
  companyId?: string
  voice?: string
  manualVadActive?: boolean
  vadMode?: 'auto' | 'manual'
}

interface LiveInlineData {
  data?: string
  mimeType?: string
}

interface LivePart {
  inlineData?: LiveInlineData
}

interface LiveServerContent {
  modelTurn?: { parts?: LivePart[] }
  inputTranscription?: { text?: string }
  outputTranscription?: { text?: string }
  interrupted?: boolean
  turnComplete?: boolean
}

interface LiveServerMessage {
  data?: string
  serverContent?: LiveServerContent
}

interface DirectLiveSession {
  close: () => Promise<void> | void
  sendClientContent: (payload: unknown) => Promise<void> | void
  sendRealtimeInput: (payload: unknown) => Promise<void> | void
}

const INITIAL_DEBUG_METRICS: SocketDebugMetrics = {
  transport: 'backend-proxy',
  heartbeatRttMs: null,
  heartbeatJitterMs: null,
  heartbeatTimeouts: 0,
  heartbeatPending: 0,
  audioTxChunks: 0,
  audioTxBytes: 0,
  audioTxDropCount: 0,
  audioTxBackpressureCount: 0,
  audioTxBufferedAmountBytes: 0,
  audioTxBufferedAmountMax: 0,
  audioRxChunks: 0,
  audioRxBytes: 0,
  audioRxDropSuspectCount: 0,
  audioRxLastGapMs: 0,
}

function arrayBufferToBase64(data: ArrayBuffer): string {
  const bytes = new Uint8Array(data)
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, Math.min(i + chunkSize, bytes.length))
    let chunkBinary = ''
    for (let j = 0; j < chunk.length; j += 1) {
      chunkBinary += String.fromCharCode(chunk[j] ?? 0)
    }
    binary += chunkBinary
  }
  return btoa(binary)
}

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

function appendMessage(
  setMessages: Dispatch<SetStateAction<ServerMessage[]>>,
  message: ServerMessage,
): void {
  setMessages(prev => {
    const next = [...prev, message]
    // Cap message array to prevent unbounded growth and UI lag.
    return next.length > MAX_MESSAGES ? next.slice(-MAX_MESSAGES) : next
  })
}

export function useEkaetteSocket(
  userId: string,
  sessionId: string,
  options: UseEkaetteSocketOptions = {},
): UseEkaetteSocketReturn {
  const demoMode = options.demoMode ?? false
  const industry = options.industry ?? 'electronics'
  const companyId = options.companyId ?? ''
  const tenantId = options.tenantId ?? 'public'
  const transportMode = options.transportMode ?? 'backend-proxy'
  const defaultConnectTimeoutMs = options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS

  const wsRef = useRef<WebSocket | null>(null)
  const directSessionRef = useRef<DirectLiveSession | null>(null)
  const activeTransportRef = useRef<TransportMode>('backend-proxy')
  const [state, setState] = useState<ConnectionState>('disconnected')
  const [messages, setMessages] = useState<ServerMessage[]>([])
  const [debugMetrics, setDebugMetrics] = useState<SocketDebugMetrics>(INITIAL_DEBUG_METRICS)
  const debugMetricsRef = useRef<SocketDebugMetrics>(INITIAL_DEBUG_METRICS)
  const metricsFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const heartbeatPendingRef = useRef<Map<number, number>>(new Map())
  const heartbeatSeqRef = useRef(0)
  const lastHeartbeatRttRef = useRef<number | null>(null)
  const lastRxAudioAtRef = useRef(0)
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const voiceReconnectPendingRef = useRef(false)
  const onAudioData = useRef<((data: ArrayBuffer) => void) | null>(null)
  const onSessionEnding = useRef<((reason: string) => void) | null>(null)
  const currentSessionIdRef = useRef(sessionId)
  const manualVadEnabledRef = useRef(false)
  const shouldReconnectRef = useRef(true)
  const connectingRef = useRef(false)
  const connectInternalRef = useRef<() => void>(() => {})
  const lastDirectInputTextRef = useRef('')
  const lastDirectOutputTextRef = useRef('')
  const receivingDirectInputRef = useRef(false)
  const lastConnectTimeRef = useRef(0)
  const rapidFailCountRef = useRef(0)
  const pendingConnectRef = useRef<PendingConnectRequest | null>(null)
  const wsTokenRef = useRef<string | null>(null)

  const flushDebugMetrics = useCallback((force = false) => {
    if (IS_TEST_ENV) {
      if (metricsFlushTimerRef.current) {
        clearTimeout(metricsFlushTimerRef.current)
        metricsFlushTimerRef.current = null
      }
      setDebugMetrics({ ...debugMetricsRef.current })
      return
    }
    if (force) {
      if (metricsFlushTimerRef.current) {
        clearTimeout(metricsFlushTimerRef.current)
        metricsFlushTimerRef.current = null
      }
      setDebugMetrics({ ...debugMetricsRef.current })
      return
    }
    if (metricsFlushTimerRef.current) {
      return
    }
    metricsFlushTimerRef.current = setTimeout(() => {
      metricsFlushTimerRef.current = null
      setDebugMetrics({ ...debugMetricsRef.current })
    }, DEBUG_METRICS_FLUSH_MS)
  }, [])

  const mutateDebugMetrics = useCallback(
    (mutator: (draft: SocketDebugMetrics) => void, force = false) => {
      const draft = { ...debugMetricsRef.current }
      mutator(draft)
      debugMetricsRef.current = draft
      flushDebugMetrics(force)
    },
    [flushDebugMetrics],
  )

  const resolvePendingConnect = useCallback(() => {
    const pending = pendingConnectRef.current
    if (!pending) {
      return
    }
    pendingConnectRef.current = null
    if (pending.timeoutTimer) {
      clearTimeout(pending.timeoutTimer)
      pending.timeoutTimer = null
    }
    pending.resolve()
  }, [])

  const rejectPendingConnect = useCallback((error: SocketConnectError) => {
    const pending = pendingConnectRef.current
    if (!pending) {
      return
    }
    pendingConnectRef.current = null
    if (pending.timeoutTimer) {
      clearTimeout(pending.timeoutTimer)
      pending.timeoutTimer = null
    }
    pending.reject(error)
  }, [])

  const cleanup = useCallback(() => {
    const pending = pendingConnectRef.current
    if (pending?.timeoutTimer) {
      clearTimeout(pending.timeoutTimer)
      pending.timeoutTimer = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current)
      heartbeatTimerRef.current = null
    }
    heartbeatPendingRef.current.clear()
    if (metricsFlushTimerRef.current) {
      clearTimeout(metricsFlushTimerRef.current)
      metricsFlushTimerRef.current = null
    }
    voiceReconnectPendingRef.current = false

    if (wsRef.current) {
      wsRef.current.onopen = null
      wsRef.current.onclose = null
      wsRef.current.onerror = null
      wsRef.current.onmessage = null
      wsRef.current.close()
      wsRef.current = null
    }

    if (directSessionRef.current) {
      void Promise.resolve(directSessionRef.current.close()).catch(() => {
        // Ignore close race.
      })
      directSessionRef.current = null
    }

    activeTransportRef.current = 'backend-proxy'
    connectingRef.current = false
    lastDirectInputTextRef.current = ''
    lastDirectOutputTextRef.current = ''
    receivingDirectInputRef.current = false
    lastRxAudioAtRef.current = 0
    heartbeatSeqRef.current = 0
    lastHeartbeatRttRef.current = null
    manualVadEnabledRef.current = false
    debugMetricsRef.current = {
      ...INITIAL_DEBUG_METRICS,
      transport: 'backend-proxy',
    }
    setDebugMetrics(debugMetricsRef.current)
  }, [])

  const createPendingConnect = useCallback(
    (timeoutMs: number) => {
      if (pendingConnectRef.current) {
        return pendingConnectRef.current.promise
      }

      let resolvePromise: (() => void) | null = null
      let rejectPromise: ((error: SocketConnectError) => void) | null = null

      const promise = new Promise<void>((resolve, reject) => {
        resolvePromise = resolve
        rejectPromise = error => reject(error)
      })

      const pending: PendingConnectRequest = {
        promise,
        resolve: () => {
          resolvePromise?.()
        },
        reject: (error: SocketConnectError) => {
          rejectPromise?.(error)
        },
        timeoutTimer: null,
      }
      pendingConnectRef.current = pending

      if (timeoutMs > 0 && Number.isFinite(timeoutMs)) {
        pending.timeoutTimer = setTimeout(() => {
          if (pendingConnectRef.current !== pending) {
            return
          }
          shouldReconnectRef.current = false
          setState('disconnected')
          cleanup()
          rejectPendingConnect(new SocketConnectError('CONNECT_TIMEOUT', 'WebSocket connection timeout'))
        }, timeoutMs)
      }

      return promise
    },
    [cleanup, rejectPendingConnect],
  )

  const startProxyHeartbeat = useCallback(() => {
    if (IS_TEST_ENV) {
      heartbeatPendingRef.current.clear()
      mutateDebugMetrics(draft => {
        draft.heartbeatPending = 0
      }, true)
      return
    }
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current)
      heartbeatTimerRef.current = null
    }
    heartbeatPendingRef.current.clear()
    heartbeatTimerRef.current = setInterval(() => {
      const ws = wsRef.current
      if (
        !ws ||
        ws.readyState !== WebSocket.OPEN ||
        activeTransportRef.current !== 'backend-proxy'
      ) {
        return
      }
      const now = Date.now()

      let timedOut = 0
      for (const [seq, sentAt] of heartbeatPendingRef.current) {
        if (now - sentAt > HEARTBEAT_TIMEOUT_MS) {
          heartbeatPendingRef.current.delete(seq)
          timedOut += 1
        }
      }

      const seq = ++heartbeatSeqRef.current
      heartbeatPendingRef.current.set(seq, now)

      mutateDebugMetrics(draft => {
        if (timedOut > 0) {
          draft.heartbeatTimeouts += timedOut
        }
        draft.heartbeatPending = heartbeatPendingRef.current.size
      })

      try {
        ws.send(JSON.stringify({ type: 'client_ping', seq, clientTs: now }))
      } catch {
        // Ignore send races; onclose will handle reconnection.
      }
    }, HEARTBEAT_INTERVAL_MS)
  }, [mutateDebugMetrics])

  const handleJsonMessage = useCallback(
    (data: string) => {
      try {
        const parsed = JSON.parse(data)
        if (
          parsed &&
          typeof parsed === 'object' &&
          'type' in parsed &&
          typeof (parsed as { type?: unknown }).type === 'string'
        ) {
          const raw = parsed as Record<string, unknown>
          const rawType = raw.type

          if (rawType === 'ping') {
            // Server keepalive ping (one-way). Heartbeat RTT is measured via client_ping/client_pong.
            return
          }

          if (rawType === 'client_pong') {
            const seq = typeof raw.seq === 'number' ? raw.seq : Number(raw.seq)
            const echoedTs = typeof raw.clientTs === 'number' ? raw.clientTs : Number(raw.clientTs)
            const now = Date.now()
            const sentAt =
              (Number.isFinite(seq) ? heartbeatPendingRef.current.get(seq) : undefined) ??
              (Number.isFinite(echoedTs) ? echoedTs : now)
            if (Number.isFinite(seq)) {
              heartbeatPendingRef.current.delete(seq)
            }
            const rtt = Math.max(0, now - sentAt)
            const prevRtt = lastHeartbeatRttRef.current
            const jitter = prevRtt == null ? 0 : Math.abs(rtt - prevRtt)
            lastHeartbeatRttRef.current = rtt

            mutateDebugMetrics(draft => {
              draft.heartbeatRttMs = rtt
              draft.heartbeatJitterMs =
                draft.heartbeatJitterMs == null
                  ? jitter
                  : Math.round(draft.heartbeatJitterMs * 0.7 + jitter * 0.3)
              draft.heartbeatPending = heartbeatPendingRef.current.size
            })
            return
          }

          const serverMessage = parsed as ServerMessage

          // Session ending — notify App so it can handle graceful reconnect.
          if (serverMessage.type === 'session_ending') {
            const reason = (serverMessage as { reason?: string }).reason ?? 'unknown'
            onSessionEnding.current?.(reason)
            appendMessage(setMessages, serverMessage)
            return
          }

          if (
            serverMessage.type === 'session_started' &&
            typeof serverMessage.sessionId === 'string' &&
            serverMessage.sessionId
          ) {
            currentSessionIdRef.current = serverMessage.sessionId
            manualVadEnabledRef.current = Boolean(serverMessage.manualVadActive)
            if (
              serverMessage.voiceChangeRequiresReconnect &&
              !demoMode &&
              activeTransportRef.current === 'backend-proxy' &&
              wsRef.current?.readyState === WebSocket.OPEN &&
              !voiceReconnectPendingRef.current
            ) {
              voiceReconnectPendingRef.current = true
              // Native voice changes apply on new live session.
              wsRef.current.close()
            }
          }
          appendMessage(setMessages, serverMessage)
        }
      } catch {
        // Ignore malformed/non-contract messages.
      }
    },
    [demoMode, mutateDebugMetrics],
  )

  const trackRxAudioChunk = useCallback(
    (byteLength: number) => {
      const now = Date.now()
      const lastAt = lastRxAudioAtRef.current
      const gapMs = lastAt > 0 ? now - lastAt : 0
      lastRxAudioAtRef.current = now

      mutateDebugMetrics(draft => {
        draft.audioRxChunks += 1
        draft.audioRxBytes += byteLength
        if (
          activeTransportRef.current === 'backend-proxy' &&
          gapMs > AUDIO_RX_GAP_SUSPECT_MS &&
          gapMs < 2000
        ) {
          draft.audioRxDropSuspectCount += 1
          draft.audioRxLastGapMs = gapMs
        } else if (gapMs > 0) {
          draft.audioRxLastGapMs = gapMs
        }
      })
    },
    [mutateDebugMetrics],
  )

  const handleBinaryMessage = useCallback(
    (data: ArrayBuffer | Blob) => {
      if (data instanceof ArrayBuffer) {
        trackRxAudioChunk(data.byteLength)
        onAudioData.current?.(data)
        return
      }
      const blobSize = typeof data.size === 'number' ? data.size : 0
      void data
        .arrayBuffer()
        .then(buffer => {
          trackRxAudioChunk(blobSize || buffer.byteLength)
          onAudioData.current?.(buffer)
        })
        .catch(() => {
          // Ignore Blob decode failures.
        })
    },
    [trackRxAudioChunk],
  )

  const scheduleReconnect = useCallback(() => {
    if (demoMode || !shouldReconnectRef.current) {
      return
    }
    if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
      setState('disconnected')
      rejectPendingConnect(
        new SocketConnectError(
          'CONNECT_FAILED',
          'Unable to establish WebSocket connection',
        ),
      )
      return
    }
    setState('reconnecting')
    const delay = BASE_RECONNECT_DELAY * 2 ** reconnectAttemptRef.current
    reconnectAttemptRef.current += 1
    reconnectTimerRef.current = setTimeout(() => {
      connectInternalRef.current()
    }, delay)
  }, [demoMode, rejectPendingConnect])

  const requestEphemeralToken = useCallback(async (): Promise<EphemeralTokenResponse> => {
    const response = await fetch('/api/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        userId,
        tenantId,
        industry,
        companyId,
      }),
    })

    if (!response.ok) {
      throw new Error(`Token request failed (${response.status})`)
    }

    const payload = (await response.json()) as Partial<EphemeralTokenResponse>
    if (!payload.token || typeof payload.token !== 'string') {
      throw new Error('Token response missing token')
    }

    const wsToken = typeof payload.wsToken === 'string' ? payload.wsToken : undefined
    wsTokenRef.current = wsToken ?? null

    return {
      token: payload.token,
      wsToken,
      model: payload.model,
      industry: payload.industry,
      companyId: payload.companyId,
      voice: typeof payload.voice === 'string' ? payload.voice : undefined,
      manualVadActive:
        typeof payload.manualVadActive === 'boolean' ? payload.manualVadActive : undefined,
      vadMode:
        payload.vadMode === 'auto' || payload.vadMode === 'manual' ? payload.vadMode : undefined,
    }
  }, [companyId, industry, tenantId, userId])

  const emitDirectFallbackMessage = useCallback((reason: string) => {
    appendMessage(setMessages, {
      type: 'error',
      code: 'DIRECT_MODE_FALLBACK',
      message: `${reason}. Using backend proxy transport.`,
    })
  }, [])

  const handleDirectLiveMessage = useCallback(
    (message: LiveServerMessage) => {
      if (typeof message.data === 'string' && message.data.length > 0) {
        const buffer = base64ToArrayBuffer(message.data)
        trackRxAudioChunk(buffer.byteLength)
        onAudioData.current?.(buffer)
      }

      const content = message.serverContent
      const parts = content?.modelTurn?.parts ?? []
      for (const part of parts) {
        const inline = part.inlineData
        if (
          inline &&
          typeof inline.data === 'string' &&
          typeof inline.mimeType === 'string' &&
          inline.mimeType.startsWith('audio/')
        ) {
          const buffer = base64ToArrayBuffer(inline.data)
          trackRxAudioChunk(buffer.byteLength)
          onAudioData.current?.(buffer)
        }
      }

      const inputText = content?.inputTranscription?.text
      if (typeof inputText === 'string' && inputText.trim()) {
        lastDirectInputTextRef.current = inputText
        receivingDirectInputRef.current = true
        appendMessage(setMessages, {
          type: 'transcription',
          role: 'user',
          text: inputText,
          partial: true,
        })
      }

      const outputText = content?.outputTranscription?.text
      if (typeof outputText === 'string' && outputText.trim()) {
        if (receivingDirectInputRef.current && lastDirectInputTextRef.current) {
          appendMessage(setMessages, {
            type: 'transcription',
            role: 'user',
            text: lastDirectInputTextRef.current,
            partial: false,
          })
          lastDirectInputTextRef.current = ''
          receivingDirectInputRef.current = false
        }
        lastDirectOutputTextRef.current = outputText
        appendMessage(setMessages, {
          type: 'transcription',
          role: 'agent',
          text: outputText,
          partial: true,
        })
      }

      if (content?.interrupted) {
        if (receivingDirectInputRef.current && lastDirectInputTextRef.current) {
          appendMessage(setMessages, {
            type: 'transcription',
            role: 'user',
            text: lastDirectInputTextRef.current,
            partial: false,
          })
          lastDirectInputTextRef.current = ''
          receivingDirectInputRef.current = false
        }
        if (lastDirectOutputTextRef.current) {
          appendMessage(setMessages, {
            type: 'transcription',
            role: 'agent',
            text: lastDirectOutputTextRef.current,
            partial: false,
          })
          lastDirectOutputTextRef.current = ''
        }
        appendMessage(setMessages, {
          type: 'interrupted',
          interrupted: true,
        })
      }

      if (content?.turnComplete) {
        if (lastDirectInputTextRef.current) {
          appendMessage(setMessages, {
            type: 'transcription',
            role: 'user',
            text: lastDirectInputTextRef.current,
            partial: false,
          })
          lastDirectInputTextRef.current = ''
          receivingDirectInputRef.current = false
        }
        if (lastDirectOutputTextRef.current) {
          appendMessage(setMessages, {
            type: 'transcription',
            role: 'agent',
            text: lastDirectOutputTextRef.current,
            partial: false,
          })
          lastDirectOutputTextRef.current = ''
        }
        appendMessage(setMessages, {
          type: 'agent_status',
          agent: 'gemini_live',
          status: 'idle',
        })
      }
    },
    [trackRxAudioChunk],
  )

  const clearMessages = useCallback(() => {
    setMessages([])
  }, [])

  const connectBackendProxy = useCallback(async () => {
    // Guard: prevent double-connect
    if (
      connectingRef.current ||
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return
    }

    if (directSessionRef.current) {
      void Promise.resolve(directSessionRef.current.close()).catch(() => {
        // Ignore close race.
      })
      directSessionRef.current = null
    }

    // Clean up any prior socket
    if (wsRef.current) {
      wsRef.current.onopen = null
      wsRef.current.onclose = null
      wsRef.current.onerror = null
      wsRef.current.onmessage = null
      wsRef.current.close()
    }

    shouldReconnectRef.current = true
    connectingRef.current = true
    setState('connecting')

    // Fetch ephemeral token to obtain optional wsToken for WS auth.
    try {
      await requestEphemeralToken()
    } catch {
      // Token fetch is best-effort for backend-proxy; proceed without wsToken.
    }
    if (!shouldReconnectRef.current) {
      connectingRef.current = false
      return
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const industryQuery = encodeURIComponent(industry)
    const companyQuery = companyId ? `&company_id=${encodeURIComponent(companyId)}` : ''
    const tenantQuery = tenantId ? `&tenant_id=${encodeURIComponent(tenantId)}` : ''
    const tokenQuery = wsTokenRef.current
      ? `&token=${encodeURIComponent(wsTokenRef.current)}`
      : ''
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/ws/${userId}/${currentSessionIdRef.current}?industry=${industryQuery}${companyQuery}${tenantQuery}${tokenQuery}`,
    )
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      connectingRef.current = false
      reconnectAttemptRef.current = 0
      lastConnectTimeRef.current = Date.now()
      activeTransportRef.current = 'backend-proxy'
      mutateDebugMetrics(draft => {
        draft.transport = 'backend-proxy'
        draft.heartbeatPending = 0
      }, true)
      startProxyHeartbeat()
      setState('connected')
      resolvePendingConnect()
    }

    ws.onclose = (event: CloseEvent) => {
      connectingRef.current = false
      wsRef.current = null
      if (heartbeatTimerRef.current) {
        clearInterval(heartbeatTimerRef.current)
        heartbeatTimerRef.current = null
      }
      heartbeatPendingRef.current.clear()
      mutateDebugMetrics(draft => {
        draft.heartbeatPending = 0
      })

      // Handle 4401: invalid/expired WS token — clear cached token so
      // reconnect will fetch a fresh one via requestEphemeralToken.
      if (event.code === 4401) {
        wsTokenRef.current = null
      }

      // Detect rapid failures: if connection lasted < 3s, increment counter.
      // After 3 rapid failures in a row, stop reconnecting to avoid loops.
      const connectionDuration = Date.now() - lastConnectTimeRef.current
      if (lastConnectTimeRef.current > 0 && connectionDuration < 3000) {
        rapidFailCountRef.current += 1
      } else {
        rapidFailCountRef.current = 0
      }

      if (rapidFailCountRef.current >= 3) {
        appendMessage(setMessages, {
          type: 'error',
          code: 'RAPID_DISCONNECT',
          message: 'Connection keeps dropping. Please try again later.',
        })
        setState('disconnected')
        rapidFailCountRef.current = 0
        rejectPendingConnect(
          new SocketConnectError(
            'CONNECT_FAILED',
            'Connection keeps dropping. Please try again later.',
          ),
        )
        return
      }

      if (voiceReconnectPendingRef.current && shouldReconnectRef.current) {
        voiceReconnectPendingRef.current = false
        setState('reconnecting')
        reconnectAttemptRef.current = 0
        reconnectTimerRef.current = setTimeout(() => {
          connectInternalRef.current()
        }, 150)
        return
      }
      if (shouldReconnectRef.current) {
        scheduleReconnect()
      } else {
        setState('disconnected')
      }
    }

    ws.onerror = () => {
      connectingRef.current = false
    }

    ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        handleBinaryMessage(event.data)
        return
      }
      if (event.data instanceof Blob) {
        handleBinaryMessage(event.data)
        return
      }
      if (typeof event.data === 'string') {
        handleJsonMessage(event.data)
      }
    }
  }, [
    companyId,
    handleBinaryMessage,
    handleJsonMessage,
    industry,
    mutateDebugMetrics,
    rejectPendingConnect,
    requestEphemeralToken,
    resolvePendingConnect,
    scheduleReconnect,
    startProxyHeartbeat,
    tenantId,
    userId,
  ])

  const connectDirectLive = useCallback(async () => {
    shouldReconnectRef.current = true
    connectingRef.current = true
    setState('connecting')

    const tokenPayload = await requestEphemeralToken()
    if (!shouldReconnectRef.current) {
      connectingRef.current = false
      return
    }

    const { GoogleGenAI, Modality } = await import('@google/genai')
    if (!shouldReconnectRef.current) {
      connectingRef.current = false
      return
    }
    const ai = new GoogleGenAI({ apiKey: tokenPayload.token })
    const selectedModel =
      tokenPayload.model ??
      String(import.meta.env.VITE_LIVE_MODEL_ID ?? 'gemini-2.5-flash-native-audio-preview-12-2025')
    const speechLanguageCode =
      String(import.meta.env.VITE_SPEECH_LANGUAGE_CODE ?? 'en-US').trim() || 'en-US'

    const session = await ai.live.connect({
      model: selectedModel,
      config: {
        responseModalities: [Modality.AUDIO],
        inputAudioTranscription: {},
        outputAudioTranscription: {},
        sessionResumption: {},
        contextWindowCompression: {
          triggerTokens: '80000',
          slidingWindow: { targetTokens: '40000' },
        },
        proactivity: { proactiveAudio: true },
        speechConfig: {
          languageCode: speechLanguageCode,
          ...(tokenPayload.voice
            ? {
                voiceConfig: {
                  prebuiltVoiceConfig: {
                    voiceName: tokenPayload.voice,
                  },
                },
              }
            : {}),
        },
      },
      callbacks: {
        onopen: () => {
          connectingRef.current = false
          reconnectAttemptRef.current = 0
          activeTransportRef.current = 'direct-live'
          mutateDebugMetrics(draft => {
            draft.transport = 'direct-live'
            draft.heartbeatPending = 0
          }, true)
          appendMessage(setMessages, {
            type: 'session_started',
            sessionId: currentSessionIdRef.current,
            industry: tokenPayload.industry ?? industry,
            companyId: tokenPayload.companyId ?? companyId,
            manualVadActive: tokenPayload.manualVadActive ?? false,
            vadMode: tokenPayload.vadMode ?? 'auto',
          })
          manualVadEnabledRef.current = Boolean(tokenPayload.manualVadActive)
          setState('connected')
          resolvePendingConnect()
        },
        onmessage: (message: unknown) => {
          handleDirectLiveMessage(message as LiveServerMessage)
        },
        onerror: (error: unknown) => {
          const msg = error instanceof Error ? error.message : 'Direct Live transport error'
          appendMessage(setMessages, {
            type: 'error',
            code: 'DIRECT_LIVE_ERROR',
            message: msg,
          })
        },
        onclose: () => {
          connectingRef.current = false
          directSessionRef.current = null
          if (shouldReconnectRef.current) {
            scheduleReconnect()
          } else {
            setState('disconnected')
          }
        },
      },
    })
    if (!shouldReconnectRef.current) {
      connectingRef.current = false
      void Promise.resolve(session.close()).catch(() => {
        // Ignore close race during teardown.
      })
      return
    }

    directSessionRef.current = session as unknown as DirectLiveSession
  }, [
    companyId,
    handleDirectLiveMessage,
    industry,
    mutateDebugMetrics,
    requestEphemeralToken,
    resolvePendingConnect,
    scheduleReconnect,
  ])

  const connectInternal = useCallback(() => {
    if (demoMode) {
      setState('connected')
      resolvePendingConnect()
      return
    }

    if (
      connectingRef.current ||
      directSessionRef.current ||
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return
    }

    if (transportMode === 'direct-live') {
      void connectDirectLive().catch(() => {
        connectingRef.current = false
        activeTransportRef.current = 'backend-proxy'
        emitDirectFallbackMessage('Direct Live connect failed')
        void connectBackendProxy()
      })
      return
    }

    void connectBackendProxy()
  }, [
    connectBackendProxy,
    connectDirectLive,
    demoMode,
    emitDirectFallbackMessage,
    resolvePendingConnect,
    transportMode,
  ])

  useEffect(() => {
    currentSessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    connectInternalRef.current = connectInternal
  }, [connectInternal])

  const connect = useCallback((connectOptions: ConnectOptions = {}) => {
    if (state === 'connected') {
      return Promise.resolve()
    }
    if (pendingConnectRef.current) {
      return pendingConnectRef.current.promise
    }

    reconnectAttemptRef.current = 0
    rapidFailCountRef.current = 0
    const timeoutMs = connectOptions.timeoutMs ?? defaultConnectTimeoutMs
    const pendingConnect = createPendingConnect(timeoutMs)
    connectInternal()
    return pendingConnect
  }, [connectInternal, createPendingConnect, defaultConnectTimeoutMs, state])

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false
    setState('disconnected')
    rejectPendingConnect(
      new SocketConnectError('CONNECT_CANCELLED', 'Connection cancelled', { retryable: false }),
    )
    cleanup()
  }, [cleanup, rejectPendingConnect])

  const sendJson = useCallback(
    (message: ClientMessage) => {
      if (demoMode) {
        return
      }

      if (activeTransportRef.current === 'direct-live' && directSessionRef.current) {
        if (message.type === 'text') {
          void Promise.resolve(
            directSessionRef.current.sendClientContent({
              turns: message.text,
              turnComplete: true,
            }),
          ).catch(() => {
            // Ignore send race.
          })
          return
        }

        if (message.type === 'image') {
          void Promise.resolve(
            directSessionRef.current.sendRealtimeInput({
              media: {
                data: message.data,
                mimeType: message.mimeType,
              },
            }),
          ).catch(() => {
            // Ignore send race.
          })
          return
        }

        if (message.type === 'negotiate') {
          void Promise.resolve(
            directSessionRef.current.sendClientContent({
              turns: `Customer negotiation: ${message.action}. Counter-offer amount: ${message.counterOffer}`,
              turnComplete: true,
            }),
          ).catch(() => {
            // Ignore send race.
          })
          return
        }

        if (message.type === 'activity_start') {
          void Promise.resolve(
            directSessionRef.current.sendRealtimeInput({ activityStart: {} }),
          ).catch(() => {
            // Ignore send race.
          })
          return
        }

        if (message.type === 'activity_end') {
          void Promise.resolve(
            directSessionRef.current.sendRealtimeInput({ activityEnd: {} }),
          ).catch(() => {
            // Ignore send race.
          })
        }
        return
      }

      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(message))
      }
    },
    [demoMode],
  )

  // Send binary audio
  const sendAudio = useCallback(
    (data: ArrayBuffer) => {
      if (demoMode) {
        return
      }
      const byteLength = data.byteLength

      if (activeTransportRef.current === 'direct-live' && directSessionRef.current) {
        mutateDebugMetrics(draft => {
          draft.audioTxChunks += 1
          draft.audioTxBytes += byteLength
        })
        const base64 = arrayBufferToBase64(data)
        void Promise.resolve(
          directSessionRef.current.sendRealtimeInput({
            audio: {
              data: base64,
              mimeType: 'audio/pcm;rate=16000',
            },
          }),
        ).catch(() => {
          // Ignore send race.
        })
        return
      }

      const ws = wsRef.current
      if (ws?.readyState === WebSocket.OPEN) {
        const bufferedAmount = ws.bufferedAmount ?? 0
        mutateDebugMetrics(draft => {
          draft.audioTxChunks += 1
          draft.audioTxBytes += byteLength
          draft.audioTxBufferedAmountBytes = bufferedAmount
          if (bufferedAmount > draft.audioTxBufferedAmountMax) {
            draft.audioTxBufferedAmountMax = bufferedAmount
          }
          if (bufferedAmount > AUDIO_TX_BACKPRESSURE_BYTES) {
            draft.audioTxBackpressureCount += 1
          }
        })
        ws.send(data)
        return
      }
      mutateDebugMetrics(draft => {
        draft.audioTxDropCount += 1
      })
    },
    [demoMode, mutateDebugMetrics],
  )

  // Send text message
  const sendText = useCallback(
    (text: string) => {
      sendJson({ type: 'text', text })
    },
    [sendJson],
  )

  // Send image with mimeType
  const sendImage = useCallback(
    (base64: string, mimeType: string) => {
      sendJson({ type: 'image', data: base64, mimeType })
    },
    [sendJson],
  )

  // Send negotiation action
  const sendNegotiate = useCallback(
    (counterOffer: number, action: 'accept' | 'decline' | 'counter') => {
      sendJson({ type: 'negotiate', counterOffer, action })
    },
    [sendJson],
  )

  const sendActivityStart = useCallback(() => {
    if (!manualVadEnabledRef.current) {
      return
    }
    sendJson({ type: 'activity_start' })
  }, [sendJson])

  const sendActivityEnd = useCallback(() => {
    if (!manualVadEnabledRef.current) {
      return
    }
    sendJson({ type: 'activity_end' })
  }, [sendJson])

  const injectDemoMessage = useCallback((message: ServerMessage) => {
    appendMessage(setMessages, message)
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cleanup()
    }
  }, [cleanup])

  return {
    state,
    messages,
    debugMetrics,
    connect,
    disconnect,
    sendAudio,
    sendText,
    sendImage,
    sendNegotiate,
    sendActivityStart,
    sendActivityEnd,
    onAudioData,
    onSessionEnding,
    clearMessages,
    injectDemoMessage,
  }
}
