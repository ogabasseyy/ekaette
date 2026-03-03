import { formatDuration, prettyAgentName } from '../../lib/format'
import { cn } from '../../lib/utils'
import type { AgentStatusMessage, MemoryRecallMessage, TelemetryMessage } from '../../types'
import { StatusBadge } from '../ui/StatusBadge'

interface VoicePanelProps {
  title: string
  sessionId: string
  elapsedSeconds: number
  isConnected: boolean
  isStarting: boolean
  latestAgentStatus?: AgentStatusMessage
  latestTelemetry?: TelemetryMessage
  latestMemoryRecall?: MemoryRecallMessage
  audioError?: string | null
  callError?: string | null
}

export function VoicePanel({
  title,
  sessionId,
  elapsedSeconds,
  isConnected,
  isStarting,
  latestAgentStatus,
  latestTelemetry,
  latestMemoryRecall,
  audioError,
  callError,
}: VoicePanelProps) {
  return (
    <section className="panel-glass voice-panel flex min-h-0 flex-col justify-between px-4 py-4 sm:px-5 sm:py-5">
      <div>
        <p className="text-[0.58rem] text-[color:var(--industry-accent)] uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
          {title}
        </p>
        <h2 className="mt-2 font-display text-lg text-white sm:text-2xl">Voice Operations</h2>
        <p className="mt-2 max-w-md text-muted-foreground text-xs leading-relaxed sm:text-sm">
          Start a live conversation, upload an image, negotiate value, and move to booking from one
          control surface.
        </p>
      </div>

      <div className="voice-panel__module mt-4 rounded-2xl border border-border/70 bg-black/30 p-3.5 sm:mt-5 sm:p-5">
        <div className="mx-auto flex max-w-xs flex-col items-center gap-4">
          <div className={cn('voice-orb', isConnected && 'is-live', isStarting && 'is-warming')}>
            <span className="voice-core">
              {isConnected ? 'LIVE' : isStarting ? 'SYNC' : 'READY'}
            </span>
          </div>
          <div className="text-center">
            <p className="voice-panel__timer font-semibold text-2xl text-white">
              {formatDuration(elapsedSeconds)}
            </p>
            <p className="text-muted-foreground text-xs uppercase tracking-[0.16em]">Call timer</p>
          </div>
        </div>
      </div>

      <div className="mt-4 space-y-2" aria-live="polite">
        <p className="voice-panel__session rounded-xl border border-border/70 bg-card/50 px-3 py-2 text-[11px] text-muted-foreground leading-5 sm:text-xs">
          Session: <span className="break-all text-foreground">{sessionId}</span>
        </p>
        {latestAgentStatus && (
          <StatusBadge variant="info" className="block break-words">
            Active agent: {prettyAgentName(latestAgentStatus.agent)} ({latestAgentStatus.status})
          </StatusBadge>
        )}
        {latestTelemetry && (
          <StatusBadge variant="muted" className="block break-words">
            Tokens: {latestTelemetry.sessionTotalTokens ?? 0} (cost: $
            {(latestTelemetry.sessionCostUsd ?? 0).toFixed(4)})
          </StatusBadge>
        )}
        {latestMemoryRecall && (
          <StatusBadge variant="info" className="block break-words">
            Context restored
            {latestMemoryRecall.customerName ? ` for ${latestMemoryRecall.customerName}` : ''}:{' '}
            {latestMemoryRecall.previousInteractions} prior interaction
            {latestMemoryRecall.previousInteractions === 1 ? '' : 's'}
          </StatusBadge>
        )}
        {audioError && (
          <StatusBadge variant="error" className="block break-words">
            Audio: {audioError}
          </StatusBadge>
        )}
        {callError && (
          <StatusBadge variant="error" className="block break-words">
            Connection: {callError}
          </StatusBadge>
        )}
      </div>
    </section>
  )
}
