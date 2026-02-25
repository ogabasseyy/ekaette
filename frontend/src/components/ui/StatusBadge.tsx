import { cva, type VariantProps } from 'class-variance-authority'
import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'

const statusBadgeVariants = cva(
  'rounded-full border px-3 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.18em]',
  {
    variants: {
      variant: {
        connected: 'border-primary/40 bg-primary/15 text-primary',
        muted: 'border-border/80 bg-card/60 text-muted-foreground',
        info: 'border-info/30 bg-info/10 text-info rounded-xl px-3 py-2 text-xs normal-case tracking-normal font-medium',
        error:
          'border-destructive/30 bg-destructive/10 text-destructive rounded-xl px-3 py-2 text-xs normal-case tracking-normal font-medium',
      },
    },
    defaultVariants: {
      variant: 'muted',
    },
  },
)

interface StatusBadgeProps extends VariantProps<typeof statusBadgeVariants> {
  children: ReactNode
  className?: string
}

export function StatusBadge({ children, variant, className }: StatusBadgeProps) {
  return (
    <span className={cn(statusBadgeVariants({ variant }), className)}>
      {children}
    </span>
  )
}
