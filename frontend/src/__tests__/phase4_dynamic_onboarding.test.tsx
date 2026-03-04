/**
 * Phase 4 — Frontend Dynamic Onboarding + Per-Industry Demo Steps.
 *
 * TDD Red: These tests define the target behavior for registry-driven
 * onboarding, per-industry demo mode, and canonical session state.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import type { IndustryTemplateMeta, OnboardingConfigResponse } from '../types'
import { getLastSocket, sendServerMessage } from './test-helpers'

// Pre-warm lazy-loaded wizard step components so React.lazy resolves under fake timers
beforeAll(async () => {
  await import('../components/layout/wizard/StepIndustry')
  await import('../components/layout/wizard/StepKnowledge')
  await import('../components/layout/wizard/StepConnectors')
  await import('../components/layout/wizard/StepCatalog')
  await import('../components/layout/wizard/StepLaunch')
  await import('../components/cards/ValuationCard')
  await import('../components/cards/BookingConfirmationCard')
  await import('../components/cards/ProductCard')
})

// ═══ Test Fixtures ═══

const MOCK_TEMPLATES: IndustryTemplateMeta[] = [
  {
    id: 'electronics',
    label: 'Electronics & Gadgets',
    category: 'retail',
    description: 'Trade-ins, valuation, negotiation, pickup booking.',
    defaultVoice: 'Aoede',
    theme: {
      accent: 'oklch(74% 0.21 158)',
      accentSoft: 'oklch(62% 0.14 172)',
      title: 'Electronics Trade Desk',
      hint: 'Inspect. Value. Negotiate. Book pickup.',
    },
    capabilities: ['catalog_lookup', 'valuation_tradein', 'booking_reservations'],
    status: 'active',
  },
  {
    id: 'hotel',
    label: 'Hospitality & Hotels',
    category: 'hospitality',
    description: 'Room search, reservations, and guest support.',
    defaultVoice: 'Puck',
    theme: {
      accent: 'oklch(78% 0.15 55)',
      accentSoft: 'oklch(70% 0.12 75)',
      title: 'Hospitality Concierge',
      hint: 'Real-time booking and guest support voice assistant.',
    },
    capabilities: ['booking_reservations', 'policy_qa'],
    status: 'active',
  },
  {
    id: 'telecom',
    label: 'Telecom & Mobile',
    category: 'telecom',
    description: 'Plan inquiry, comparison, and resolution.',
    defaultVoice: 'Charon',
    theme: {
      accent: 'oklch(70% 0.18 280)',
      accentSoft: 'oklch(62% 0.14 290)',
      title: 'Telecom Support Center',
      hint: 'Plan inquiries, comparisons, and resolution support.',
    },
    capabilities: ['policy_qa', 'connector_dispatch'],
    status: 'active',
  },
]

const MOCK_ONBOARDING_CONFIG: OnboardingConfigResponse = {
  tenantId: 'public',
  templates: MOCK_TEMPLATES,
  companies: [
    { id: 'ekaette-electronics', templateId: 'electronics', displayName: 'Ekaette Devices Hub' },
    { id: 'ekaette-hotel', templateId: 'hotel', displayName: 'Ekaette Suites' },
    { id: 'ekaette-telecom', templateId: 'telecom', displayName: 'Ekaette Telecom' },
  ],
  defaults: { templateId: 'electronics', companyId: 'ekaette-electronics' },
}

// ═══ Type Tests ═══

describe('OnboardingConfigResponse types', () => {
  it('IndustryTemplateMeta has required fields', () => {
    const template: IndustryTemplateMeta = MOCK_TEMPLATES[0]
    expect(template.id).toBe('electronics')
    expect(template.label).toBe('Electronics & Gadgets')
    expect(template.category).toBe('retail')
    expect(template.description).toContain('Trade-ins')
    expect(template.defaultVoice).toBe('Aoede')
    expect(template.theme.accent).toContain('oklch')
    expect(template.theme.title).toBe('Electronics Trade Desk')
    expect(template.capabilities).toContain('catalog_lookup')
    expect(template.status).toBe('active')
  })

  it('OnboardingConfigResponse has tenantId, templates, companies, defaults', () => {
    const config: OnboardingConfigResponse = MOCK_ONBOARDING_CONFIG
    expect(config.tenantId).toBe('public')
    expect(config.templates).toHaveLength(3)
    expect(config.companies).toHaveLength(3)
    expect(config.defaults.templateId).toBe('electronics')
    expect(config.defaults.companyId).toBe('ekaette-electronics')
  })
})

// ═══ Per-Industry Demo Steps ═══

describe('Per-industry demo steps (mockData)', () => {
  it('exports DEMO_STEPS_BY_TEMPLATE mapping', async () => {
    const { DEMO_STEPS_BY_TEMPLATE } = await import('../utils/mockData')
    expect(DEMO_STEPS_BY_TEMPLATE).toBeDefined()
    expect(typeof DEMO_STEPS_BY_TEMPLATE).toBe('object')
  })

  it('has demo steps for electronics, hotel, automotive, fashion, telecom, aviation', async () => {
    const { DEMO_STEPS_BY_TEMPLATE } = await import('../utils/mockData')
    expect(DEMO_STEPS_BY_TEMPLATE.electronics).toBeDefined()
    expect(DEMO_STEPS_BY_TEMPLATE.hotel).toBeDefined()
    expect(DEMO_STEPS_BY_TEMPLATE.automotive).toBeDefined()
    expect(DEMO_STEPS_BY_TEMPLATE.fashion).toBeDefined()
    expect(DEMO_STEPS_BY_TEMPLATE.telecom).toBeDefined()
    expect(DEMO_STEPS_BY_TEMPLATE['aviation-support']).toBeDefined()
  })

  it('hotel demo includes booking_confirmation step', async () => {
    const { DEMO_STEPS_BY_TEMPLATE } = await import('../utils/mockData')
    const types = DEMO_STEPS_BY_TEMPLATE.hotel.map(s => s.message.type)
    expect(types).toContain('booking_confirmation')
  })

  it('automotive demo includes agent_transfer step', async () => {
    const { DEMO_STEPS_BY_TEMPLATE } = await import('../utils/mockData')
    const types = DEMO_STEPS_BY_TEMPLATE.automotive.map(s => s.message.type)
    expect(types).toContain('agent_transfer')
  })

  it('fashion demo includes product_recommendation step', async () => {
    const { DEMO_STEPS_BY_TEMPLATE } = await import('../utils/mockData')
    const types = DEMO_STEPS_BY_TEMPLATE.fashion.map(s => s.message.type)
    expect(types).toContain('product_recommendation')
  })

  it('each demo starts with session_started including industryTemplateId', async () => {
    const { DEMO_STEPS_BY_TEMPLATE, validateDemoSteps } = await import('../utils/mockData')
    const canonicalTemplateIds: Record<string, string> = { aviation: 'aviation-support' }
    for (const [templateId, steps] of Object.entries(DEMO_STEPS_BY_TEMPLATE)) {
      expect(steps.length).toBeGreaterThanOrEqual(3)
      expect(validateDemoSteps(steps)).toBe(true)
      const first = steps[0]
      expect(first.message.type).toBe('session_started')
      const msg = first.message as unknown as Record<string, unknown>
      // industry is the broad category, industryTemplateId is the specific key
      expect(msg.industry).toBeDefined()
      expect(msg.industryTemplateId).toBe(canonicalTemplateIds[templateId] ?? templateId)
      expect(msg.tenantId).toBe('public')
    }
  })

  it('ELECTRONICS_DEMO_STEPS is still exported and unchanged (10 steps)', async () => {
    const { ELECTRONICS_DEMO_STEPS } = await import('../utils/mockData')
    expect(ELECTRONICS_DEMO_STEPS).toHaveLength(10)
  })
})

// ═══ useDemoMode with industryTemplateId ═══

describe('useDemoMode with industryTemplateId', () => {
  it('selects hotel steps when industryTemplateId is hotel', async () => {
    const { renderHook, act: hookAct } = await import('@testing-library/react')
    const { useDemoMode } = await import('../hooks/useDemoMode')
    const { DEMO_STEPS_BY_TEMPLATE } = await import('../utils/mockData')
    const emitted: Array<Record<string, unknown>> = []
    const firstDelay = DEMO_STEPS_BY_TEMPLATE.hotel[0]?.delayMs ?? 0

    const { result } = renderHook(() =>
      useDemoMode({
        industryTemplateId: 'hotel',
        onEmit: message => emitted.push(message as unknown as Record<string, unknown>),
      }),
    )
    vi.useFakeTimers()

    try {
      hookAct(() => {
        result.current.play()
      })

      hookAct(() => {
        vi.advanceTimersByTime(firstDelay)
      })

      expect(result.current.isPlaying).toBe(true)
      expect(emitted[0]).toMatchObject({ type: 'session_started', industryTemplateId: 'hotel' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('falls back to generic support demo for unknown template', async () => {
    const { renderHook, act: hookAct } = await import('@testing-library/react')
    const { useDemoMode } = await import('../hooks/useDemoMode')
    const { GENERIC_SUPPORT_DEMO_STEPS } = await import('../utils/mockData')
    const emitted: Array<Record<string, unknown>> = []
    const firstDelay = GENERIC_SUPPORT_DEMO_STEPS[0]?.delayMs ?? 0

    const { result } = renderHook(() =>
      useDemoMode({
        industryTemplateId: 'unknown-industry-xyz',
        onEmit: message => emitted.push(message as unknown as Record<string, unknown>),
      }),
    )
    vi.useFakeTimers()

    try {
      hookAct(() => {
        result.current.play()
      })

      hookAct(() => {
        vi.advanceTimersByTime(firstDelay)
      })

      expect(result.current.isPlaying).toBe(true)
      expect(emitted[0]).toMatchObject({
        type: 'session_started',
        industryTemplateId: 'generic-support',
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('defaults to electronics when no industryTemplateId given', async () => {
    const { renderHook } = await import('@testing-library/react')
    const { useDemoMode } = await import('../hooks/useDemoMode')

    const { result } = renderHook(() => useDemoMode())
    // Default behavior should remain electronics
    expect(result.current.messages).toHaveLength(0)
    expect(result.current.isPlaying).toBe(false)
  })
})

// ═══ IndustryOnboarding with dynamic templates ═══

describe('IndustryOnboarding dynamic templates', () => {
  it('renders template options from props instead of hardcoded list', async () => {
    const { IndustryOnboarding } = await import('../components/layout/IndustryOnboarding')
    render(
      <IndustryOnboarding
        templates={MOCK_TEMPLATES}
        companies={MOCK_ONBOARDING_CONFIG.companies}
        onComplete={() => {}}
      />,
    )

    // All 3 templates from props should render
    expect(screen.getByRole('radio', { name: /electronics/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /hospitality/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /telecom/i })).toBeInTheDocument()
  })

  it('calls onComplete with template id (not legacy industry string)', async () => {
    const { IndustryOnboarding } = await import('../components/layout/IndustryOnboarding')
    const onComplete = vi.fn()
    const user = userEvent.setup()

    render(
      <IndustryOnboarding
        templates={MOCK_TEMPLATES}
        companies={MOCK_ONBOARDING_CONFIG.companies}
        onComplete={onComplete}
      />,
    )

    await user.click(screen.getByRole('radio', { name: /telecom/i }))
    await user.selectOptions(screen.getByLabelText(/choose company/i), 'ekaette-telecom')
    await user.click(screen.getByRole('button', { name: /launch live desk/i }))
    expect(onComplete).toHaveBeenCalledWith({
      templateId: 'telecom',
      companyId: 'ekaette-telecom',
    })
  })

  it('defaults to first template when none selected', async () => {
    const { IndustryOnboarding } = await import('../components/layout/IndustryOnboarding')
    const onComplete = vi.fn()
    const user = userEvent.setup()

    render(
      <IndustryOnboarding
        templates={MOCK_TEMPLATES}
        companies={MOCK_ONBOARDING_CONFIG.companies}
        onComplete={onComplete}
      />,
    )

    await user.click(screen.getByRole('button', { name: /launch live desk/i }))
    expect(onComplete).toHaveBeenCalledWith({
      templateId: 'electronics',
      companyId: 'ekaette-electronics',
    })
  })
})

// ═══ Header with template label ═══

describe('Header with template label', () => {
  it('displays templateLabel prop instead of hardcoded INDUSTRY_LABELS', async () => {
    const { Header } = await import('../components/layout/Header')
    render(
      <Header hint="Test hint" templateLabel="Telecom & Mobile" connectionState="disconnected" />,
    )

    expect(screen.getByText(/Telecom & Mobile/i)).toBeInTheDocument()
  })
})

// ═══ SessionStartedMessage canonical fields ═══

describe('SessionStartedMessage canonical fields', () => {
  it('type allows optional canonical fields', async () => {
    const { isServerMessage } = await import('../utils/mockData')

    // session_started with canonical fields should still be valid
    expect(
      isServerMessage({
        type: 'session_started',
        sessionId: 'test-123',
        industry: 'electronics',
        tenantId: 'public',
        industryTemplateId: 'electronics',
        capabilities: ['catalog_lookup', 'valuation_tradein'],
        registryVersion: '2026-02-26T00:00:00Z',
      }),
    ).toBe(true)
  })
})

// ═══ localStorage canonical tuple ═══

describe('localStorage canonical tuple storage', () => {
  const STORAGE_KEY = 'ekaette:onboarding:industry'

  beforeEach(() => {
    window.localStorage.clear()
    window.localStorage.setItem(
      'ekaette:privacy:consent',
      JSON.stringify({ accepted: true, timestamp: '2026-01-01T00:00:00Z', version: '1.0' }),
    )
  })

  afterEach(() => {
    window.localStorage.clear()
  })

  it('stores industryTemplateId as the industry value', () => {
    // Phase 4: localStorage should accept any template ID string, not just legacy Industry union
    window.localStorage.setItem(STORAGE_KEY, 'telecom')
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('telecom')
  })
})

// ═══ App integration (real Phase 4 behavior) ═══

describe('App dynamic onboarding integration', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(globalThis.WebSocket as unknown as { instances?: unknown[] }).instances = []
    ;(globalThis as { __lastMockWebSocket?: unknown }).__lastMockWebSocket = undefined
    window.localStorage.clear()
    window.localStorage.setItem(
      'ekaette:privacy:consent',
      JSON.stringify({ accepted: true, timestamp: '2026-01-01T00:00:00Z', version: '1.0' }),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  async function flushOnboardingFetch() {
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })
  }

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

  it('fetches onboarding config and renders backend-provided template options in App', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_ONBOARDING_CONFIG,
    } as Response)

    render(<App />)
    await flushOnboardingFetch()

    expect(screen.getByRole('radio', { name: /telecom & mobile/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /hospitality & hotels/i })).toBeInTheDocument()
  })

  it('falls back to local onboarding options when onboarding config fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'))

    render(<App />)
    await flushOnboardingFetch()

    expect(screen.getByText(/using local configuration/i)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /hardware/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /hotel/i })).toBeInTheDocument()
  })

  it('persists canonical onboarding tuple and uses selected company in websocket URL', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_ONBOARDING_CONFIG,
    } as Response)

    render(<App />)
    await flushOnboardingFetch()

    // Step 1 (Industry): select telecom + company, click "Next"
    await act(async () => {
      screen.getByRole('radio', { name: /telecom & mobile/i }).click()
      fireEvent.change(screen.getByLabelText(/company name/i), {
        target: { value: 'ekaette-telecom' },
      })
      screen.getByRole('button', { name: /next/i }).click()
    })

    // Step 2 (Knowledge): skip
    await act(async () => {
      screen.getByRole('button', { name: /skip/i }).click()
    })

    // Step 3 (Connectors): skip
    await act(async () => {
      screen.getByRole('button', { name: /skip/i }).click()
    })

    // Step 4 (Catalog): skip
    await act(async () => {
      screen.getByRole('button', { name: /skip/i }).click()
    })

    // Step 5 (Launch): click "Launch Live Desk"
    await act(async () => {
      screen.getByRole('button', { name: /launch live desk/i }).click()
    })

    expect(window.localStorage.getItem('ekaette:onboarding:templateId')).toBe('telecom')
    expect(window.localStorage.getItem('ekaette:onboarding:companyId')).toBe('ekaette-telecom')
    expect(window.localStorage.getItem('ekaette:onboarding:tenantId')).toBe('public')
    expect(window.localStorage.getItem('ekaette:onboarding:industry')).toBe('telecom') // legacy alias

    await connectCall()
    const ws = getLastSocket()
    expect(ws.url).toContain('industry=telecom')
    expect(ws.url).toContain('company_id=ekaette-telecom')
    expect(ws.url).toContain('tenant_id=public')
  })

  it('reconciles canonical session_started fields into local storage state', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => MOCK_ONBOARDING_CONFIG,
    } as Response)

    window.localStorage.setItem('ekaette:onboarding:industry', 'electronics')
    window.localStorage.setItem('ekaette:onboarding:templateId', 'electronics')
    window.localStorage.setItem('ekaette:onboarding:companyId', 'ekaette-electronics')
    render(<App />)
    await flushOnboardingFetch()
    await connectCall()

    const ws = getLastSocket()
    await act(async () => {
      sendServerMessage(ws, {
        type: 'session_started',
        sessionId: 'session-1',
        industry: 'electronics',
        companyId: 'ekaette-telecom',
        tenantId: 'public',
        industryTemplateId: 'telecom',
        capabilities: ['policy_qa'],
        registryVersion: 'v1-telecom',
      })
    })

    expect(window.localStorage.getItem('ekaette:onboarding:templateId')).toBe('telecom')
    expect(window.localStorage.getItem('ekaette:onboarding:industry')).toBe('telecom')
    expect(window.localStorage.getItem('ekaette:onboarding:companyId')).toBe('ekaette-telecom')
    expect(screen.getAllByText(/Telecom & Mobile/i).length).toBeGreaterThan(0)
  })
})
