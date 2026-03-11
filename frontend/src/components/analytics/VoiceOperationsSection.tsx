import {
  formatCompactNumber,
  formatDuration,
  formatPercent,
  prettyAgentName,
} from '../../lib/format'
import { cn } from '../../lib/utils'
import type { VoiceAnalyticsSummary, VoiceCallSnapshot } from '../../types/analytics'
import { RateBar } from './RateBar'

interface VoiceOperationsSectionProps {
  summary: VoiceAnalyticsSummary | null
  recentCalls: VoiceCallSnapshot[]
  loading?: boolean
  error?: string | null
  className?: string
}

export function VoiceOperationsSection({
  summary,
  recentCalls,
  loading = false,
  error = null,
  className,
}: VoiceOperationsSectionProps) {
  const cards: Array<{
    label: string
    value: string
    rate?: number
    colorClass?: string
  }> = summary
    ? [
        {
          label: 'Calls',
          value: formatCompactNumber(summary.calls_total),
        },
        {
          label: 'Completed',
          value: formatCompactNumber(summary.calls_completed),
          rate: summary.calls_total > 0 ? summary.calls_completed / summary.calls_total : 0,
          colorClass: 'bg-primary',
        },
        {
          label: 'Avg Duration',
          value: formatDuration(summary.avg_duration_seconds),
        },
        {
          label: 'Transcript Coverage',
          value: formatPercent(summary.transcript_coverage_rate),
          rate: summary.transcript_coverage_rate,
          colorClass: 'bg-accent',
        },
        {
          label: 'Transfers',
          value: formatCompactNumber(summary.transfers_total),
          rate: summary.transfer_rate,
          colorClass: 'bg-warning',
        },
        {
          label: 'Callbacks Triggered',
          value: formatCompactNumber(summary.callback_triggered_total),
        },
      ]
    : []

  return (
    <section className={cn('panel-glass flex flex-col gap-5 p-5', className)}>
      <div className="flex flex-col gap-1">
        <p className="font-semibold text-[0.65rem] text-accent uppercase tracking-[0.2em]">
          Voice Analytics
        </p>
        <h2 className="font-display text-foreground text-xl sm:text-2xl">Voice Operations</h2>
      </div>

      {error && !summary ? (
        <div className="rounded-2xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-destructive text-sm">
          {error}
        </div>
      ) : null}

      {summary ? (
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
          {cards.map(card => (
            <div key={card.label} className="rounded-2xl border border-border/40 bg-card/30 p-4">
              <span className="font-semibold text-[0.65rem] text-muted-foreground uppercase tracking-[0.18em]">
                {card.label}
              </span>
              <div className="mt-2 flex flex-col gap-2">
                <span className="font-display text-2xl text-foreground">{card.value}</span>
                {card.rate !== undefined ? (
                  <RateBar rate={card.rate} colorClass={card.colorClass} />
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : loading ? (
        <div className="rounded-2xl border border-border/40 bg-card/20 px-4 py-6 text-center text-muted-foreground text-sm">
          Loading voice analytics…
        </div>
      ) : (
        <div className="rounded-2xl border border-border/40 bg-card/20 px-4 py-6 text-center text-muted-foreground text-sm">
          No voice activity yet
        </div>
      )}

      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-semibold text-muted-foreground text-sm uppercase tracking-[0.18em]">
            Recent Calls
          </h3>
          {summary ? (
            <span className="text-muted-foreground text-xs tabular-nums">
              {summary.window_days} day window
            </span>
          ) : null}
        </div>

        {loading && recentCalls.length === 0 ? (
          <div className="rounded-2xl border border-border/40 bg-card/20 px-4 py-6 text-center text-muted-foreground text-sm">
            Loading recent calls…
          </div>
        ) : recentCalls.length > 0 ? (
          <div className="grid gap-3">
            {recentCalls.map(call => (
              <article
                key={call.session_id}
                className="rounded-2xl border border-border/40 bg-background/40 p-4"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="space-y-1">
                    <p className="font-medium text-foreground text-sm">{call.transcript_preview}</p>
                    <p className="text-muted-foreground text-xs">
                      {call.agent_path.length > 0
                        ? call.agent_path.map(prettyAgentName).join(' → ')
                        : 'No agent path'}
                    </p>
                  </div>

                  <div className="flex flex-wrap gap-2 text-muted-foreground text-xs">
                    <span className="rounded-full border border-border/40 px-2 py-1">
                      {call.status}
                    </span>
                    <span className="rounded-full border border-border/40 px-2 py-1">
                      {formatDuration(call.duration_seconds)}
                    </span>
                    <span className="rounded-full border border-border/40 px-2 py-1">
                      {call.transcript_messages_total} msgs
                    </span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-border/50 border-dashed px-4 py-6 text-center text-muted-foreground text-sm">
            No recent calls found
          </div>
        )}
      </div>
    </section>
  )
}
