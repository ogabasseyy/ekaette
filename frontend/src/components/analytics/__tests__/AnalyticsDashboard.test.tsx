import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  AnalyticsOverviewResponse,
  CampaignSnapshot,
  VoiceAnalyticsOverviewResponse,
} from '../../../types/analytics'
import { AnalyticsDashboard } from '../AnalyticsDashboard'

vi.mock('../../../lib/navigation', () => ({
  NAV_ITEMS: [
    { page: 'voice', label: 'Voice', iconName: 'Mic' },
    { page: 'analytics', label: 'Analytics', iconName: 'BarChart3' },
    { page: 'marketing', label: 'Marketing', iconName: 'Megaphone' },
    { page: 'admin', label: 'Admin', iconName: 'Settings' },
  ],
  navigateTo: vi.fn(),
  currentPage: () => 'analytics',
}))

const MOCK_CAMPAIGN: CampaignSnapshot = {
  campaign_id: 'cmp-sms-001',
  channel: 'sms',
  tenant_id: 'public',
  company_id: 'ekaette-electronics',
  campaign_name: 'Weekend Promo',
  message: '5% off this weekend',
  created_at: '2026-02-28T10:00:00+00:00',
  updated_at: '2026-02-28T12:00:00+00:00',
  recipients_total: 50,
  sent_total: 50,
  delivered_total: 40,
  failed_total: 10,
  replies_total: 5,
  conversions_total: 3,
  revenue_kobo: 30000,
  payments_initialized_total: 4,
  payments_success_total: 3,
  delivery_rate: 0.8,
  engagement_rate: 0.125,
  conversion_rate: 0.075,
  avg_order_value_kobo: 10000,
}

const MOCK_OVERVIEW: AnalyticsOverviewResponse = {
  status: 'ok',
  tenant_id: 'public',
  company_id: 'ekaette-electronics',
  summary: {
    window_days: 30,
    campaigns_total: 1,
    total_sent: 50,
    total_delivered: 40,
    total_failed: 10,
    total_replies: 5,
    total_conversions: 3,
    total_revenue_kobo: 30000,
    total_revenue_naira: 300,
    delivery_rate: 0.8,
    engagement_rate: 0.125,
    conversion_rate: 0.075,
  },
  campaigns: [MOCK_CAMPAIGN],
}

const MOCK_VOICE_OVERVIEW: VoiceAnalyticsOverviewResponse = {
  status: 'ok',
  tenant_id: 'public',
  company_id: 'ekaette-electronics',
  summary: {
    window_days: 30,
    calls_total: 12,
    calls_completed: 10,
    avg_duration_seconds: 87.5,
    transfers_total: 4,
    transfer_rate: 0.3333,
    callback_requests_total: 3,
    callback_triggered_total: 2,
    transcript_coverage_rate: 0.75,
  },
  recent_calls: [
    {
      session_id: 'sess-1',
      tenant_id: 'public',
      company_id: 'ekaette-electronics',
      channel: 'voice',
      status: 'completed',
      started_at: '2026-03-11T12:00:00+00:00',
      updated_at: '2026-03-11T12:01:27+00:00',
      ended_at: '2026-03-11T12:01:27+00:00',
      duration_seconds: 87,
      transfer_count: 1,
      callback_requested: false,
      callback_triggered: false,
      transcript_messages_total: 4,
      transcript_preview: 'Customer: I want to buy an iPhone 14.',
      agent_path: ['ekaette_router', 'catalog_agent'],
    },
  ],
}

function mockAnalyticsFetch(options?: { overviewOk?: boolean; detailOk?: boolean }) {
  const overviewOk = options?.overviewOk ?? true
  const detailOk = options?.detailOk ?? true
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (url.includes('/api/onboarding/config')) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            tenantId: 'public',
            defaults: { companyId: 'ekaette-electronics' },
          }),
      } as unknown as Response)
    }
    if (url.includes('/api/v1/at/analytics/overview')) {
      if (!overviewOk) {
        return Promise.resolve({
          ok: false,
          status: 500,
          statusText: 'Server Error',
        } as unknown as Response)
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_OVERVIEW),
      } as unknown as Response)
    }
    if (url.includes('/api/v1/at/analytics/voice/overview')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_VOICE_OVERVIEW),
      } as unknown as Response)
    }
    if (url.includes('/api/v1/at/analytics/campaigns/')) {
      if (!detailOk) {
        return Promise.resolve({
          ok: false,
          status: 500,
          statusText: 'Server Error',
        } as unknown as Response)
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ status: 'ok', campaign: MOCK_CAMPAIGN }),
      } as unknown as Response)
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ status: 'ok' }),
    } as unknown as Response)
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AnalyticsDashboard', () => {
  it('renders the page title', () => {
    global.fetch = mockAnalyticsFetch() as unknown as typeof fetch
    render(<AnalyticsDashboard />)
    expect(screen.getByText('Operations Dashboard')).toBeInTheDocument()
  })

  it('shows loading state while fetching', () => {
    global.fetch = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.includes('/api/onboarding/config')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              tenantId: 'public',
              defaults: { companyId: 'ekaette-electronics' },
            }),
        } as unknown as Response)
      }
      return new Promise<Response>(() => {})
    }) as unknown as typeof fetch
    render(<AnalyticsDashboard />)
    expect(screen.getByText('Loading analytics…')).toBeInTheDocument()
  })

  it('renders KPI cards after data loads', async () => {
    global.fetch = mockAnalyticsFetch() as unknown as typeof fetch
    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText('Campaigns')).toBeInTheDocument()
    })

    expect(screen.getByText('Delivery Rate')).toBeInTheDocument()
    expect(screen.getByText('Messages Sent')).toBeInTheDocument()
  })

  it('renders campaign table with campaign names', async () => {
    global.fetch = mockAnalyticsFetch() as unknown as typeof fetch
    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText('Weekend Promo')).toBeInTheDocument()
    })
  })

  it('renders voice operations metrics and recent calls', async () => {
    global.fetch = mockAnalyticsFetch() as unknown as typeof fetch
    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText('Voice Operations')).toBeInTheDocument()
    })

    expect(screen.getByText('Calls')).toBeInTheDocument()
    expect(screen.getByText('Transcript Coverage')).toBeInTheDocument()
    expect(screen.getByText('Customer: I want to buy an iPhone 14.')).toBeInTheDocument()
  })

  it('clicking a campaign row shows campaign detail', async () => {
    const user = userEvent.setup()
    global.fetch = mockAnalyticsFetch() as unknown as typeof fetch

    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText('Weekend Promo')).toBeInTheDocument()
    })

    const row = screen.getByText('Weekend Promo').closest('tr')!
    await user.click(row)

    await waitFor(() => {
      expect(screen.getByText('5% off this weekend')).toBeInTheDocument()
    })
  })

  it('shows error state on fetch failure', async () => {
    global.fetch = mockAnalyticsFetch({ overviewOk: false }) as unknown as typeof fetch
    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText(/500/)).toBeInTheDocument()
    })
  })
})
