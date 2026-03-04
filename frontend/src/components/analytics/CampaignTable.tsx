import { formatNaira, formatPercent } from '../../lib/format'
import { cn } from '../../lib/utils'
import type { CampaignSnapshot } from '../../types/analytics'
import { RateBar } from './RateBar'

interface CampaignTableProps {
  campaigns: CampaignSnapshot[]
  selectedId?: string
  onSelect: (campaignId: string) => void
  className?: string
}

function channelLabel(channel: string): string {
  return channel.toUpperCase()
}

function channelColorClass(channel: string): string {
  switch (channel) {
    case 'sms':
      return 'border-primary/40 bg-primary/15 text-primary'
    case 'voice':
      return 'border-accent/40 bg-accent/15 text-accent'
    default:
      return 'border-warning/40 bg-warning/15 text-warning'
  }
}

export function CampaignTable({ campaigns, selectedId, onSelect, className }: CampaignTableProps) {
  if (campaigns.length === 0) {
    return (
      <div className={cn('panel-glass p-6 text-center text-muted-foreground', className)}>
        No campaigns yet
      </div>
    )
  }

  return (
    <div className={cn('panel-glass overflow-hidden', className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/40 text-left text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            <th className="px-4 py-3">Campaign</th>
            <th className="px-4 py-3">Channel</th>
            <th className="hidden px-4 py-3 sm:table-cell">Sent</th>
            <th className="px-4 py-3">Delivery</th>
            <th className="hidden px-4 py-3 sm:table-cell">Revenue</th>
          </tr>
        </thead>
        <tbody className="analytics-table-body">
          {campaigns.map(campaign => (
            <tr
              key={campaign.campaign_id}
              className={cn(
                'cursor-pointer border-b border-border/20 transition-colors hover:bg-card/40',
                selectedId === campaign.campaign_id && 'bg-primary/5 border-l-2 border-l-primary',
              )}
              tabIndex={0}
              onClick={() => onSelect(campaign.campaign_id)}
              onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onSelect(campaign.campaign_id)
                }
              }}
            >
              <td className="px-4 py-3 font-medium text-foreground">{campaign.campaign_name}</td>
              <td className="px-4 py-3">
                <span
                  className={cn(
                    'inline-block rounded-full border px-2 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wider',
                    channelColorClass(campaign.channel),
                  )}
                >
                  {channelLabel(campaign.channel)}
                </span>
              </td>
              <td className="hidden px-4 py-3 tabular-nums text-muted-foreground sm:table-cell">
                {campaign.sent_total}
              </td>
              <td className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <RateBar rate={campaign.delivery_rate} className="w-16" />
                  <span className="tabular-nums text-xs text-muted-foreground">
                    {formatPercent(campaign.delivery_rate)}
                  </span>
                </div>
              </td>
              <td className="hidden px-4 py-3 tabular-nums text-muted-foreground sm:table-cell">
                {formatNaira(campaign.revenue_kobo / 100)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
