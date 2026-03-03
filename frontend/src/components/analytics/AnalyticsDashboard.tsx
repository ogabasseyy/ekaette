import { useState } from 'react'
import { useAnalytics } from '../../hooks/useAnalytics'
import { cn } from '../../lib/utils'
import { NavBar } from '../layout/NavBar'
import { CampaignDetail } from './CampaignDetail'
import { CampaignTable } from './CampaignTable'
import { KpiCards } from './KpiCards'

const DAYS_OPTIONS = [7, 30, 90] as const

export function AnalyticsDashboard() {
  const [tenantId] = useState('public')
  const [companyId] = useState('ekaette-electronics')
  const [days, setDays] = useState<number>(30)

  const { summary, campaigns, selectedCampaign, loading, error, selectCampaign, clearSelection } =
    useAnalytics({ tenantId, companyId, days })

  return (
    <main className="app-shell min-h-screen">
      <NavBar activePage="analytics" />

      <div className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6">
        {/* Header */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[0.65rem] text-primary uppercase tracking-[0.25em]">Analytics</p>
            <h1 className="font-display text-2xl text-foreground sm:text-3xl">
              Campaign Analytics
            </h1>
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
