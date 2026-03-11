import { useCallback, useEffect, useRef, useState } from 'react'
import type { VoiceAnalyticsOverviewResponse, VoiceAnalyticsSummary, VoiceCallSnapshot } from '../types/analytics'

const POLL_INTERVAL_MS = 30_000

interface UseVoiceAnalyticsOptions {
  tenantId: string
  companyId: string
  days?: number
}

interface UseVoiceAnalyticsResult {
  summary: VoiceAnalyticsSummary | null
  recentCalls: VoiceCallSnapshot[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useVoiceAnalytics({
  tenantId,
  companyId,
  days = 30,
}: UseVoiceAnalyticsOptions): UseVoiceAnalyticsResult {
  const [summary, setSummary] = useState<VoiceAnalyticsSummary | null>(null)
  const [recentCalls, setRecentCalls] = useState<VoiceCallSnapshot[]>([])
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
      const response = await fetch(`/api/v1/at/analytics/voice/overview?${params}`, {
        signal: controller.signal,
      })

      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }

      const data: VoiceAnalyticsOverviewResponse = await response.json()
      setSummary(data.summary)
      setRecentCalls(data.recent_calls ?? [])
      setError(null)
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      setSummary(null)
      setRecentCalls([])
      setError((err as Error).message || 'Failed to fetch voice analytics')
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    }
  }, [tenantId, companyId, days])

  useEffect(() => {
    fetchOverview()
    const interval = setInterval(fetchOverview, POLL_INTERVAL_MS)

    return () => {
      clearInterval(interval)
      abortRef.current?.abort()
    }
  }, [fetchOverview])

  return {
    summary,
    recentCalls,
    loading,
    error,
    refresh: fetchOverview,
  }
}
