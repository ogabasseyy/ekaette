import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  AnalyticsOverviewResponse,
  AnalyticsSummary,
  CampaignDetailResponse,
  CampaignSnapshot,
} from '../types/analytics'

const POLL_INTERVAL_MS = 30_000

interface UseAnalyticsOptions {
  tenantId: string
  companyId: string
  days?: number
}

interface UseAnalyticsResult {
  summary: AnalyticsSummary | null
  campaigns: CampaignSnapshot[]
  selectedCampaign: CampaignSnapshot | null
  loading: boolean
  error: string | null
  refresh: () => void
  selectCampaign: (campaignId: string) => Promise<void>
  clearSelection: () => void
}

export function useAnalytics({
  tenantId,
  companyId,
  days = 30,
}: UseAnalyticsOptions): UseAnalyticsResult {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null)
  const [campaigns, setCampaigns] = useState<CampaignSnapshot[]>([])
  const [selectedCampaign, setSelectedCampaign] = useState<CampaignSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  const fetchOverview = useCallback(async () => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    try {
      setLoading(true)
      const params = new URLSearchParams({
        tenantId,
        companyId,
        days: String(days),
      })
      const response = await fetch(`/api/v1/at/analytics/overview?${params}`, {
        signal: controller.signal,
      })

      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }

      const data: AnalyticsOverviewResponse = await response.json()
      setSummary(data.summary)
      setCampaigns(data.campaigns ?? [])
      setError(null)
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      setError(err instanceof Error ? err.message : 'Failed to fetch analytics')
      setSummary(null)
      setCampaigns([])
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    }
  }, [tenantId, companyId, days])

  useEffect(() => {
    void fetchOverview()

    const interval = setInterval(() => void fetchOverview(), POLL_INTERVAL_MS)

    return () => {
      clearInterval(interval)
      abortRef.current?.abort()
    }
  }, [fetchOverview])

  const selectCampaign = useCallback(async (campaignId: string) => {
    try {
      const controller = new AbortController()
      const response = await fetch(
        `/api/v1/at/analytics/campaigns/${encodeURIComponent(campaignId)}`,
        { signal: controller.signal },
      )
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }
      const data: CampaignDetailResponse = await response.json()
      setSelectedCampaign(data.campaign ?? null)
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      setSelectedCampaign(null)
      setError(err instanceof Error ? err.message : 'Failed to fetch campaign detail')
    }
  }, [])

  const clearSelection = useCallback(() => {
    setSelectedCampaign(null)
  }, [])

  return {
    summary,
    campaigns,
    selectedCampaign,
    loading,
    error,
    refresh: fetchOverview,
    selectCampaign,
    clearSelection,
  }
}
