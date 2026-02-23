import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
} from 'react'
import type {
  ClientMessage,
  ConnectionState,
  ServerMessage,
} from '../types'

const MAX_RECONNECT_ATTEMPTS = 3
const BASE_RECONNECT_DELAY = 1000

interface UseEkaetteSocketReturn {
  state: ConnectionState
  messages: ServerMessage[]
  connect: () => void
  disconnect: () => void
  sendAudio: (data: ArrayBuffer) => void
  sendText: (text: string) => void
  sendImage: (base64: string, mimeType: string) => void
  sendNegotiate: (counterOffer: number, action: 'accept' | 'decline' | 'counter') => void
  sendConfig: (industry: string) => void
  onAudioData: MutableRefObject<((data: ArrayBuffer) => void) | null>
  injectDemoMessage: (message: ServerMessage) => void
}

interface UseEkaetteSocketOptions {
  demoMode?: boolean
}

export function useEkaetteSocket(
  userId: string,
  sessionId: string,
  options: UseEkaetteSocketOptions = {},
): UseEkaetteSocketReturn {
  const demoMode = options.demoMode ?? false
  const wsRef = useRef<WebSocket | null>(null)
  const [state, setState] = useState<ConnectionState>('disconnected')
  const [messages, setMessages] = useState<ServerMessage[]>([])
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onAudioData = useRef<((data: ArrayBuffer) => void) | null>(null)
  const shouldReconnectRef = useRef(true)
  const connectingRef = useRef(false)
  const connectInternalRef = useRef<() => void>(() => {})

  const cleanup = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.onopen = null
      wsRef.current.onclose = null
      wsRef.current.onerror = null
      wsRef.current.onmessage = null
      wsRef.current.close()
      wsRef.current = null
    }
    connectingRef.current = false
  }, [])

  const handleJsonMessage = useCallback((data: string) => {
    try {
      const parsed = JSON.parse(data)
      if (
        parsed &&
        typeof parsed === 'object' &&
        'type' in parsed &&
        typeof (parsed as { type?: unknown }).type === 'string'
      ) {
        setMessages(prev => [...prev, parsed as ServerMessage])
      }
    } catch {
      // Ignore malformed/non-contract messages.
    }
  }, [])

  const handleBinaryMessage = useCallback((data: ArrayBuffer | Blob) => {
    if (data instanceof ArrayBuffer) {
      onAudioData.current?.(data)
      return
    }
    void data
      .arrayBuffer()
      .then(buffer => {
        onAudioData.current?.(buffer)
      })
      .catch(() => {
        // Ignore Blob decode failures.
      })
  }, [])

  const scheduleReconnect = useCallback(() => {
    if (demoMode || !shouldReconnectRef.current) {
      return
    }
    if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
      setState('disconnected')
      return
    }
    setState('reconnecting')
    const delay = BASE_RECONNECT_DELAY * Math.pow(2, reconnectAttemptRef.current)
    reconnectAttemptRef.current += 1
    reconnectTimerRef.current = setTimeout(() => {
      connectInternalRef.current()
    }, delay)
  }, [demoMode])

  const connectInternal = useCallback(() => {
    if (demoMode) {
      setState('connected')
      return
    }

    // Guard: prevent double-connect
    if (
      connectingRef.current ||
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return
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

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/${userId}/${sessionId}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      connectingRef.current = false
      reconnectAttemptRef.current = 0
      setState('connected')
    }

    ws.onclose = () => {
      connectingRef.current = false
      wsRef.current = null
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
    demoMode,
    userId,
    sessionId,
    scheduleReconnect,
    handleJsonMessage,
    handleBinaryMessage,
  ])

  useEffect(() => {
    connectInternalRef.current = connectInternal
  }, [connectInternal])

  const connect = useCallback(() => {
    reconnectAttemptRef.current = 0
    connectInternal()
  }, [connectInternal])

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false
    setState('disconnected')
    cleanup()
  }, [cleanup])

  const sendJson = useCallback(
    (message: ClientMessage) => {
      if (demoMode) {
        return
      }
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(message))
      }
    },
    [demoMode],
  )

  // Send binary audio
  const sendAudio = useCallback((data: ArrayBuffer) => {
    if (demoMode) {
      return
    }
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data)
    }
  }, [demoMode])

  // Send text message
  const sendText = useCallback((text: string) => {
    sendJson({ type: 'text', text })
  }, [sendJson])

  // Send image with mimeType
  const sendImage = useCallback((base64: string, mimeType: string) => {
    sendJson({ type: 'image', data: base64, mimeType })
  }, [sendJson])

  // Send negotiation action
  const sendNegotiate = useCallback((counterOffer: number, action: 'accept' | 'decline' | 'counter') => {
    sendJson({ type: 'negotiate', counterOffer, action })
  }, [sendJson])

  // Send config change (industry switch)
  const sendConfig = useCallback((industry: string) => {
    sendJson({ type: 'config', industry })
  }, [sendJson])

  const injectDemoMessage = useCallback((message: ServerMessage) => {
    setMessages(prev => [...prev, message])
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => { cleanup() }
  }, [cleanup])

  return {
    state,
    messages,
    connect,
    disconnect,
    sendAudio,
    sendText,
    sendImage,
    sendNegotiate,
    sendConfig,
    onAudioData,
    injectDemoMessage,
  }
}
