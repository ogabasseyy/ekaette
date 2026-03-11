import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { VoiceAnalyticsOverviewResponse } from '../../types/analytics'
import { useVoiceAnalytics } from '../useVoiceAnalytics'

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
      updated_at: '2026-03-11T12:02:00+00:00',
      ended_at: '2026-03-11T12:02:00+00:00',
      duration_seconds: 120,
      transfer_count: 1,
      callback_requested: false,
      callback_triggered: false,
      transcript_messages_total: 6,
      transcript_preview: 'Customer: I want an iPhone 14.',
      agent_path: ['ekaette_router', 'catalog_agent'],
    },
  ],
}

describe('useVoiceAnalytics', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches voice analytics overview', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_VOICE_OVERVIEW),
    })
    global.fetch = fetchMock as unknown as typeof fetch

    const { result } = renderHook(() =>
      useVoiceAnalytics({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(fetchMock).toHaveBeenCalled()
    expect(result.current.summary?.calls_total).toBe(12)
    expect(result.current.recentCalls).toHaveLength(1)
  })
})
