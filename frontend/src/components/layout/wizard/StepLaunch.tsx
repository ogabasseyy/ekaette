import { useEffect, useState } from 'react'
import type { IndustryTemplateMeta } from '../../../types'
import type { WizardCounts } from '../VendorSetupWizard'
import { useWizardApi } from './useWizardApi'

interface StepLaunchProps {
  templateId: string
  companyId: string
  tenantId: string
  templates?: IndustryTemplateMeta[]
  counts: WizardCounts
  onBack: () => void
  onLaunch: (selection: { templateId: string; companyId: string }) => void
}

function CountValue({ value, loading }: { value: string; loading: boolean }) {
  if (loading) {
    return (
      <span className="inline-block h-4 w-8 animate-pulse rounded bg-white/10" />
    )
  }
  return <span className="text-sm font-medium text-white">{value}</span>
}

export function StepLaunch({
  templateId,
  companyId,
  tenantId,
  templates,
  counts: wizardCounts,
  onBack,
  onLaunch,
}: StepLaunchProps) {
  const { callJson } = useWizardApi({ tenantId })
  const [companyDisplayName, setCompanyDisplayName] = useState(companyId)
  const [counts, setCounts] = useState<WizardCounts>(wizardCounts)
  const [loading, setLoading] = useState(true)

  const template = templates?.find(t => t.id === templateId)
  const voice = template?.defaultVoice ?? 'Aoede'
  const title = template?.theme?.title ?? templateId

  useEffect(() => {
    async function loadSummary() {
      try {
        const payload = await callJson(
          `/api/v1/admin/companies/${encodeURIComponent(companyId)}/export`,
          {
            method: 'POST',
            payload: { includeRuntimeData: true },
          },
        )
        const countsPayload =
          payload.counts && typeof payload.counts === 'object'
            ? (payload.counts as Record<string, unknown>)
            : {}
        const companyPayload =
          payload.company && typeof payload.company === 'object'
            ? (payload.company as Record<string, unknown>)
            : {}
        const connectorsPayload =
          companyPayload.connectors && typeof companyPayload.connectors === 'object'
            ? (companyPayload.connectors as Record<string, unknown>)
            : {}

        setCompanyDisplayName(
          typeof companyPayload.displayName === 'string' && companyPayload.displayName.trim()
            ? companyPayload.displayName
            : companyId,
        )
        setCounts({
          knowledge: typeof countsPayload.knowledge === 'number' ? countsPayload.knowledge : 0,
          connectors: Object.keys(connectorsPayload).length,
          products: typeof countsPayload.products === 'number' ? countsPayload.products : 0,
        })
      } catch {
        // Keep wizard counts on failure
      } finally {
        setLoading(false)
      }
    }

    void loadSummary()
  }, [callJson, companyId])

  const summaryItems = [
    { label: 'Industry', value: title, showLoading: false },
    { label: 'Company', value: companyDisplayName, showLoading: loading },
    { label: 'Voice', value: voice, showLoading: false },
    {
      label: 'Knowledge entries',
      value: counts.knowledge !== null ? String(counts.knowledge) : '—',
      showLoading: loading && counts.knowledge === null,
    },
    {
      label: 'Connectors',
      value: counts.connectors !== null ? String(counts.connectors) : '—',
      showLoading: loading && counts.connectors === null,
    },
    {
      label: 'Catalog items',
      value: counts.products !== null ? String(counts.products) : '—',
      showLoading: loading && counts.products === null,
    },
  ]

  return (
    <>
      <div className="mt-5 space-y-4">
        <h2 className="font-semibold text-white">Review & Launch</h2>

        <div className="space-y-2">
          {summaryItems.map(item => (
            <div
              key={item.label}
              className="flex items-center justify-between rounded-lg border border-border/40 bg-card/30 px-4 py-2.5"
            >
              <span className="text-sm text-muted-foreground">{item.label}</span>
              <CountValue value={item.value} loading={item.showLoading} />
            </div>
          ))}
        </div>
      </div>

      <div className="mt-6 flex justify-between">
        <button
          type="button"
          onClick={onBack}
          className="rounded-full border border-border/50 bg-card/40 px-5 py-2 text-sm text-muted-foreground transition hover:text-white"
        >
          Back
        </button>
        <button
          type="button"
          onClick={() => onLaunch({ templateId, companyId })}
          className="rounded-full bg-[color:var(--industry-accent)] px-5 py-2.5 font-semibold text-black text-sm transition hover:brightness-110 sm:py-2"
        >
          Launch Live Desk
        </button>
      </div>
    </>
  )
}
