import { useState } from 'react'
import { cn } from '../../lib/utils'
import type { Industry } from '../../types'

interface IndustryOnboardingProps {
  onComplete: (industry: Industry) => void
}

const INDUSTRY_OPTIONS: Array<{
  value: Industry
  label: string
  description: string
}> = [
  {
    value: 'electronics',
    label: 'Electronics',
    description: 'Trade-ins, valuation, negotiation, pickup booking.',
  },
  {
    value: 'hotel',
    label: 'Hotel',
    description: 'Reservations, room search, stay assistance workflows.',
  },
  {
    value: 'automotive',
    label: 'Automotive',
    description: 'Service lane support, estimates, and booking.',
  },
  {
    value: 'fashion',
    label: 'Fashion',
    description: 'Catalog assistance and customer styling support.',
  },
]

export function IndustryOnboarding({ onComplete }: IndustryOnboardingProps) {
  const [selected, setSelected] = useState<Industry>('electronics')

  return (
    <section className="panel-glass mx-auto w-full max-w-3xl px-4 py-5 sm:px-7 sm:py-8">
      <p className="text-[0.58rem] text-[color:var(--industry-accent)] uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
        Onboarding
      </p>
      <h1 className="mt-2 font-display text-white text-xl leading-tight sm:text-3xl">
        Choose Your Service Industry
      </h1>
      <p className="mt-2 max-w-2xl text-muted-foreground text-xs leading-relaxed sm:text-sm">
        Your industry profile sets the assistant voice and behavior for live calls. This is locked
        during active conversations.
      </p>

      <div className="mt-5 grid gap-3 sm:mt-6 sm:grid-cols-2">
        {INDUSTRY_OPTIONS.map(option => {
          const active = selected === option.value
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => setSelected(option.value)}
              className={cn(
                'rounded-2xl border px-4 py-4 text-left transition',
                active
                  ? 'border-primary/60 bg-primary/10'
                  : 'border-border/70 bg-card/40 hover:border-primary/40',
              )}
            >
              <p className="font-semibold text-white">{option.label}</p>
              <p className="mt-1 text-muted-foreground text-sm">{option.description}</p>
            </button>
          )
        })}
      </div>

      <div className="mt-6 flex justify-stretch sm:justify-end">
        <button
          type="button"
          onClick={() => onComplete(selected)}
          className="w-full rounded-full bg-[color:var(--industry-accent)] px-5 py-2.5 font-semibold text-black text-sm transition hover:brightness-110 sm:w-auto sm:py-2"
        >
          Continue
        </button>
      </div>
    </section>
  )
}
