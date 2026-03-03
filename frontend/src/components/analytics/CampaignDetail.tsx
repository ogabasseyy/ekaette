import { X } from 'lucide-react'
import { formatNaira, formatPercent } from '../../lib/format'
import { cn } from '../../lib/utils'
import type { CampaignSnapshot } from '../../types/analytics'
import { RateBar } from './RateBar'

interface CampaignDetailProps {
  campaign: CampaignSnapshot
  onClose: () => void
  className?: string
}

interface MetricRow {
  label: string
  value: string | number
  rate?: number
  colorClass?: string
}

export function CampaignDetail({ campaign, onClose, className }: CampaignDetailProps) {
  const metrics: MetricRow[] = [
    { label: 'Sent', value: campaign.sent_total },
    {
      label: 'Delivered',
      value: campaign.delivered_total,
      rate: campaign.delivery_rate,
      colorClass: 'bg-primary',
    },
    {
      label: 'Failed',
      value: campaign.failed_total,
      rate: campaign.sent_total > 0 ? campaign.failed_total / campaign.sent_total : 0,
      colorClass: 'bg-destructive',
    },
    { label: 'Replies', value: campaign.replies_total },
    {
      label: 'Conversions',
      value: campaign.conversions_total,
      rate: campaign.conversion_rate,
      colorClass: 'bg-warning',
    },
    { label: 'Revenue', value: formatNaira(campaign.revenue_kobo / 100) },
    { label: 'Payments Init', value: campaign.payments_initialized_total },
    { label: 'Payments OK', value: campaign.payments_success_total },
  ]

  return (
    <div className={cn('campaign-detail-panel panel-glass p-5', className)}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-foreground text-xl">{campaign.campaign_name}</h3>
          <span className="font-semibold text-[0.62rem] text-muted-foreground uppercase tracking-[0.18em]">
            {campaign.channel} &middot; {campaign.campaign_id}
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-card/60 hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>

      {campaign.message && (
        <div className="mb-4 rounded-xl border border-border/30 bg-card/30 px-4 py-3 text-foreground/80 text-sm">
          {campaign.message}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {metrics.map(metric => (
          <div key={metric.label} className="flex flex-col gap-1">
            <span className="font-semibold text-[0.6rem] text-muted-foreground uppercase tracking-[0.18em]">
              {metric.label}
            </span>
            <span className="font-display text-foreground text-lg tabular-nums">
              {metric.value}
            </span>
            {metric.rate !== undefined && (
              <RateBar rate={metric.rate} colorClass={metric.colorClass} />
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 flex gap-4 text-muted-foreground text-xs">
        <span>Delivery: {formatPercent(campaign.delivery_rate)}</span>
        <span>Engagement: {formatPercent(campaign.engagement_rate)}</span>
        <span>Conversion: {formatPercent(campaign.conversion_rate)}</span>
      </div>
    </div>
  )
}
