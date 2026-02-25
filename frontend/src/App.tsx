import {
  type CSSProperties,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

// bundle-dynamic-imports: cards lazy-loaded since they only render on specific message types
const ValuationCard = lazy(() => import('./components/cards/ValuationCard'))
const BookingConfirmationCard = lazy(() => import('./components/cards/BookingConfirmationCard'))
const ProductCard = lazy(() => import('./components/cards/ProductCard'))

import { Footer } from './components/layout/Footer'
import { Header } from './components/layout/Header'
import { IndustryOnboarding } from './components/layout/IndustryOnboarding'
import { TranscriptionOverlay } from './components/layout/TranscriptionOverlay'
import { VoicePanel } from './components/layout/VoicePanel'
import { ImagePreview } from './components/media/ImagePreview'
import { type PlaybackStats, useAudioWorklet } from './hooks/useAudioWorklet'
import { useDemoMode } from './hooks/useDemoMode'
import { useEkaetteSocket } from './hooks/useEkaetteSocket'
import {
  normalizeTranscriptMessages,
  sanitizeTranscriptForDisplay,
  type TranscriptMessage,
} from './lib/transcript'
import type {
  AgentStatusMessage,
  BookingConfirmation,
  ErrorMessage,
  ImageReceivedMessage,
  Industry,
  MemoryRecallMessage,
  ProductRecommendation,
  SessionStartedMessage,
  TelemetryMessage,
  TransportMode,
  ValuationResult,
} from './types'

const INDUSTRY_STORAGE_KEY = 'ekaette:onboarding:industry'

const INDUSTRY_COMPANY_MAP: Record<Industry, string> = {
  electronics: 'ekaette-electronics',
  hotel: 'ekaette-hotel',
  automotive: 'ekaette-automotive',
  fashion: 'ekaette-fashion',
}
const INDUSTRY_VALUES = Object.keys(INDUSTRY_COMPANY_MAP) as Industry[]
const INDUSTRY_VALUE_SET = new Set<Industry>(INDUSTRY_VALUES)

function parseStoredIndustry(value: string | null): Industry | null {
  if (!value) return null
  return INDUSTRY_VALUE_SET.has(value as Industry) ? (value as Industry) : null
}

function readDemoModeFlag(): boolean {
  if (typeof window === 'undefined') return false
  if (window.location.search.includes('demo=1')) return true
  return String(import.meta.env.VITE_DEMO_MODE ?? '').toLowerCase() === 'true'
}

function resolveTransportMode(): TransportMode {
  const raw = String(import.meta.env.VITE_LIVE_TRANSPORT ?? '').toLowerCase()
  return raw === 'direct-live' ? 'direct-live' : 'backend-proxy'
}

const INDUSTRY_THEMES: Record<
  Industry,
  { accent: string; accentSoft: string; title: string; hint: string }
> = {
  electronics: {
    accent: 'oklch(74% 0.21 158)',
    accentSoft: 'oklch(62% 0.14 172)',
    title: 'Electronics Trade Desk',
    hint: 'Inspect. Value. Negotiate. Book pickup.',
  },
  hotel: {
    accent: 'oklch(78% 0.15 55)',
    accentSoft: 'oklch(70% 0.12 75)',
    title: 'Hospitality Concierge',
    hint: 'Real-time booking and guest support voice assistant.',
  },
  automotive: {
    accent: 'oklch(71% 0.18 240)',
    accentSoft: 'oklch(63% 0.15 260)',
    title: 'Automotive Service Lane',
    hint: 'Trade-ins, inspections, parts and service scheduling.',
  },
  fashion: {
    accent: 'oklch(74% 0.2 20)',
    accentSoft: 'oklch(66% 0.16 345)',
    title: 'Fashion Client Studio',
    hint: 'Catalog recommendations and consultation workflows.',
  },
}

const ERROR_TOAST_DURATION = 8000
const DEBUG_EVENT_LIMIT = 40

interface DebugEventItem {
  id: number
  ts: number
  kind: string
  detail: string
}

function formatDebugTime(ts: number): string {
  const date = new Date(ts)
  return `${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(
    2,
    '0',
  )}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

function formatMs(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'n/a'
  return `${Math.round(value)}ms`
}

function App() {
  const [industry, setIndustry] = useState<Industry | null>(() => {
    if (typeof window === 'undefined') return null
    return parseStoredIndustry(window.localStorage.getItem(INDUSTRY_STORAGE_KEY))
  })
  const userId = 'demo-user'
  const [sessionId] = useState(() => `session-${Date.now()}`)
  const [isStarting, setIsStarting] = useState(false)
  const [callError, setCallError] = useState<string | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [errorToast, setErrorToast] = useState<ErrorMessage | null>(null)
  const [debugOpen, setDebugOpen] = useState(() => import.meta.env.DEV)
  const [debugEvents, setDebugEvents] = useState<DebugEventItem[]>([])
  const [playbackStats, setPlaybackStats] = useState<PlaybackStats | null>(null)
  const onAudioChunkRef = useRef<((data: ArrayBuffer) => void) | null>(null)
  const lastPlaybackUnderrunsRef = useRef(0)
  const lastPlaybackDebugAtRef = useRef(0)
  const debugEventIdRef = useRef(0)
  const activeIndustry: Industry = industry ?? 'electronics'
  const demoModeEnabled = useMemo(() => readDemoModeFlag(), [])
  const transportMode = useMemo(() => resolveTransportMode(), [])
  const tenantId = String(import.meta.env.VITE_TENANT_ID ?? 'public')

  const socket = useEkaetteSocket(userId, sessionId, {
    demoMode: demoModeEnabled,
    industry: activeIndustry,
    companyId: INDUSTRY_COMPANY_MAP[activeIndustry],
    tenantId,
    transportMode,
  })
  const demo = useDemoMode({
    onEmit: socket.injectDemoMessage,
  })
  const isConnected = socket.state === 'connected'

  const pushDebugEvent = useCallback((kind: string, detail: string) => {
    if (!import.meta.env.DEV) return
    setDebugEvents(prev => {
      const next = [
        ...prev,
        {
          id: ++debugEventIdRef.current,
          ts: Date.now(),
          kind,
          detail,
        },
      ]
      return next.length > DEBUG_EVENT_LIMIT ? next.slice(-DEBUG_EVENT_LIMIT) : next
    })
  }, [])

  const handleSpeechActivity = useCallback(
    (state: 'start' | 'end') => {
      if (!isConnected) return
      pushDebugEvent('vad', state)
      if (state === 'start') {
        socket.sendActivityStart()
      } else {
        socket.sendActivityEnd()
      }
    },
    [isConnected, pushDebugEvent, socket.sendActivityEnd, socket.sendActivityStart],
  )
  const audio = useAudioWorklet(onAudioChunkRef, {
    onSpeechActivity: handleSpeechActivity,
    onPlaybackStats: stats => {
      setPlaybackStats(stats)
      const now = Date.now()
      const underrunsIncreased = stats.underrunCount > lastPlaybackUnderrunsRef.current
      lastPlaybackUnderrunsRef.current = stats.underrunCount
      if (underrunsIncreased) {
        pushDebugEvent(
          'playback_underrun',
          `underruns=${stats.underrunCount} rebuffer=${Math.round(
            (stats.currentRebufferSamples / 24000) * 1000,
          )}ms avail=${stats.availableSamples}`,
        )
      }

      // Dev-only throttled telemetry for audio jitter/underflow debugging.
      if (
        import.meta.env.DEV &&
        (underrunsIncreased || now - lastPlaybackDebugAtRef.current > 4000)
      ) {
        lastPlaybackDebugAtRef.current = now
        console.debug('[ekaette][playback]', stats)
      }
    },
  })

  const socketStateRef = useRef(socket.state)
  const processedCountRef = useRef(0)
  const wasConnectedRef = useRef(false)
  const connectWaitTimerRef = useRef<number | null>(null)
  const isMountedRef = useRef(true)

  const clearConnectWaitTimer = useCallback(() => {
    if (connectWaitTimerRef.current != null) {
      window.clearInterval(connectWaitTimerRef.current)
      connectWaitTimerRef.current = null
    }
  }, [])

  // Single-pass message extraction (js-combine-iterations)
  const derived = useMemo(() => {
    let agentStatus: AgentStatusMessage | null = null
    let sessionStarted: SessionStartedMessage | null = null
    let telemetry: TelemetryMessage | null = null
    let valuation: ValuationResult | null = null
    let booking: BookingConfirmation | null = null
    let products: ProductRecommendation | null = null
    let imageStatus: ImageReceivedMessage | null = null
    let error: ErrorMessage | null = null
    let memoryRecall: MemoryRecallMessage | null = null
    const transcripts: TranscriptMessage[] = []

    for (const msg of socket.messages) {
      switch (msg.type) {
        case 'transcription':
          transcripts.push(msg)
          break
        case 'agent_status':
          agentStatus = msg
          break
        case 'session_started':
          sessionStarted = msg
          break
        case 'telemetry':
          telemetry = msg
          break
        case 'valuation_result':
          valuation = msg
          break
        case 'booking_confirmation':
          booking = msg
          break
        case 'product_recommendation':
          products = msg
          break
        case 'image_received':
          imageStatus = msg
          break
        case 'agent_transfer':
          break
        case 'error':
          error = msg
          break
        case 'memory_recall':
          memoryRecall = msg
          break
        // 'audio', 'interrupted', 'ping', 'session_ending' handled elsewhere
      }
    }
    return {
      agentStatus,
      sessionStarted,
      telemetry,
      valuation,
      booking,
      products,
      imageStatus,
      error,
      memoryRecall,
      transcripts,
    }
  }, [socket.messages])

  const preferLatinTranscriptDisplay = useMemo(() => {
    const lang =
      typeof navigator !== 'undefined' ? navigator.language || navigator.languages?.[0] || '' : ''
    return /^en\b/i.test(lang)
  }, [])

  const displayTranscriptMessages = useMemo(() => {
    // Filter obvious wrong-script anomalies before normalization so they don't
    // get merged into otherwise-correct partial/final bubbles.
    const sanitizedRaw = sanitizeTranscriptForDisplay(derived.transcripts, {
      preferredUserScript: preferLatinTranscriptDisplay ? 'latin' : null,
    })
    const normalized = normalizeTranscriptMessages(sanitizedRaw)
    return sanitizeTranscriptForDisplay(normalized, {
      preferredUserScript: preferLatinTranscriptDisplay ? 'latin' : null,
    })
  }, [derived.transcripts, preferLatinTranscriptDisplay])
  const rawTranscriptTail = useMemo(
    () => (debugOpen ? socket.messages.filter(msg => msg.type === 'transcription').slice(-10) : []),
    [socket.messages, debugOpen],
  )
  const socketDebug = socket.debugMetrics

  const displaySessionId = derived.sessionStarted?.sessionId ?? sessionId
  const theme = INDUSTRY_THEMES[activeIndustry]

  const rootStyle = useMemo(
    () =>
      ({
        '--industry-accent': theme.accent,
        '--industry-accent-2': theme.accentSoft,
      }) as CSSProperties,
    [theme.accent, theme.accentSoft],
  )

  useEffect(() => {
    const prevState = socketStateRef.current
    socketStateRef.current = socket.state

    // Re-initialize audio when transitioning from reconnecting → connected.
    // This handles the case where audio.stop() was called during disconnect
    // and the audio system needs to be set up again for the new connection.
    if (
      socket.state === 'connected' &&
      (prevState === 'reconnecting' || prevState === 'connecting') &&
      wasConnectedRef.current &&
      !demoModeEnabled
    ) {
      void (async () => {
        try {
          audio.stop()
          await audio.initPlayer()
          await audio.startRecording()
        } catch {
          // Audio re-init failed — call will work without mic
        }
      })()
    }

    if (socket.state === 'connected') {
      wasConnectedRef.current = true
    }
    if (socket.state === 'disconnected') {
      wasConnectedRef.current = false
    }
  }, [socket.state, audio.initPlayer, audio.startRecording, audio.stop, demoModeEnabled])

  useEffect(() => {
    onAudioChunkRef.current = socket.sendAudio
  }, [socket.sendAudio])

  useEffect(() => {
    const recover = () => {
      void audio.recoverAudioContexts()
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        recover()
      }
    }

    window.addEventListener('focus', recover)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      window.removeEventListener('focus', recover)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [audio.recoverAudioContexts])

  // Handle session_ending from server (GoAway, Live API timeout, etc.)
  useEffect(() => {
    socket.onSessionEnding.current = (reason: string) => {
      if (reason === 'live_session_ended' || reason === 'go_away') {
        // Clear stale messages and reset processedCount so old cards don't persist.
        processedCountRef.current = 0
        socket.clearMessages()
      }
    }
    return () => {
      socket.onSessionEnding.current = null
    }
  }, [socket.clearMessages, socket.onSessionEnding])

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      clearConnectWaitTimer()
    }
  }, [clearConnectWaitTimer])

  useEffect(() => {
    socket.onAudioData.current = (data: ArrayBuffer) => {
      audio.playAudioChunk(data)
    }
    return () => {
      socket.onAudioData.current = null
    }
  }, [audio.playAudioChunk, socket.onAudioData])

  useEffect(() => {
    const newMessages = socket.messages.slice(processedCountRef.current)
    processedCountRef.current = socket.messages.length
    for (const msg of newMessages) {
      if (msg.type === 'transcription') {
        const snippet = msg.text.length > 90 ? `${msg.text.slice(0, 90)}...` : msg.text
        pushDebugEvent(
          'tx',
          `${msg.role}:${msg.partial ? 'P' : 'F'} ${snippet.replace(/\s+/g, ' ')}`,
        )
      }
      if (msg.type === 'agent_transfer') {
        pushDebugEvent('transfer', `${msg.from} -> ${msg.to}`)
      }
      if (msg.type === 'interrupted') {
        pushDebugEvent('interrupted', 'server signaled interruption')
        audio.clearPlaybackBuffer()
      }
      if (msg.type === 'error') {
        pushDebugEvent('error', `${msg.code}: ${msg.message}`)
      }
      if (msg.type === 'session_started') {
        pushDebugEvent(
          'session_started',
          `${msg.sessionId} company=${msg.companyId ?? 'n/a'} voice=${msg.voice ?? 'n/a'}`,
        )
      }
    }
  }, [socket.messages, audio.clearPlaybackBuffer, pushDebugEvent])

  useEffect(() => {
    if (!isConnected) {
      setElapsedSeconds(0)
      return
    }
    const timer = window.setInterval(() => {
      setElapsedSeconds(prev => prev + 1)
    }, 1000)
    return () => {
      window.clearInterval(timer)
    }
  }, [isConnected])

  // Error toast from server error messages — auto-dismiss
  useEffect(() => {
    if (!derived.error) return
    setErrorToast(derived.error)
    const dismissTimer = window.setTimeout(() => {
      setErrorToast(null)
    }, ERROR_TOAST_DURATION)
    return () => {
      window.clearTimeout(dismissTimer)
    }
  }, [derived.error])

  const handleToggleCall = async () => {
    if (!industry) {
      setCallError('Complete onboarding before starting a call.')
      return
    }
    if (isConnected) {
      socket.sendActivityEnd()
      if (demoModeEnabled) {
        demo.reset()
      } else {
        audio.stop()
      }
      socket.disconnect()
      // Reset for clean next-call state.
      processedCountRef.current = 0
      socket.clearMessages()
      return
    }
    if (socket.state === 'connecting' || socket.state === 'reconnecting') {
      return
    }

    setIsStarting(true)
    setCallError(null)
    try {
      if (demoModeEnabled) {
        socket.connect()
        demo.reset()
        demo.play()
        return
      }
      socket.connect()
      await audio.recoverAudioContexts()
      await new Promise<void>((resolve, reject) => {
        const startedAt = Date.now()
        clearConnectWaitTimer()
        connectWaitTimerRef.current = window.setInterval(() => {
          if (socketStateRef.current === 'connected') {
            clearConnectWaitTimer()
            resolve()
            return
          }
          if (Date.now() - startedAt > 5000) {
            clearConnectWaitTimer()
            reject(new Error('WebSocket connection timeout'))
          }
        }, 50)
      })
      await audio.initPlayer()
      await audio.startRecording()
    } catch (error) {
      if (isMountedRef.current) {
        setCallError(error instanceof Error ? error.message : 'Call start failed')
      }
      socket.disconnect()
      if (!demoModeEnabled) {
        audio.stop()
      }
    } finally {
      clearConnectWaitTimer()
      if (isMountedRef.current) {
        setIsStarting(false)
      }
    }
  }

  const handleImageSelected = useCallback(
    (base64: string, mimeType: string) => {
      socket.sendImage(base64, mimeType)
    },
    [socket],
  )

  const handleSendText = useCallback(
    (text: string) => {
      if (!isConnected) return
      socket.sendText(text)
    },
    [isConnected, socket],
  )

  const handleOnboardingComplete = useCallback((selectedIndustry: Industry) => {
    setIndustry(selectedIndustry)
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(INDUSTRY_STORAGE_KEY, selectedIndustry)
    }
  }, [])

  const handleAcceptValuation = useCallback(() => {
    if (derived.valuation) {
      socket.sendNegotiate(derived.valuation.price, 'accept')
    }
  }, [derived.valuation, socket])

  const handleDeclineValuation = useCallback(() => {
    if (derived.valuation) {
      socket.sendNegotiate(0, 'decline')
    }
  }, [derived.valuation, socket])

  const handleCounterOffer = useCallback(
    (value: number) => {
      socket.sendNegotiate(value, 'counter')
    },
    [socket],
  )

  return (
    <div
      className="app-shell h-screen min-h-screen overflow-hidden text-foreground supports-[height:100dvh]:h-dvh supports-[height:100dvh]:min-h-dvh"
      style={rootStyle}
    >
      <div className="atmosphere-layer" aria-hidden />

      <div className="relative mx-auto flex h-full w-full max-w-6xl flex-col px-3 pt-[calc(env(safe-area-inset-top)+0.75rem)] pb-[calc(env(safe-area-inset-bottom)+0.75rem)] sm:px-6 sm:pt-5 sm:pb-6 lg:px-8">
        {!industry ? (
          <main className="mt-3 grid min-h-0 flex-1 overflow-y-auto pb-1 sm:mt-4 sm:pb-0">
            <IndustryOnboarding onComplete={handleOnboardingComplete} />
          </main>
        ) : (
          <>
            <div className="hidden lg:block">
              <Header hint={theme.hint} industry={activeIndustry} connectionState={socket.state} />
            </div>

            <main className="mt-3 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pb-1 sm:mt-4 sm:pb-0 lg:grid lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] lg:overflow-hidden lg:pb-0">
              <div className="lg:hidden">
                <Header
                  hint={theme.hint}
                  industry={activeIndustry}
                  connectionState={socket.state}
                />
              </div>

              <div className="flex min-h-0 flex-col gap-4 lg:overflow-y-auto lg:pr-1">
                <VoicePanel
                  title={theme.title}
                  sessionId={displaySessionId}
                  elapsedSeconds={elapsedSeconds}
                  isConnected={isConnected}
                  isStarting={isStarting}
                  latestAgentStatus={derived.agentStatus ?? undefined}
                  latestTelemetry={derived.telemetry ?? undefined}
                  latestMemoryRecall={derived.memoryRecall ?? undefined}
                  audioError={audio.error}
                  callError={callError}
                />

                {/* Card stack — only latest of each type shown */}
                {derived.imageStatus?.previewUrl ? (
                  <ImagePreview
                    src={derived.imageStatus.previewUrl}
                    status={derived.imageStatus.status}
                  />
                ) : null}

                <Suspense fallback={null}>
                  {derived.valuation ? (
                    <ValuationCard
                      deviceName={derived.valuation.deviceName}
                      condition={derived.valuation.condition}
                      price={derived.valuation.price}
                      currency={derived.valuation.currency}
                      details={derived.valuation.details}
                      onAccept={handleAcceptValuation}
                      onDecline={handleDeclineValuation}
                      onCounterOffer={handleCounterOffer}
                    />
                  ) : null}

                  {derived.booking ? (
                    <BookingConfirmationCard
                      confirmationId={derived.booking.confirmationId}
                      date={derived.booking.date}
                      time={derived.booking.time}
                      location={derived.booking.location}
                      service={derived.booking.service}
                    />
                  ) : null}

                  {derived.products
                    ? derived.products.products.map((product, idx) => (
                        <ProductCard
                          key={`${product.name}-${idx}`}
                          name={product.name}
                          price={product.price}
                          currency={product.currency}
                          available={product.available}
                          description={product.description}
                        />
                      ))
                    : null}
                </Suspense>
              </div>

              <TranscriptionOverlay messages={displayTranscriptMessages} />
            </main>

            {/* Error toast */}
            {errorToast ? (
              <div
                role="alert"
                className="error-toast fixed bottom-[calc(env(safe-area-inset-bottom)+6rem)] left-1/2 z-50 w-[min(calc(100vw-1.5rem),32rem)] -translate-x-1/2 rounded-xl border border-destructive/50 bg-destructive/15 px-4 py-3 text-destructive text-sm backdrop-blur-sm sm:bottom-24"
              >
                {errorToast.message}
              </div>
            ) : null}

            <Footer
              connectionState={socket.state}
              isStarting={isStarting}
              onToggleCall={handleToggleCall}
              onSendText={handleSendText}
              onImageSelected={handleImageSelected}
            />
          </>
        )}
      </div>

      {import.meta.env.DEV && (
        <div className="pointer-events-none fixed right-3 bottom-3 z-50 hidden max-w-[22rem] flex-col items-end gap-2 sm:flex">
          <div className="pointer-events-auto rounded-2xl border border-border/80 bg-card/85 px-3 py-2 text-[10px] text-muted-foreground uppercase tracking-[0.12em] shadow-lg backdrop-blur">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span>
                RTT <span className="text-foreground">{formatMs(socketDebug.heartbeatRttMs)}</span>
              </span>
              <span>
                Jitter{' '}
                <span className="text-foreground">{formatMs(socketDebug.heartbeatJitterMs)}</span>
              </span>
              <span>
                Buf{' '}
                <span className="text-foreground">
                  {playbackStats
                    ? `${Math.round((playbackStats.availableSamples / 24000) * 1000)}ms`
                    : 'n/a'}
                </span>
              </span>
              <span>
                Und <span className="text-foreground">{playbackStats?.underrunCount ?? 0}</span>
              </span>
              <span>
                BP <span className="text-foreground">{socketDebug.audioTxBackpressureCount}</span>
              </span>
              <span>
                TxDrop <span className="text-foreground">{socketDebug.audioTxDropCount}</span>
              </span>
              <span>
                RxGap <span className="text-foreground">{socketDebug.audioRxDropSuspectCount}</span>
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setDebugOpen(prev => !prev)}
            className="pointer-events-auto rounded-full border border-border/80 bg-card/85 px-3 py-1 font-medium text-[0.65rem] text-muted-foreground uppercase tracking-[0.16em] shadow-lg backdrop-blur"
          >
            {debugOpen ? 'Hide Debug' : 'Show Debug'}
          </button>
          {debugOpen && (
            <aside className="pointer-events-auto w-full rounded-2xl border border-border/80 bg-background/92 p-3 shadow-2xl backdrop-blur">
              <div className="flex items-center justify-between gap-2">
                <p className="font-semibold text-muted-foreground text-xs uppercase tracking-[0.16em]">
                  Live Debug
                </p>
                <button
                  type="button"
                  onClick={() => setDebugEvents([])}
                  className="rounded-md border border-border/70 px-2 py-1 text-[10px] text-muted-foreground uppercase tracking-[0.12em]"
                >
                  Clear
                </button>
              </div>

              <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] leading-4">
                <div className="rounded-lg border border-border/60 bg-card/60 p-2">
                  <p className="text-muted-foreground">Socket</p>
                  <p className="font-medium text-foreground">
                    {socket.state} / {transportMode}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    RTT {formatMs(socketDebug.heartbeatRttMs)} · jitter{' '}
                    {formatMs(socketDebug.heartbeatJitterMs)} · pending{' '}
                    {socketDebug.heartbeatPending} · timeouts {socketDebug.heartbeatTimeouts}
                  </p>
                </div>
                <div className="rounded-lg border border-border/60 bg-card/60 p-2">
                  <p className="text-muted-foreground">Transcript</p>
                  <p className="font-medium text-foreground">
                    raw {derived.transcripts.length} / normalized {displayTranscriptMessages.length}
                  </p>
                </div>
                <div className="col-span-2 rounded-lg border border-border/60 bg-card/60 p-2">
                  <p className="text-muted-foreground">Playback</p>
                  <p className="font-medium text-foreground">
                    {playbackStats
                      ? `buffer=${playbackStats.availableSamples} samples · ${
                          playbackStats.isBuffering ? 'buffering' : 'playing'
                        } · rebuffer=${Math.round(
                          (playbackStats.currentRebufferSamples / 24000) * 1000,
                        )}ms · underruns=${playbackStats.underrunCount}`
                      : 'waiting for audio'}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    tx {socketDebug.audioTxChunks} chunks /{' '}
                    {Math.round(socketDebug.audioTxBytes / 1024)} KiB · rx{' '}
                    {socketDebug.audioRxChunks} chunks /{' '}
                    {Math.round(socketDebug.audioRxBytes / 1024)} KiB · txDrop{' '}
                    {socketDebug.audioTxDropCount} · bp {socketDebug.audioTxBackpressureCount} ·
                    rxGap {socketDebug.audioRxDropSuspectCount} (
                    {formatMs(socketDebug.audioRxLastGapMs)})
                  </p>
                </div>
              </div>

              <div className="mt-3">
                <p className="mb-1 text-[10px] text-muted-foreground uppercase tracking-[0.16em]">
                  Raw Transcript Tail
                </p>
                <div className="max-h-28 space-y-1 overflow-y-auto rounded-lg border border-border/60 bg-card/40 p-2 font-mono text-[10px] leading-4">
                  {rawTranscriptTail.length === 0 ? (
                    <p className="text-muted-foreground">No transcript events yet.</p>
                  ) : (
                    rawTranscriptTail.map((msg, idx) => (
                      <p key={`${idx}-${msg.role}-${msg.partial ? 'p' : 'f'}`}>
                        <span className="text-muted-foreground">
                          {msg.role[0].toUpperCase()}:{msg.partial ? 'P' : 'F'}
                        </span>{' '}
                        {msg.text}
                      </p>
                    ))
                  )}
                </div>
              </div>

              <div className="mt-3">
                <p className="mb-1 text-[10px] text-muted-foreground uppercase tracking-[0.16em]">
                  Event Trace
                </p>
                <div className="max-h-44 space-y-1 overflow-y-auto rounded-lg border border-border/60 bg-card/40 p-2 font-mono text-[10px] leading-4">
                  {debugEvents.length === 0 ? (
                    <p className="text-muted-foreground">No events captured yet.</p>
                  ) : (
                    [...debugEvents].reverse().map(event => (
                      <p key={event.id}>
                        <span className="text-muted-foreground">
                          {formatDebugTime(event.ts)} [{event.kind}]
                        </span>{' '}
                        {event.detail}
                      </p>
                    ))
                  )}
                </div>
              </div>
            </aside>
          )}
        </div>
      )}
    </div>
  )
}

export default App
