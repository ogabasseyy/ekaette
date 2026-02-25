import type { ConnectionState, Industry } from '../../types'
import { StatusBadge } from '../ui/StatusBadge'

interface HeaderProps {
  hint: string
  industry: Industry
  connectionState: ConnectionState
}

const INDUSTRY_LABELS: Record<Industry, string> = {
  electronics: 'Electronics',
  hotel: 'Hotel',
  automotive: 'Automotive',
  fashion: 'Fashion',
}

export function Header({ hint, industry, connectionState }: HeaderProps) {
  const isConnected = connectionState === 'connected'

  return (
    <header className="panel-glass flex shrink-0 flex-col gap-3 px-4 py-4 sm:gap-4 sm:px-5 sm:py-5 lg:flex-row lg:items-center lg:justify-between">
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
          {connectionState}
        </StatusBadge>
        <StatusBadge variant="muted" className="text-center sm:text-left">
          Industry: {INDUSTRY_LABELS[industry]}
        </StatusBadge>
        <p className="px-1 text-[0.68rem] text-muted-foreground sm:basis-full sm:pt-0.5">
          Onboarding is locked during active calls.
        </p>
      </div>
    </header>
  )
}
