import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AnalyticsOverviewResponse, CampaignSnapshot } from '../../types/analytics'
import { useAnalytics } from '../useAnalytics'

const MOCK_SUMMARY = {
  window_days: 30,
  campaigns_total: 2,
  total_sent: 100,
  total_delivered: 80,
  total_failed: 20,
  total_replies: 10,
  total_conversions: 5,
  total_revenue_kobo: 50000,
  total_revenue_naira: 500,
  delivery_rate: 0.8,
  engagement_rate: 0.125,
  conversion_rate: 0.0625,
}

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
  summary: MOCK_SUMMARY,
  campaigns: [MOCK_CAMPAIGN],
}

function mockFetchOk(data: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(data),
  })
}

describe('useAnalytics', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('starts in loading state', () => {
    global.fetch = mockFetchOk(MOCK_OVERVIEW)
    const { result } = renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )
    expect(result.current.loading).toBe(true)
  })

  it('fetches overview on mount with correct query params', async () => {
    const fetchMock = mockFetchOk(MOCK_OVERVIEW)
    global.fetch = fetchMock

    renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics', days: 30 }),
    )

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/api/v1/at/analytics/overview')
    expect(url).toContain('tenantId=public')
    expect(url).toContain('companyId=ekaette-electronics')
    expect(url).toContain('days=30')
  })

  it('parses summary and campaigns from response', async () => {
    global.fetch = mockFetchOk(MOCK_OVERVIEW)

    const { result } = renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.summary).toEqual(MOCK_SUMMARY)
    expect(result.current.campaigns).toEqual([MOCK_CAMPAIGN])
    expect(result.current.error).toBeNull()
  })

  it('handles fetch errors gracefully', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
    })

    const { result } = renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBeTruthy()
    expect(result.current.summary).toBeNull()
  })

  it('handles network errors', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Network error')
  })

  it('selectCampaign fetches detail and stores it', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(MOCK_OVERVIEW) })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'ok', campaign: MOCK_CAMPAIGN }),
      })

    global.fetch = fetchMock

    const { result } = renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    await act(async () => {
      await result.current.selectCampaign('cmp-sms-001')
    })

    expect(result.current.selectedCampaign).toEqual(MOCK_CAMPAIGN)

    const detailUrl = fetchMock.mock.calls[1][0] as string
    expect(detailUrl).toContain('/api/v1/at/analytics/campaigns/cmp-sms-001')
  })

  it('refresh refetches overview', async () => {
    const fetchMock = mockFetchOk(MOCK_OVERVIEW)
    global.fetch = fetchMock

    const { result } = renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    const callCountAfterMount = fetchMock.mock.calls.length

    await act(async () => {
      result.current.refresh()
    })

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callCountAfterMount)
    })
  })

  it('auto-polls at 30s interval', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    const fetchMock = mockFetchOk(MOCK_OVERVIEW)
    global.fetch = fetchMock

    const { result } = renderHook(() =>
      useAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    const callCountAfterMount = fetchMock.mock.calls.length

    act(() => {
      vi.advanceTimersByTime(30_000)
    })

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callCountAfterMount)
    })
  })
})
