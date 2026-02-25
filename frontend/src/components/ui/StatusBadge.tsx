import { cva, type VariantProps } from 'class-variance-authority'
import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'

const statusBadgeVariants = cva(
  'rounded-full border px-3 py-1 font-semibold text-[0.68rem] uppercase tracking-[0.18em]',
  {
    variants: {
      variant: {
        connected: 'border-primary/40 bg-primary/15 text-primary',
        muted: 'border-border/80 bg-card/60 text-muted-foreground',
        info: 'rounded-xl border-info/30 bg-info/10 px-3 py-2 font-medium text-info text-xs normal-case tracking-normal',
        error:
          'rounded-xl border-destructive/30 bg-destructive/10 px-3 py-2 font-medium text-destructive text-xs normal-case tracking-normal',
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
  return <span className={cn(statusBadgeVariants({ variant }), className)}>{children}</span>
}
