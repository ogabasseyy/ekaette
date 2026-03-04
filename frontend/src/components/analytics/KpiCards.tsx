import { formatCompactNumber, formatNaira, formatPercent } from '../../lib/format'
import { cn } from '../../lib/utils'
import type { AnalyticsSummary } from '../../types/analytics'
import { RateBar } from './RateBar'

interface KpiCardsProps {
  summary: AnalyticsSummary | null
  className?: string
}

export function KpiCards({ summary, className }: KpiCardsProps) {
  if (!summary) {
    return (
      <div className={cn('panel-glass p-6 text-center text-muted-foreground', className)}>
        No data available
      </div>
    )
  }

  const cards: Array<{
    label: string
    value: string
    rate?: number
    colorClass?: string
  }> = [
    {
      label: 'Campaigns',
      value: String(summary.campaigns_total),
    },
    {
      label: 'Delivery Rate',
      value: formatPercent(summary.delivery_rate),
      rate: summary.delivery_rate,
      colorClass: 'bg-primary',
    },
    {
      label: 'Engagement',
      value: formatPercent(summary.engagement_rate),
      rate: summary.engagement_rate,
      colorClass: 'bg-accent',
    },
    {
      label: 'Conversion',
      value: formatPercent(summary.conversion_rate),
      rate: summary.conversion_rate,
      colorClass: 'bg-warning',
    },
    {
      label: 'Revenue',
      value: formatNaira(summary.total_revenue_naira),
    },
    {
      label: 'Messages Sent',
      value: formatCompactNumber(summary.total_sent),
    },
  ]

  return (
    <div className={cn('grid grid-cols-2 gap-3 lg:grid-cols-3', className)}>
      {cards.map(card => (
        <div key={card.label} className="panel-glass flex flex-col gap-2 p-4">
          <span className="text-[0.65rem] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            {card.label}
          </span>
          <span className="font-display text-2xl text-foreground">{card.value}</span>
          {card.rate !== undefined && <RateBar rate={card.rate} colorClass={card.colorClass} />}
        </div>
      ))}
    </div>
  )
}
