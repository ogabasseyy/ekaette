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

import { AiDisclosureBanner } from './components/layout/AiDisclosureBanner'
import { ConsentModal } from './components/layout/ConsentModal'
import { Footer } from './components/layout/Footer'
import { Header } from './components/layout/Header'
import { NavBar } from './components/layout/NavBar'
import { TranscriptionOverlay } from './components/layout/TranscriptionOverlay'
import { VendorSetupWizard } from './components/layout/VendorSetupWizard'
import { VoicePanel } from './components/layout/VoicePanel'
import { ImagePreview } from './components/media/ImagePreview'
import {
  type NoiseCancellationLevel,
  type PlaybackStats,
  useAudioWorklet,
} from './hooks/useAudioWorklet'
import { useConsent } from './hooks/useConsent'
import { useDemoMode } from './hooks/useDemoMode'
import { SocketConnectError, useEkaetteSocket } from './hooks/useEkaetteSocket'
import {
  normalizeTranscriptMessages,
  preferFinalTranscriptMessages,
  sanitizeTranscriptForDisplay,
  type TranscriptMessage,
} from './lib/transcript'
import type {
  AgentStatusMessage,
  BookingConfirmation,
  ErrorMessage,
  ImageReceivedMessage,
  IndustryTemplateMeta,
  MemoryRecallMessage,
  OnboardingCompanyMeta,
  OnboardingConfigResponse,
  ProductRecommendation,
  RuntimeBootstrapResponse,
  SessionStartedMessage,
  TelemetryMessage,
  TransportMode,
  ValuationResult,
} from './types'

const INDUSTRY_STORAGE_KEY = 'ekaette:onboarding:industry'
const TEMPLATE_STORAGE_KEY = 'ekaette:onboarding:templateId'
const COMPANY_STORAGE_KEY = 'ekaette:onboarding:companyId'
const TENANT_STORAGE_KEY = 'ekaette:onboarding:tenantId'

// ═══ Hardcoded Fallbacks (legacy compat, used until registry fetch is wired) ═══

interface ThemeConfig {
  accent: string
  accentSoft: string
  title: string
  hint: string
}

const FALLBACK_COMPANY_MAP = {
  electronics: 'ekaette-electronics',
  hotel: 'ekaette-hotel',
  automotive: 'ekaette-automotive',
  fashion: 'ekaette-fashion',
} satisfies Record<string, string>

const FALLBACK_THEMES = {
  electronics: {
    accent: 'oklch(74% 0.21 158)',
    accentSoft: 'oklch(62% 0.14 172)',
    title: 'Hardware Trade Desk',
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
} satisfies Record<string, ThemeConfig>

const FALLBACK_LABELS = {
  electronics: 'Hardware',
  hotel: 'Hotel',
  automotive: 'Automotive',
  fashion: 'Fashion',
} satisfies Record<string, string>

const DEFAULT_THEME: ThemeConfig = {
  accent: 'oklch(74% 0.21 158)',
  accentSoft: 'oklch(62% 0.14 172)',
  title: 'Ekaette Live Desk',
  hint: 'AI-powered customer service.',
}

function createClientSessionId(): string {
  // crypto.randomUUID() is cryptographically secure (Web Crypto API).
  // Date.now() prefix aids log correlation / debugging; uniqueness is guaranteed by the UUID.
  const uuid = crypto.randomUUID()
  return `session-${Date.now()}-${uuid.slice(0, 12)}`
}

function parseStoredIndustry(value: string | null): string | null {
  if (!value || !value.trim()) return null
  return value
}

function parseStoredValue(value: string | null): string | null {
  if (!value || !value.trim()) return null
  return value.trim()
}

function readDemoModeFlag(): boolean {
  if (typeof window === 'undefined') return false
  const params = new URLSearchParams(window.location.search)
  if (params.get('demo') === '1') return true
  return String(import.meta.env.VITE_DEMO_MODE ?? '').toLowerCase() === 'true'
}

function resolveCustomerOnboardingEnabled(): boolean {
  // Vendor setup UI is enabled in test and development modes.
  if (import.meta.env.MODE === 'test') return true
  if (import.meta.env.DEV) return true
  return false
}

function resolveTransportMode(): TransportMode {
  const raw = String(import.meta.env.VITE_LIVE_TRANSPORT ?? '').toLowerCase()
  return raw === 'direct-live' ? 'direct-live' : 'backend-proxy'
}

function resolveNoiseCancellationLevel(): NoiseCancellationLevel {
  const raw = String(import.meta.env.VITE_NOISE_CANCELLATION_LEVEL ?? 'aggressive')
    .toLowerCase()
    .trim()
  if (raw === 'off' || raw === 'standard' || raw === 'aggressive') {
    return raw
  }
  return 'aggressive'
}

function resolveTheme(templateId: string, templates: IndustryTemplateMeta[] | null): ThemeConfig {
  // Server-provided templates take priority
  if (templates) {
    const match = templates.find(t => t.id === templateId)
    if (match) return match.theme
  }
  // Fallback to hardcoded
  return (FALLBACK_THEMES as Record<string, ThemeConfig>)[templateId] ?? DEFAULT_THEME
}

function resolveCompanyFromConfig(
  templateId: string,
  companies: OnboardingCompanyMeta[] | null,
  defaults?: { templateId: string; companyId: string } | null,
): string | null {
  if (!companies || companies.length === 0) return null
  if (
    defaults &&
    defaults.templateId === templateId &&
    companies.some(
      company => company.id === defaults.companyId && company.templateId === templateId,
    )
  ) {
    return defaults.companyId
  }
  const match = companies.find(company => company.templateId === templateId)
  return match?.id ?? null
}

function resolveCompanyId(
  templateId: string,
  onboardingConfig: OnboardingConfigResponse | null,
): string {
  const fromConfig = resolveCompanyFromConfig(
    templateId,
    onboardingConfig?.companies ?? null,
    onboardingConfig?.defaults ?? null,
  )
  if (fromConfig) return fromConfig
  return (FALLBACK_COMPANY_MAP as Record<string, string>)[templateId] ?? `ekaette-${templateId}`
}

function resolveTemplateLabel(
  templateId: string,
  templates: IndustryTemplateMeta[] | null,
): string {
  if (templates) {
    const match = templates.find(t => t.id === templateId)
    if (match) return match.label
  }
  return (FALLBACK_LABELS as Record<string, string>)[templateId] ?? templateId
}

const ERROR_TOAST_DURATION = 8000
const DEBUG_EVENT_LIMIT = 40

interface DebugEventItem {
  id: number
  ts: number
  kind: string
  detail: string
}

type OnboardingConfigStatus = 'idle' | 'loading' | 'ready' | 'compat' | 'error'
type RuntimeBootstrapStatus = 'idle' | 'loading' | 'ready' | 'compat' | 'error'

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

function formatBooleanish(value: boolean | string | null | undefined): string {
  if (value == null) return 'n/a'
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  return value
}

function isRuntimeBootstrapResponse(value: unknown): value is RuntimeBootstrapResponse {
  if (typeof value !== 'object' || value === null) return false
  const data = value as Partial<RuntimeBootstrapResponse> & Record<string, unknown>
  return (
    typeof data.apiVersion === 'string' &&
    typeof data.tenantId === 'string' &&
    typeof data.companyId === 'string' &&
    typeof data.industryTemplateId === 'string' &&
    typeof data.industry === 'string' &&
    typeof data.voice === 'string' &&
    Array.isArray(data.capabilities)
  )
}

function App() {
  const [industry, setIndustry] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    // In dev mode (not test), start null so the vendor setup wizard renders first.
    if (import.meta.env.DEV && import.meta.env.MODE !== 'test') return null
    return (
      parseStoredValue(window.localStorage.getItem(TEMPLATE_STORAGE_KEY)) ??
      parseStoredIndustry(window.localStorage.getItem(INDUSTRY_STORAGE_KEY))
    )
  })
  const [companySelection, setCompanySelection] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    if (import.meta.env.DEV && import.meta.env.MODE !== 'test') return null
    return parseStoredValue(window.localStorage.getItem(COMPANY_STORAGE_KEY))
  })
  const [tenantSelection, setTenantSelection] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    return parseStoredValue(window.localStorage.getItem(TENANT_STORAGE_KEY))
  })
  const [onboardingConfig, setOnboardingConfig] = useState<OnboardingConfigResponse | null>(null)
  const [onboardingConfigStatus, setOnboardingConfigStatus] =
    useState<OnboardingConfigStatus>('idle')
  const [onboardingConfigError, setOnboardingConfigError] = useState<string | null>(null)
  const [runtimeBootstrapStatus, setRuntimeBootstrapStatus] =
    useState<RuntimeBootstrapStatus>('idle')
  const [runtimeBootstrapError, setRuntimeBootstrapError] = useState<string | null>(null)
  const [_onboardingReloadNonce, setOnboardingReloadNonce] = useState(0)
  const userId = 'demo-user'
  const [sessionId] = useState<string>(() => createClientSessionId())
  const [isStarting, setIsStarting] = useState(false)
  const [callError, setCallError] = useState<string | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [errorToast, setErrorToast] = useState<ErrorMessage | null>(null)
  const [manualOnboardingOverride, setManualOnboardingOverride] = useState(false)
  const [debugOpen, setDebugOpen] = useState(() => import.meta.env.DEV)
  const [debugEvents, setDebugEvents] = useState<DebugEventItem[]>([])
  const [playbackStats, setPlaybackStats] = useState<PlaybackStats | null>(null)
  const onAudioChunkRef = useRef<((data: ArrayBuffer) => void) | null>(null)
  const lastPlaybackUnderrunsRef = useRef(0)
  const lastPlaybackDebugAtRef = useRef(0)
  const debugEventIdRef = useRef(0)
  const activeIndustry = industry ?? 'electronics'
  const { hasConsented, acceptConsent, declineConsent } = useConsent()
  const [aiBannerDismissed, setAiBannerDismissed] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.sessionStorage.getItem('ekaette:ui:ai-banner-dismissed') === '1',
  )
  const handleDismissBanner = useCallback(() => {
    setAiBannerDismissed(true)
    window.sessionStorage.setItem('ekaette:ui:ai-banner-dismissed', '1')
  }, [])
  const customerOnboardingEnabled = useMemo(() => resolveCustomerOnboardingEnabled(), [])
  const demoModeEnabled = useMemo(() => readDemoModeFlag(), [])
  const transportMode = useMemo(() => resolveTransportMode(), [])
  const noiseCancellationLevel = useMemo(() => resolveNoiseCancellationLevel(), [])
  const endUserOnboardingEnabled =
    customerOnboardingEnabled &&
    String(import.meta.env.VITE_END_USER_ONBOARDING_ENABLED ?? '').toLowerCase() === 'true'
  const allowOnboardingCompatFallback =
    customerOnboardingEnabled &&
    (import.meta.env.DEV ||
      demoModeEnabled ||
      String(import.meta.env.VITE_ONBOARDING_COMPAT_FALLBACK ?? '').toLowerCase() === 'true')
  const forceManualOnboarding =
    customerOnboardingEnabled &&
    ((import.meta.env.DEV && import.meta.env.MODE !== 'test') ||
      endUserOnboardingEnabled ||
      manualOnboardingOverride)
  const showRuntimeBootstrapLoading = !forceManualOnboarding && runtimeBootstrapStatus === 'loading'
  const showRuntimeBootstrapError =
    !forceManualOnboarding && runtimeBootstrapStatus === 'error' && !allowOnboardingCompatFallback
  const canRenderOnboardingSelection =
    customerOnboardingEnabled &&
    (forceManualOnboarding ||
      runtimeBootstrapStatus === 'compat' ||
      runtimeBootstrapStatus === 'idle')
  const showOnboardingConfigLoading =
    canRenderOnboardingSelection &&
    (onboardingConfigStatus === 'idle' || onboardingConfigStatus === 'loading')
  const tenantId = tenantSelection ?? String(import.meta.env.VITE_TENANT_ID ?? 'public')
  const templates = onboardingConfig?.templates ?? null
  const companies = onboardingConfig?.companies ?? null

  const fallbackCompanyId = resolveCompanyId(activeIndustry, onboardingConfig)
  const companyId = useMemo(() => {
    if (companySelection && companies?.some(company => company.id === companySelection)) {
      const company = companies.find(item => item.id === companySelection)
      if (company?.templateId === activeIndustry) return companySelection
    }
    return fallbackCompanyId
  }, [activeIndustry, companies, companySelection, fallbackCompanyId])
  const theme = resolveTheme(activeIndustry, templates)
  const templateLabel = resolveTemplateLabel(activeIndustry, templates)
  const socket = useEkaetteSocket(userId, sessionId, {
    demoMode: demoModeEnabled,
    industry: activeIndustry,
    companyId,
    tenantId,
    transportMode,
  })
  const demo = useDemoMode({
    industryTemplateId: activeIndustry,
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

  const audioRef = useRef<ReturnType<typeof useAudioWorklet> | null>(null)

  const handleSpeechActivity = useCallback(
    (state: 'start' | 'end') => {
      if (!isConnected) return
      pushDebugEvent('vad', state)
      if (state === 'start') {
        socket.sendActivityStart()
        // Preemptively clear agent playback so user hears silence immediately.
        // The server will also send an 'interrupted' event, but this avoids
        // the round-trip latency for a snappier barge-in experience.
        audioRef.current?.clearPlaybackBuffer()
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
    noiseCancellationLevel,
  })
  audioRef.current = audio

  const socketStateRef = useRef(socket.state)
  const processedCountRef = useRef(0)
  const wasConnectedRef = useRef(false)
  const isMountedRef = useRef(true)

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
  const preferFinalTranscriptDisplay = useMemo(
    () => String(import.meta.env.VITE_TRANSCRIPT_PREFER_FINAL ?? 'true').toLowerCase() !== 'false',
    [],
  )

  const displayTranscriptMessages = useMemo(() => {
    // Filter obvious wrong-script anomalies before normalization so they don't
    // get merged into otherwise-correct partial/final bubbles.
    const sanitizedRaw = sanitizeTranscriptForDisplay(derived.transcripts, {
      preferredUserScript: preferLatinTranscriptDisplay ? 'latin' : null,
    })
    const normalized = normalizeTranscriptMessages(sanitizedRaw)
    const displayReady = sanitizeTranscriptForDisplay(normalized, {
      preferredUserScript: preferLatinTranscriptDisplay ? 'latin' : null,
    })
    return preferFinalTranscriptDisplay ? preferFinalTranscriptMessages(displayReady) : displayReady
  }, [derived.transcripts, preferFinalTranscriptDisplay, preferLatinTranscriptDisplay])
  const rawTranscriptTail = useMemo(
    () => (debugOpen ? socket.messages.filter(msg => msg.type === 'transcription').slice(-10) : []),
    [socket.messages, debugOpen],
  )
  const socketDebug = socket.debugMetrics
  const micCaptureDiagnostics = audio.micCaptureDiagnostics

  const displaySessionId = derived.sessionStarted?.sessionId ?? sessionId

  const rootStyle = useMemo(
    () =>
      ({
        '--industry-accent': theme.accent,
        '--industry-accent-2': theme.accentSoft,
      }) as CSSProperties,
    [theme.accent, theme.accentSoft],
  )

  useEffect(() => {
    if (forceManualOnboarding) {
      setRuntimeBootstrapStatus('compat')
      setRuntimeBootstrapError(null)
      return
    }
    let disposed = false
    const controller = new AbortController()

    async function loadRuntimeBootstrap() {
      setRuntimeBootstrapStatus('loading')
      setRuntimeBootstrapError(null)
      try {
        const response = await fetch(
          `/api/v1/runtime/bootstrap?tenantId=${encodeURIComponent(tenantId)}`,
          {
            signal: controller.signal,
            headers: { Accept: 'application/json' },
          },
        )
        const payload =
          response.headers.get('content-type')?.includes('application/json') === true
            ? ((await response.json()) as Record<string, unknown>)
            : null

        if (!response.ok) {
          if (disposed) return
          const message =
            (payload?.error as string | undefined) ??
            `Runtime bootstrap request failed (${response.status})`
          setRuntimeBootstrapError(message)
          setRuntimeBootstrapStatus(allowOnboardingCompatFallback ? 'compat' : 'error')
          return
        }

        if (!isRuntimeBootstrapResponse(payload)) {
          if (disposed) return
          setRuntimeBootstrapError('Invalid runtime bootstrap payload')
          setRuntimeBootstrapStatus(allowOnboardingCompatFallback ? 'compat' : 'error')
          return
        }

        const bootstrap = payload
        if (disposed) return
        const nextTemplate =
          bootstrap.industryTemplateId?.trim() || bootstrap.industry?.trim() || 'electronics'
        const nextCompany = bootstrap.companyId?.trim() || ''
        const nextTenant = bootstrap.tenantId?.trim() || tenantId

        setIndustry(nextTemplate)
        setCompanySelection(nextCompany || null)
        setTenantSelection(nextTenant)
        setRuntimeBootstrapStatus('ready')
        setRuntimeBootstrapError(null)

        if (typeof window !== 'undefined') {
          window.localStorage.setItem(INDUSTRY_STORAGE_KEY, nextTemplate)
          window.localStorage.setItem(TEMPLATE_STORAGE_KEY, nextTemplate)
          if (nextCompany) {
            window.localStorage.setItem(COMPANY_STORAGE_KEY, nextCompany)
          } else {
            window.localStorage.removeItem(COMPANY_STORAGE_KEY)
          }
          window.localStorage.setItem(TENANT_STORAGE_KEY, nextTenant)
        }
      } catch {
        if (disposed) return
        setRuntimeBootstrapError('Unable to initialize runtime setup')
        setRuntimeBootstrapStatus(allowOnboardingCompatFallback ? 'compat' : 'error')
      }
    }

    void loadRuntimeBootstrap()
    return () => {
      disposed = true
      controller.abort()
    }
  }, [allowOnboardingCompatFallback, forceManualOnboarding, tenantId])

  useEffect(() => {
    const shouldLoadOnboardingConfig = canRenderOnboardingSelection
    if (!shouldLoadOnboardingConfig) return

    let disposed = false
    const controller = new AbortController()

    async function loadOnboardingConfig() {
      setOnboardingConfigStatus('loading')
      setOnboardingConfigError(null)
      try {
        const response = await fetch(
          `/api/onboarding/config?tenantId=${encodeURIComponent(tenantId)}`,
          {
            signal: controller.signal,
            headers: { Accept: 'application/json' },
          },
        )
        if (!response.ok) {
          if (disposed) return
          setOnboardingConfigStatus(allowOnboardingCompatFallback ? 'compat' : 'error')
          setOnboardingConfigError(`Onboarding config request failed (${response.status})`)
          return
        }
        const payload = (await response.json()) as OnboardingConfigResponse
        if (disposed) return
        setOnboardingConfig(payload)
        setOnboardingConfigStatus('ready')
      } catch {
        if (disposed) return
        setOnboardingConfigStatus(allowOnboardingCompatFallback ? 'compat' : 'error')
        setOnboardingConfigError('Unable to load onboarding configuration')
      }
    }

    void loadOnboardingConfig()
    return () => {
      disposed = true
      controller.abort()
    }
  }, [allowOnboardingCompatFallback, canRenderOnboardingSelection, tenantId])

  useEffect(() => {
    if (!onboardingConfig || !industry || companySelection) return
    const resolvedCompany = resolveCompanyFromConfig(
      industry,
      onboardingConfig.companies,
      onboardingConfig.defaults,
    )
    if (!resolvedCompany) return
    setCompanySelection(resolvedCompany)
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(COMPANY_STORAGE_KEY, resolvedCompany)
    }
  }, [onboardingConfig, industry, companySelection])

  useEffect(() => {
    const started = derived.sessionStarted
    if (!started) return

    const runtimeTemplateId =
      (typeof started.industryTemplateId === 'string' && started.industryTemplateId.trim()) ||
      (typeof started.industry === 'string' && started.industry.trim()) ||
      null
    const runtimeCompanyId =
      typeof started.companyId === 'string' && started.companyId.trim() ? started.companyId : null
    const nextTemplateId = runtimeTemplateId
    const nextCompanyId = runtimeCompanyId
    const nextTenantId =
      typeof started.tenantId === 'string' && started.tenantId.trim() ? started.tenantId : null

    if (nextTemplateId && nextTemplateId !== industry) {
      setIndustry(nextTemplateId)
    }
    if (nextCompanyId && nextCompanyId !== companySelection) {
      setCompanySelection(nextCompanyId)
    }
    if (nextTenantId && nextTenantId !== tenantSelection) {
      setTenantSelection(nextTenantId)
    }

    if (typeof window !== 'undefined') {
      if (nextTemplateId) {
        window.localStorage.setItem(TEMPLATE_STORAGE_KEY, nextTemplateId)
        window.localStorage.setItem(INDUSTRY_STORAGE_KEY, nextTemplateId) // legacy compat alias
      }
      if (nextCompanyId) {
        window.localStorage.setItem(COMPANY_STORAGE_KEY, nextCompanyId)
      }
      if (nextTenantId) {
        window.localStorage.setItem(TENANT_STORAGE_KEY, nextTenantId)
      }
    }
  }, [derived.sessionStarted, industry, companySelection, tenantSelection])

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
    }
  }, [])

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
      setCallError('Complete vendor setup before starting a call.')
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
        await socket.connect()
        demo.reset()
        demo.play()
        return
      }
      const connectPromise = socket.connect()
      await audio.recoverAudioContexts()
      await connectPromise
      await audio.initPlayer()
      await audio.startRecording()
    } catch (error) {
      if (isMountedRef.current) {
        if (error instanceof SocketConnectError) {
          setCallError(error.message)
        } else {
          setCallError(error instanceof Error ? error.message : 'Call start failed')
        }
      }
      socket.disconnect()
      if (!demoModeEnabled) {
        audio.stop()
      }
    } finally {
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

  const handleOnboardingComplete = useCallback(
    (selection: { templateId: string; companyId: string }) => {
      setManualOnboardingOverride(false)
      setIndustry(selection.templateId)
      setCompanySelection(selection.companyId)
      setTenantSelection(tenantId)
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(INDUSTRY_STORAGE_KEY, selection.templateId)
        window.localStorage.setItem(TEMPLATE_STORAGE_KEY, selection.templateId)
        window.localStorage.setItem(COMPANY_STORAGE_KEY, selection.companyId)
        window.localStorage.setItem(TENANT_STORAGE_KEY, tenantId)
      }
    },
    [tenantId],
  )

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

  const handleRetryOnboardingConfig = useCallback(() => {
    setOnboardingReloadNonce(value => value + 1)
  }, [])

  const _resetClientUiState = useCallback(() => {
    processedCountRef.current = 0
    socket.clearMessages()
    setElapsedSeconds(0)
    setCallError(null)
    setErrorToast(null)
    setDebugEvents([])
    setPlaybackStats(null)
    lastPlaybackUnderrunsRef.current = 0
    lastPlaybackDebugAtRef.current = 0
  }, [socket.clearMessages])
  void _resetClientUiState

  return (
    <div
      className="app-shell h-screen min-h-screen overflow-hidden text-foreground supports-[height:100dvh]:h-dvh supports-[height:100dvh]:min-h-dvh"
      style={rootStyle}
    >
      {!hasConsented && <ConsentModal onAccept={acceptConsent} onDecline={declineConsent} />}
      <div className="atmosphere-layer" aria-hidden />
      <NavBar activePage="voice" />

      <div className="relative mx-auto flex h-full w-full max-w-6xl flex-col px-3 pt-[calc(env(safe-area-inset-top)+0.75rem)] pb-[calc(env(safe-area-inset-bottom)+0.75rem)] sm:px-6 sm:pt-5 sm:pb-6 lg:px-8">
        {!industry ? (
          <main className="mt-3 grid min-h-0 flex-1 overflow-y-auto pb-1 sm:mt-4 sm:pb-0">
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
              {showRuntimeBootstrapLoading ? (
                <section className="panel-glass w-full px-4 py-5 sm:px-7 sm:py-8">
                  <p className="text-[0.58rem] text-muted-foreground uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
                    Runtime Setup
                  </p>
                  <h1 className="mt-2 font-display text-white text-xl leading-tight sm:text-3xl">
                    Preparing your workspace
                  </h1>
                  <p className="mt-2 max-w-2xl text-muted-foreground text-xs leading-relaxed sm:text-sm">
                    Loading company and industry configuration.
                  </p>
                </section>
              ) : null}

              {showRuntimeBootstrapError ? (
                <section className="panel-glass w-full px-4 py-5 sm:px-7 sm:py-8">
                  <p className="text-[0.58rem] text-muted-foreground uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
                    Runtime Setup
                  </p>
                  <h1 className="mt-2 font-display text-white text-xl leading-tight sm:text-3xl">
                    Runtime Setup Unavailable
                  </h1>
                  <p className="mt-2 max-w-2xl text-muted-foreground text-xs leading-relaxed sm:text-sm">
                    {runtimeBootstrapError ?? 'Unable to initialize runtime configuration.'}
                  </p>
                  <div className="mt-6 flex justify-stretch sm:justify-end">
                    <button
                      type="button"
                      onClick={handleRetryOnboardingConfig}
                      className="w-full rounded-full border border-primary/50 bg-primary/10 px-5 py-2.5 font-semibold text-primary text-sm transition hover:bg-primary/15 sm:w-auto sm:py-2"
                    >
                      Retry
                    </button>
                  </div>
                </section>
              ) : null}

              {onboardingConfigStatus === 'compat' ? (
                <div className="panel-glass px-4 py-3 text-muted-foreground text-xs sm:px-5">
                  Using local configuration because the backend setup service is unavailable.
                </div>
              ) : null}

              {showOnboardingConfigLoading ? (
                <section className="panel-glass w-full px-4 py-5 sm:px-7 sm:py-8" aria-busy="true">
                  <p className="text-[0.58rem] text-muted-foreground uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
                    Vendor Setup
                  </p>
                  <h1 className="mt-2 font-display text-white text-xl leading-tight sm:text-3xl">
                    Loading configuration
                  </h1>
                  <p className="mt-2 max-w-2xl text-muted-foreground text-xs leading-relaxed sm:text-sm">
                    Fetching available industries and companies for your tenant.
                  </p>
                  <span className="sr-only">Loading industry options</span>
                </section>
              ) : null}

              {onboardingConfigStatus === 'error' && !allowOnboardingCompatFallback ? (
                <section className="panel-glass w-full px-4 py-5 sm:px-7 sm:py-8">
                  <p className="text-[0.58rem] text-muted-foreground uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
                    Vendor Setup
                  </p>
                  <h1 className="mt-2 font-display text-white text-xl leading-tight sm:text-3xl">
                    Setup Unavailable
                  </h1>
                  <p className="mt-2 max-w-2xl text-muted-foreground text-xs leading-relaxed sm:text-sm">
                    {onboardingConfigError ??
                      'Unable to load vendor configuration. Please try again.'}
                  </p>
                  <div className="mt-6 flex justify-stretch sm:justify-end">
                    <button
                      type="button"
                      onClick={handleRetryOnboardingConfig}
                      className="w-full rounded-full border border-primary/50 bg-primary/10 px-5 py-2.5 font-semibold text-primary text-sm transition hover:bg-primary/15 sm:w-auto sm:py-2"
                    >
                      Retry
                    </button>
                  </div>
                </section>
              ) : canRenderOnboardingSelection && !showOnboardingConfigLoading ? (
                <VendorSetupWizard
                  templates={templates ?? undefined}
                  companies={companies ?? undefined}
                  defaultTemplateId={onboardingConfig?.defaults.templateId ?? industry}
                  defaultCompanyId={onboardingConfig?.defaults.companyId ?? companySelection}
                  onComplete={handleOnboardingComplete}
                />
              ) : null}
            </div>
          </main>
        ) : (
          <>
            <div className="hidden lg:block">
              <Header
                hint={theme.hint}
                templateLabel={templateLabel}
                connectionState={socket.state}
              />
            </div>

            {!aiBannerDismissed && <AiDisclosureBanner onDismiss={handleDismissBanner} />}

            <main className="conversation-stage mt-3 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pb-1 sm:mt-4 sm:pb-0 lg:grid lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] lg:overflow-hidden lg:pb-0">
              <div className="lg:hidden">
                <Header
                  hint={theme.hint}
                  templateLabel={templateLabel}
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
                <div className="col-span-2 rounded-lg border border-border/60 bg-card/60 p-2">
                  <p className="text-muted-foreground">Mic Processing</p>
                  <p className="font-medium text-foreground">
                    profile {noiseCancellationLevel} · software denoiser{' '}
                    {micCaptureDiagnostics?.softwareDenoiserEnabled ? 'on' : 'off'}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    AEC {formatBooleanish(micCaptureDiagnostics?.appliedSettings.echoCancellation)}{' '}
                    · NS {formatBooleanish(micCaptureDiagnostics?.appliedSettings.noiseSuppression)}{' '}
                    · AGC {formatBooleanish(micCaptureDiagnostics?.appliedSettings.autoGainControl)}{' '}
                    · sr {micCaptureDiagnostics?.appliedSettings.sampleRate ?? 'n/a'}Hz · ch{' '}
                    {micCaptureDiagnostics?.appliedSettings.channelCount ?? 'n/a'}
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
