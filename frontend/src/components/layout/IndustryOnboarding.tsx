import { useState } from 'react'
import { cn } from '../../lib/utils'
import type { IndustryTemplateMeta } from '../../types'

/** Hardcoded fallback used when no templates prop is provided (legacy compat). */
const FALLBACK_OPTIONS: IndustryTemplateMeta[] = [
  {
    id: 'electronics',
    label: 'Electronics',
    category: 'retail',
    description: 'Trade-ins, valuation, negotiation, pickup booking.',
    defaultVoice: 'Aoede',
    theme: {
      accent: 'oklch(74% 0.21 158)',
      accentSoft: 'oklch(62% 0.14 172)',
      title: 'Electronics Trade Desk',
      hint: 'Inspect. Value. Negotiate. Book pickup.',
    },
    capabilities: [],
    status: 'active',
  },
  {
    id: 'hotel',
    label: 'Hotel',
    category: 'hospitality',
    description: 'Reservations, room search, stay assistance workflows.',
    defaultVoice: 'Puck',
    theme: {
      accent: 'oklch(78% 0.15 55)',
      accentSoft: 'oklch(70% 0.12 75)',
      title: 'Hospitality Concierge',
      hint: 'Real-time booking and guest support voice assistant.',
    },
    capabilities: [],
    status: 'active',
  },
  {
    id: 'automotive',
    label: 'Automotive',
    category: 'automotive',
    description: 'Service lane support, estimates, and booking.',
    defaultVoice: 'Kore',
    theme: {
      accent: 'oklch(71% 0.18 240)',
      accentSoft: 'oklch(63% 0.15 260)',
      title: 'Automotive Service Lane',
      hint: 'Trade-ins, inspections, parts and service scheduling.',
    },
    capabilities: [],
    status: 'active',
  },
  {
    id: 'fashion',
    label: 'Fashion',
    category: 'retail',
    description: 'Catalog assistance and customer styling support.',
    defaultVoice: 'Aoede',
    theme: {
      accent: 'oklch(74% 0.2 20)',
      accentSoft: 'oklch(66% 0.16 345)',
      title: 'Fashion Client Studio',
      hint: 'Catalog recommendations and consultation workflows.',
    },
    capabilities: [],
    status: 'active',
  },
]

interface IndustryOnboardingProps {
  templates?: IndustryTemplateMeta[]
  onComplete: (templateId: string) => void
}

export function IndustryOnboarding({ templates, onComplete }: IndustryOnboardingProps) {
  const options = templates && templates.length > 0 ? templates : FALLBACK_OPTIONS
  const [selected, setSelected] = useState<string>(options[0]?.id ?? 'electronics')

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
        {options.map(option => {
          const active = selected === option.id
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => setSelected(option.id)}
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
