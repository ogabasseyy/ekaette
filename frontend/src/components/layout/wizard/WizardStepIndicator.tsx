import { cva } from 'class-variance-authority'
import { cn } from '../../../lib/utils'
import type { WizardStepStatus } from '../../../types'

const STEP_LABELS = ['Industry', 'Knowledge', 'Connectors', 'Catalog', 'Launch'] as const

const stepDotVariants = cva(
  'flex size-8 items-center justify-center rounded-full text-xs font-medium transition',
  {
    variants: {
      status: {
        pending: 'border border-border/50 bg-card/40 text-muted-foreground',
        active: 'border border-primary/60 bg-primary/15 text-primary',
        completed: 'bg-emerald-500/20 text-emerald-400',
        error: 'bg-destructive/20 text-destructive',
      },
    },
    defaultVariants: { status: 'pending' },
  },
)

interface WizardStepIndicatorProps {
  currentStep: number
  completedSteps: ReadonlySet<number>
  onStepClick?: (step: number) => void
}

function resolveStepStatus(
  index: number,
  currentStep: number,
  completedSteps: ReadonlySet<number>,
): WizardStepStatus {
  if (index === currentStep) return 'active'
  if (completedSteps.has(index)) return 'completed'
  return 'pending'
}

export function WizardStepIndicator({
  currentStep,
  completedSteps,
  onStepClick,
}: WizardStepIndicatorProps) {
  return (
    <nav
      aria-label="Vendor setup steps"
      className="flex items-center justify-center gap-2 sm:gap-3"
    >
      {STEP_LABELS.map((label, index) => {
        const status = resolveStepStatus(index, currentStep, completedSteps)
        const canClick = completedSteps.has(index) && index !== currentStep
        return (
          <div key={label} className="flex items-center gap-2 sm:gap-3">
            <button
              type="button"
              aria-current={status === 'active' ? 'step' : undefined}
              aria-label={`Step ${index + 1}: ${label}`}
              disabled={!canClick}
              onClick={canClick ? () => onStepClick?.(index) : undefined}
              className={cn(
                stepDotVariants({ status }),
                canClick ? 'cursor-pointer' : 'cursor-default',
              )}
            >
              {status === 'completed' ? '✓' : index + 1}
            </button>
            <span
              className={cn(
                'hidden text-xs sm:inline',
                status === 'active' ? 'font-medium text-white' : 'text-muted-foreground',
              )}
            >
              {label}
            </span>
            {index < STEP_LABELS.length - 1 ? (
              <div className="h-px w-4 bg-border/40 sm:w-8" aria-hidden />
            ) : null}
          </div>
        )
      })}
    </nav>
  )
}
