import { useEffect, useMemo, useState } from 'react'
import { cn } from '../../lib/utils'
import type { IndustryTemplateMeta, OnboardingCompanyMeta } from '../../types'

/** Hardcoded fallback used when no templates prop is provided (legacy compat). */
const FALLBACK_OPTIONS: IndustryTemplateMeta[] = [
  {
    id: 'electronics',
    label: 'Hardware',
    category: 'retail',
    description: 'Trade-ins, valuation, negotiation, pickup booking.',
    defaultVoice: 'Aoede',
    theme: {
      accent: 'oklch(74% 0.21 158)',
      accentSoft: 'oklch(62% 0.14 172)',
      title: 'Hardware Trade Desk',
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
  companies?: OnboardingCompanyMeta[]
  defaultTemplateId?: string | null
  defaultCompanyId?: string | null
  onComplete: (selection: { templateId: string; companyId: string }) => void
}

function resolveTemplateDisplayLabel(option: IndustryTemplateMeta): string {
  if (option.id === 'electronics') return 'Hardware'
  return option.label
}

export function IndustryOnboarding({
  templates,
  companies,
  defaultTemplateId,
  defaultCompanyId,
  onComplete,
}: IndustryOnboardingProps) {
  const options = templates && templates.length > 0 ? templates : FALLBACK_OPTIONS
  const initialTemplateId =
    (defaultTemplateId && options.some(option => option.id === defaultTemplateId) && defaultTemplateId) ||
    options[0]?.id ||
    'electronics'
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>(initialTemplateId)
  const [templateTouched, setTemplateTouched] = useState(false)

  const availableCompanies = useMemo(() => {
    if (!companies || companies.length === 0) return []
    return companies.filter(company => company.templateId === selectedTemplateId)
  }, [companies, selectedTemplateId])

  const fallbackCompanyId = `ekaette-${selectedTemplateId}`
  const [selectedCompanyId, setSelectedCompanyId] = useState<string>(
    defaultCompanyId && availableCompanies.some(company => company.id === defaultCompanyId)
      ? defaultCompanyId
      : availableCompanies[0]?.id ?? fallbackCompanyId,
  )

  useEffect(() => {
    if (
      !templateTouched &&
      defaultTemplateId &&
      options.some(option => option.id === defaultTemplateId) &&
      selectedTemplateId !== defaultTemplateId
    ) {
      setSelectedTemplateId(defaultTemplateId)
      return
    }
    if (!options.some(option => option.id === selectedTemplateId)) {
      setSelectedTemplateId(initialTemplateId)
    }
  }, [defaultTemplateId, initialTemplateId, options, selectedTemplateId, templateTouched])

  useEffect(() => {
    const nextCompanyId =
      (defaultCompanyId &&
        availableCompanies.some(company => company.id === defaultCompanyId) &&
        defaultCompanyId) ||
      availableCompanies[0]?.id ||
      fallbackCompanyId

    if (!availableCompanies.some(company => company.id === selectedCompanyId)) {
      setSelectedCompanyId(nextCompanyId)
    }
  }, [availableCompanies, defaultCompanyId, fallbackCompanyId, selectedCompanyId])

  return (
    <section className="panel-glass mx-auto w-full max-w-3xl px-4 py-5 sm:px-7 sm:py-8">
      <p className="text-[0.58rem] text-[color:var(--industry-accent)] uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
        Vendor Setup
      </p>
      <h1 className="mt-2 font-display text-white text-xl leading-tight sm:text-3xl">
        Configure Your Business
      </h1>
      <p className="mt-2 max-w-2xl text-muted-foreground text-xs leading-relaxed sm:text-sm">
        Select the industry and company profile for your AI assistant. This determines voice,
        behavior, and capabilities for customer calls.
      </p>

      <div className="mt-5 grid gap-3 sm:mt-6 sm:grid-cols-2" role="radiogroup" aria-label="Industry selection">
        {options.map(option => {
          const active = selectedTemplateId === option.id
          const displayLabel = resolveTemplateDisplayLabel(option)
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => {
                setTemplateTouched(true)
                setSelectedTemplateId(option.id)
              }}
              className={cn(
                'rounded-2xl border px-4 py-4 text-left transition',
                active
                  ? 'border-primary/60 bg-primary/10'
                  : 'border-border/70 bg-card/40 hover:border-primary/40',
              )}
            >
              <p className="font-semibold text-white">{displayLabel}</p>
              <p className="mt-1 text-muted-foreground text-sm">{option.description}</p>
            </button>
          )
        })}
      </div>

      <div className="mt-4">
        <label htmlFor="vendor-company" className="block text-[0.68rem] text-muted-foreground uppercase tracking-[0.16em]">
          Company
        </label>
        <select
          id="vendor-company"
          aria-label="Choose Company"
          value={selectedCompanyId}
          onChange={event => setSelectedCompanyId(event.target.value)}
          className="mt-2 w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white outline-none focus:border-primary/60"
        >
          {availableCompanies.length > 0 ? (
            availableCompanies.map(company => (
              <option key={company.id} value={company.id}>
                {company.displayName}
              </option>
            ))
          ) : (
            <option value={fallbackCompanyId}>{fallbackCompanyId}</option>
          )}
        </select>
      </div>

      <div className="mt-6 flex justify-stretch sm:justify-end">
        <button
          type="button"
          onClick={() =>
            onComplete({
              templateId: selectedTemplateId,
              companyId: selectedCompanyId || fallbackCompanyId,
            })
          }
          className="w-full rounded-full bg-[color:var(--industry-accent)] px-5 py-2.5 font-semibold text-black text-sm transition hover:brightness-110 sm:w-auto sm:py-2"
        >
          Launch Live Desk
        </button>
      </div>
    </section>
  )
}
