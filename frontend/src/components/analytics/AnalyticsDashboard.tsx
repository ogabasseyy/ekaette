import { useEffect, useState } from 'react'
import { useAnalytics } from '../../hooks/useAnalytics'
import { useVoiceAnalytics } from '../../hooks/useVoiceAnalytics'
import { cn } from '../../lib/utils'
import { NavBar } from '../layout/NavBar'
import { CampaignDetail } from './CampaignDetail'
import { CampaignTable } from './CampaignTable'
import { KpiCards } from './KpiCards'
import { VoiceOperationsSection } from './VoiceOperationsSection'

const DAYS_OPTIONS = [7, 30, 90] as const
const TENANT_STORAGE_KEY = 'ekaette:onboarding:tenantId'
const COMPANY_STORAGE_KEY = 'ekaette:onboarding:companyId'

function readStoredValue(key: string): string | null {
  if (typeof window === 'undefined') return null
  const value = window.localStorage.getItem(key)
  if (!value || !value.trim()) return null
  return value.trim()
}

export function AnalyticsDashboard() {
  const [tenantId, setTenantId] = useState(
    () => readStoredValue(TENANT_STORAGE_KEY) ?? String(import.meta.env.VITE_TENANT_ID ?? 'public'),
  )
  const [companyId, setCompanyId] = useState(() => readStoredValue(COMPANY_STORAGE_KEY) ?? '')
  const [days, setDays] = useState<number>(30)

  useEffect(() => {
    let cancelled = false

    async function hydrateIdentity() {
      try {
        const response = await fetch(
          `/api/onboarding/config?tenantId=${encodeURIComponent(tenantId)}`,
          {
            headers: { Accept: 'application/json' },
          },
        )
        if (!response.ok) return
        const payload = (await response.json()) as {
          tenantId?: string
          defaults?: { companyId?: string }
        }
        if (cancelled) return

        const nextTenant =
          typeof payload.tenantId === 'string' && payload.tenantId.trim()
            ? payload.tenantId.trim()
            : tenantId
        const nextCompany =
          typeof payload.defaults?.companyId === 'string' && payload.defaults.companyId.trim()
            ? payload.defaults.companyId.trim()
            : companyId

        if (nextTenant !== tenantId) {
          setTenantId(nextTenant)
          if (typeof window !== 'undefined') {
            window.localStorage.setItem(TENANT_STORAGE_KEY, nextTenant)
          }
        }
        if (nextCompany && nextCompany !== companyId) {
          setCompanyId(nextCompany)
          if (typeof window !== 'undefined') {
            window.localStorage.setItem(COMPANY_STORAGE_KEY, nextCompany)
          }
        }
      } catch {
        // Keep stored/runtime fallback identity when config cannot be loaded.
      }
    }

    void hydrateIdentity()
    return () => {
      cancelled = true
    }
  }, [companyId, tenantId])

  const { summary, campaigns, selectedCampaign, loading, error, selectCampaign, clearSelection } =
    useAnalytics({ tenantId, companyId, days })
  const {
    summary: voiceSummary,
    recentCalls,
    loading: voiceLoading,
    error: voiceError,
  } = useVoiceAnalytics({ tenantId, companyId, days })

  return (
    <main className="app-shell min-h-screen">
      <NavBar activePage="analytics" />

      <div className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6">
        {/* Header */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[0.65rem] text-primary uppercase tracking-[0.25em]">Analytics</p>
            <h1 className="font-display text-2xl text-foreground sm:text-3xl">Operations Dashboard</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Track live voice performance, campaigns, and conversion activity in one view.
            </p>
          </div>

          {/* Days filter pills */}
          <div className="flex gap-1.5">
            {DAYS_OPTIONS.map(d => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={cn(
                  'rounded-full border px-3 py-1 font-semibold text-[0.65rem] uppercase tracking-[0.15em] transition-colors',
                  d === days
                    ? 'border-primary/40 bg-primary/15 text-primary'
                    : 'border-border/60 bg-card/30 text-muted-foreground hover:text-foreground',
                )}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>

        {/* Loading state */}
        {loading && !summary && (
          <div className="panel-glass py-12 text-center text-muted-foreground">
            Loading analytics…
          </div>
        )}

        {/* Error state */}
        {error && !summary && (
          <div className="panel-glass border-destructive/30 py-8 text-center text-destructive">
            {error}
          </div>
        )}

        <VoiceOperationsSection
          summary={voiceSummary}
          recentCalls={recentCalls}
          loading={voiceLoading}
          error={voiceError}
        />

        <div className="flex flex-col gap-1">
          <p className="text-[0.65rem] font-semibold uppercase tracking-[0.2em] text-primary">
            Campaign Analytics
          </p>
          <h2 className="font-display text-xl text-foreground sm:text-2xl">
            Messaging and conversion performance
          </h2>
        </div>

        {/* KPI cards */}
        {summary && <KpiCards summary={summary} />}

        {/* Campaign table */}
        {!loading && (
          <CampaignTable
            campaigns={campaigns}
            selectedId={selectedCampaign?.campaign_id}
            onSelect={selectCampaign}
          />
        )}

        {/* Campaign detail slide-up */}
        {selectedCampaign && (
          <CampaignDetail campaign={selectedCampaign} onClose={clearSelection} />
        )}
      </div>
    </main>
  )
}
