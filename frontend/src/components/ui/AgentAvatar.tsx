import { cva } from 'class-variance-authority'
import { cn } from '../../lib/utils'

const avatarVariants = cva(
  'inline-flex h-8 w-8 items-center justify-center rounded-full border font-semibold text-xs uppercase',
  {
    variants: {
      active: {
        true: 'border-primary/50 bg-primary/20 text-primary',
        false: 'border-border/70 bg-card/60 text-muted-foreground',
      },
    },
    defaultVariants: { active: false },
  },
)

interface AgentAvatarProps {
  label: string
  active?: boolean
  className?: string
}

export function AgentAvatar({ label, active = false, className }: AgentAvatarProps) {
  const normalizedLabel = label?.trim() ?? ''
  const initials = normalizedLabel
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(part => part[0]?.toUpperCase() ?? '')
    .join('')

  return (
    <div className={cn('inline-flex items-center gap-2', className)}>
      <span aria-hidden="true" className={cn(avatarVariants({ active }))}>
        {initials || 'AI'}
      </span>
      {normalizedLabel ? (
        <span className="text-muted-foreground text-xs">{normalizedLabel}</span>
      ) : null}
    </div>
  )
}
