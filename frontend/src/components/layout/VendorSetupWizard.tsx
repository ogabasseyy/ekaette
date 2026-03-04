import { lazy, Suspense, useCallback, useReducer } from 'react'
import type { IndustryTemplateMeta, OnboardingCompanyMeta, WizardStepId } from '../../types'
import { WizardStepIndicator } from './wizard/WizardStepIndicator'
// --- Lazy-loaded step components (bundle-dynamic-imports) ---
const StepIndustry = lazy(() =>
  import('./wizard/StepIndustry').then(m => ({ default: m.StepIndustry })),
)
const StepKnowledge = lazy(() =>
  import('./wizard/StepKnowledge').then(m => ({ default: m.StepKnowledge })),
)
const StepConnectors = lazy(() =>
  import('./wizard/StepConnectors').then(m => ({ default: m.StepConnectors })),
)
const StepCatalog = lazy(() =>
  import('./wizard/StepCatalog').then(m => ({ default: m.StepCatalog })),
)
const StepLaunch = lazy(() =>
  import('./wizard/StepLaunch').then(m => ({ default: m.StepLaunch })),
)

// --- Fallback templates (same as IndustryOnboarding, for title/hint resolution) ---
const FALLBACK_OPTIONS: IndustryTemplateMeta[] = [
  {
    id: 'electronics',
    label: 'Hardware',
    category: 'retail',
    description: 'Trade-ins, valuation, negotiation, pickup booking.',
    defaultVoice: 'Aoede',
    theme: { accent: 'oklch(74% 0.21 158)', accentSoft: 'oklch(62% 0.14 172)', title: 'Hardware Trade Desk', hint: 'Inspect. Value. Negotiate. Book pickup.' },
    capabilities: [],
    status: 'active',
  },
]

// --- Step ordering ---
const STEPS: WizardStepId[] = ['industry', 'knowledge', 'connectors', 'catalog', 'launch']

// --- Wizard state machine ---
interface WizardState {
  currentStep: number
  completedSteps: Set<number>
  templateId: string
  companyId: string
}

type WizardAction =
  | { type: 'ADVANCE'; templateId?: string; companyId?: string }
  | { type: 'GO_BACK' }
  | { type: 'GO_TO_STEP'; step: number }

function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'ADVANCE': {
      const nextCompleted = new Set(state.completedSteps)
      nextCompleted.add(state.currentStep)
      const nextStep = Math.min(state.currentStep + 1, STEPS.length - 1)
      return {
        ...state,
        currentStep: nextStep,
        completedSteps: nextCompleted,
        templateId: action.templateId ?? state.templateId,
        companyId: action.companyId ?? state.companyId,
      }
    }
    case 'GO_BACK': {
      const prevStep = Math.max(state.currentStep - 1, 0)
      return { ...state, currentStep: prevStep }
    }
    case 'GO_TO_STEP': {
      if (state.completedSteps.has(action.step) || action.step === state.currentStep) {
        return { ...state, currentStep: action.step }
      }
      return state
    }
    default:
      return state
  }
}

// --- Spinner for Suspense fallback ---
function StepSpinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="size-6 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
    </div>
  )
}

// --- Props (drop-in replacement for IndustryOnboarding) ---
interface VendorSetupWizardProps {
  templates?: IndustryTemplateMeta[]
  companies?: OnboardingCompanyMeta[]
  defaultTemplateId?: string | null
  defaultCompanyId?: string | null
  onComplete: (selection: { templateId: string; companyId: string }) => void
}

export function VendorSetupWizard({
  templates,
  companies,
  defaultTemplateId,
  defaultCompanyId,
  onComplete,
}: VendorSetupWizardProps) {
  const options = templates && templates.length > 0 ? templates : FALLBACK_OPTIONS
  return (
    <NormalWizard
      templates={templates}
      companies={companies}
      options={options}
      defaultTemplateId={defaultTemplateId}
      defaultCompanyId={defaultCompanyId}
      onComplete={onComplete}
    />
  )
}

// Extracted to its own component so hooks are always called (no conditional hooks)
function NormalWizard({
  templates,
  companies,
  options,
  defaultTemplateId,
  defaultCompanyId,
  onComplete,
}: {
  templates?: IndustryTemplateMeta[]
  companies?: OnboardingCompanyMeta[]
  options: IndustryTemplateMeta[]
  defaultTemplateId?: string | null
  defaultCompanyId?: string | null
  onComplete: (selection: { templateId: string; companyId: string }) => void
}) {
  const initialTemplateId = defaultTemplateId ?? options[0]?.id ?? 'electronics'
  const initialCompanyId = defaultCompanyId ?? `ekaette-${initialTemplateId}`

  const [state, dispatch] = useReducer(wizardReducer, {
    currentStep: 0,
    completedSteps: new Set<number>(),
    templateId: initialTemplateId,
    companyId: initialCompanyId,
  })

  const tenantId = String(import.meta.env.VITE_TENANT_ID ?? 'public')

  // Derive template metadata during render (rerender-derived-state-no-effect)
  const currentTemplate = options.find(o => o.id === state.templateId)
  const templateTitle = currentTemplate?.theme?.title ?? currentTemplate?.label ?? state.templateId
  const templateHint = currentTemplate?.theme?.hint ?? currentTemplate?.description ?? ''

  const handleStepClick = useCallback((step: number) => {
    dispatch({ type: 'GO_TO_STEP', step })
  }, [])

  const handleIndustryNext = useCallback((selection: { templateId: string; companyId: string }) => {
    dispatch({ type: 'ADVANCE', templateId: selection.templateId, companyId: selection.companyId })
  }, [])

  const handleAdvance = useCallback(() => {
    dispatch({ type: 'ADVANCE' })
  }, [])

  const handleBack = useCallback(() => {
    dispatch({ type: 'GO_BACK' })
  }, [])

  const handleLaunch = useCallback(
    (selection: { templateId: string; companyId: string }) => {
      onComplete(selection)
    },
    [onComplete],
  )

  const stepId = STEPS[state.currentStep]

  return (
    <section className="panel-glass mx-auto w-full max-w-3xl px-4 py-5 sm:px-7 sm:py-8">
      <p className="text-[0.58rem] text-[color:var(--industry-accent)] uppercase tracking-[0.24em] sm:text-[0.64rem] sm:tracking-[0.3em]">
        Vendor Setup
      </p>
      <h1 className="mt-2 font-display text-white text-xl leading-tight sm:text-3xl">
        {templateTitle}
      </h1>
      <p className="mt-2 max-w-2xl text-muted-foreground text-xs leading-relaxed sm:text-sm">
        {templateHint}
      </p>

      <div className="mt-5">
        <WizardStepIndicator
          currentStep={state.currentStep}
          completedSteps={state.completedSteps}
          onStepClick={handleStepClick}
        />
      </div>

      <Suspense fallback={<StepSpinner />}>
        {stepId === 'industry' ? (
          <StepIndustry
            templates={templates}
            companies={companies}
            defaultTemplateId={state.templateId}
            defaultCompanyId={state.companyId}
            onNext={handleIndustryNext}
          />
        ) : null}

        {stepId === 'knowledge' ? (
          <StepKnowledge
            companyId={state.companyId}
            tenantId={tenantId}
            onNext={handleAdvance}
            onBack={handleBack}
          />
        ) : null}

        {stepId === 'connectors' ? (
          <StepConnectors
            companyId={state.companyId}
            tenantId={tenantId}
            onNext={handleAdvance}
            onBack={handleBack}
          />
        ) : null}

        {stepId === 'catalog' ? (
          <StepCatalog
            companyId={state.companyId}
            tenantId={tenantId}
            onNext={handleAdvance}
            onBack={handleBack}
          />
        ) : null}

        {stepId === 'launch' ? (
          <StepLaunch
            templateId={state.templateId}
            companyId={state.companyId}
            tenantId={tenantId}
            templates={templates}
            onBack={handleBack}
            onLaunch={handleLaunch}
          />
        ) : null}
      </Suspense>
    </section>
  )
}
