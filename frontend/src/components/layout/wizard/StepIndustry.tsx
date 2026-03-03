import { useEffect, useMemo, useState } from 'react'
import { cn } from '../../../lib/utils'
import type { IndustryTemplateMeta, OnboardingCompanyMeta } from '../../../types'

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

function resolveTemplateDisplayLabel(option: IndustryTemplateMeta): string {
  if (option.id === 'electronics') return 'Hardware'
  return option.label
}

interface StepIndustryProps {
  templates?: IndustryTemplateMeta[]
  companies?: OnboardingCompanyMeta[]
  defaultTemplateId?: string | null
  defaultCompanyId?: string | null
  onNext: (selection: { templateId: string; companyId: string }) => void
}

export function StepIndustry({
  templates,
  companies,
  defaultTemplateId,
  defaultCompanyId,
  onNext,
}: StepIndustryProps) {
  const options = templates && templates.length > 0 ? templates : FALLBACK_OPTIONS
  const initialTemplateId =
    (defaultTemplateId && options.some(o => o.id === defaultTemplateId) && defaultTemplateId) ||
    options[0]?.id ||
    'electronics'
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>(initialTemplateId)
  const [templateTouched, setTemplateTouched] = useState(false)

  const availableCompanies = useMemo(
    () =>
      companies && companies.length > 0
        ? companies.filter(c => c.templateId === selectedTemplateId)
        : [],
    [companies, selectedTemplateId],
  )

  const fallbackCompanyId = `ekaette-${selectedTemplateId}`
  const [selectedCompanyId, setSelectedCompanyId] = useState<string>(
    defaultCompanyId && availableCompanies.some(c => c.id === defaultCompanyId)
      ? defaultCompanyId
      : (availableCompanies[0]?.id ?? fallbackCompanyId),
  )

  useEffect(() => {
    if (
      !templateTouched &&
      defaultTemplateId &&
      options.some(o => o.id === defaultTemplateId) &&
      selectedTemplateId !== defaultTemplateId
    ) {
      setSelectedTemplateId(defaultTemplateId)
      return
    }
    if (!options.some(o => o.id === selectedTemplateId)) {
      setSelectedTemplateId(initialTemplateId)
    }
  }, [defaultTemplateId, initialTemplateId, options, selectedTemplateId, templateTouched])

  useEffect(() => {
    const nextCompanyId =
      (defaultCompanyId &&
        availableCompanies.some(c => c.id === defaultCompanyId) &&
        defaultCompanyId) ||
      availableCompanies[0]?.id ||
      fallbackCompanyId
    if (!availableCompanies.some(c => c.id === selectedCompanyId)) {
      setSelectedCompanyId(nextCompanyId)
    }
  }, [availableCompanies, defaultCompanyId, fallbackCompanyId, selectedCompanyId])

  return (
    <>
      <div
        className="mt-5 grid gap-3 sm:mt-6 sm:grid-cols-2"
        role="radiogroup"
        aria-label="Industry selection"
      >
        {options.map(option => {
          const active = selectedTemplateId === option.id
          const displayLabel = resolveTemplateDisplayLabel(option)
          return (
            // biome-ignore lint/a11y/useSemanticElements: styled card radio group, native input would break layout
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
        <label
          htmlFor="vendor-company"
          className="block text-[0.68rem] text-muted-foreground uppercase tracking-[0.16em]"
        >
          Company Name
        </label>
        <input
          id="vendor-company"
          type="text"
          aria-label="Company Name"
          value={selectedCompanyId}
          onChange={event => setSelectedCompanyId(event.target.value)}
          placeholder="e.g. Acme Electronics"
          className="mt-2 w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white outline-none placeholder:text-muted-foreground/50 focus:border-primary/60"
          list="vendor-company-suggestions"
        />
        {availableCompanies.length > 0 && (
          <datalist id="vendor-company-suggestions">
            {availableCompanies.map(company => (
              <option key={company.id} value={company.id}>
                {company.displayName}
              </option>
            ))}
          </datalist>
        )}
      </div>

      <div className="mt-6 flex justify-end">
        <button
          type="button"
          disabled={!selectedTemplateId || !selectedCompanyId.trim()}
          onClick={() =>
            onNext({
              templateId: selectedTemplateId,
              companyId: selectedCompanyId.trim() || fallbackCompanyId,
            })
          }
          className="rounded-full bg-[color:var(--industry-accent)] px-5 py-2.5 font-semibold text-black text-sm transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50 sm:py-2"
        >
          Next
        </button>
      </div>
    </>
  )
}
