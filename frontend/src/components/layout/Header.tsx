import type { ConnectionState } from '../../types'
import { StatusBadge } from '../ui/StatusBadge'

interface HeaderProps {
  hint: string
  templateLabel: string
  connectionState: ConnectionState
}

export function Header({ hint, templateLabel, connectionState }: HeaderProps) {
  const isConnected = connectionState === 'connected'
  const connectionLabel =
    connectionState === 'connected'
      ? 'Connected'
      : connectionState === 'connecting'
        ? 'Connecting'
        : connectionState === 'reconnecting'
          ? 'Reconnecting'
          : 'Disconnected'

  return (
    <header className="panel-glass header-panel flex shrink-0 flex-col gap-3 px-4 py-4 sm:gap-4 sm:px-5 sm:py-5 lg:flex-row lg:items-center lg:justify-between">
      <div className="space-y-1.5">
        <p className="text-[0.58rem] text-muted-foreground uppercase tracking-[0.28em] sm:text-[0.62rem] sm:tracking-[0.34em]">
          Baci Technologies
        </p>
        <h1 className="font-display text-white text-xl tracking-tight sm:text-3xl">
          Ekaette Live Desk
        </h1>
        <p className="text-muted-foreground text-xs leading-relaxed sm:text-sm">{hint}</p>
      </div>

      <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3">
        <StatusBadge
          variant={isConnected ? 'connected' : 'muted'}
          className="text-center sm:text-left"
        >
          {connectionLabel}
        </StatusBadge>
        <StatusBadge variant="muted" className="text-center sm:text-left">
          {templateLabel}
        </StatusBadge>
      </div>
    </header>
  )
}
