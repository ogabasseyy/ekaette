import { cn } from '../../lib/utils'

interface AgentAvatarProps {
  label: string
  active?: boolean
  className?: string
}

export function AgentAvatar({ label, active = false, className }: AgentAvatarProps) {
  const initials = label
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(part => part[0]?.toUpperCase() ?? '')
    .join('')

  return (
    <div className={cn('inline-flex items-center gap-2', className)}>
      <span
        className={cn(
          'inline-flex h-8 w-8 items-center justify-center rounded-full border text-xs font-semibold uppercase',
          active
            ? 'border-primary/50 bg-primary/20 text-primary'
            : 'border-border/70 bg-card/60 text-muted-foreground',
        )}
      >
        {initials || 'AI'}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  )
}
