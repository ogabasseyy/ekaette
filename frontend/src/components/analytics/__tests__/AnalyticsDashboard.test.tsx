import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AnalyticsOverviewResponse, CampaignSnapshot } from '../../../types/analytics'
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

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AnalyticsDashboard', () => {
  it('renders the page title', () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve(MOCK_OVERVIEW) })
    render(<AnalyticsDashboard />)
    expect(screen.getByText('Campaign Analytics')).toBeInTheDocument()
  })

  it('shows loading state while fetching', () => {
    global.fetch = vi.fn().mockReturnValue(new Promise(() => {}))
    render(<AnalyticsDashboard />)
    expect(screen.getByText('Loading analytics…')).toBeInTheDocument()
  })

  it('renders KPI cards after data loads', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve(MOCK_OVERVIEW) })
    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText('Campaigns')).toBeInTheDocument()
    })

    expect(screen.getByText('Delivery Rate')).toBeInTheDocument()
    expect(screen.getByText('Messages Sent')).toBeInTheDocument()
  })

  it('renders campaign table with campaign names', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve(MOCK_OVERVIEW) })
    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText('Weekend Promo')).toBeInTheDocument()
    })
  })

  it('clicking a campaign row shows campaign detail', async () => {
    const user = userEvent.setup()
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(MOCK_OVERVIEW) })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'ok', campaign: MOCK_CAMPAIGN }),
      })

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
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: 'Server Error' })
    render(<AnalyticsDashboard />)

    await waitFor(() => {
      expect(screen.getByText(/500/)).toBeInTheDocument()
    })
  })
})
