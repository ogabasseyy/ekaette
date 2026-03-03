import { useEffect, useState } from 'react'
import type { IndustryTemplateMeta } from '../../../types'

interface StepLaunchProps {
  templateId: string
  companyId: string
  tenantId: string
  templates?: IndustryTemplateMeta[]
  onBack: () => void
  onLaunch: (selection: { templateId: string; companyId: string }) => void
}

interface SummaryCounts {
  knowledge: number | null
  connectors: number | null
  products: number | null
}

export function StepLaunch({
  templateId,
  companyId,
  tenantId,
  templates,
  onBack,
  onLaunch,
}: StepLaunchProps) {
  const [counts, setCounts] = useState<SummaryCounts>({
    knowledge: null,
    connectors: null,
    products: null,
  })

  const template = templates?.find(t => t.id === templateId)
  const voice = template?.defaultVoice ?? 'Aoede'
  const title = template?.theme?.title ?? templateId

  useEffect(() => {
    let disposed = false
    const controller = new AbortController()

    async function loadCounts() {
      const companyUrl = `/api/v1/admin/companies/${encodeURIComponent(companyId)}`
      const headers: Record<string, string> = {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'x-tenant-id': tenantId,
      }
      const separator = companyUrl.includes('?') ? '&' : '?'
      const tenantSuffix = `${separator}tenantId=${encodeURIComponent(tenantId)}`

      try {
        const [knowledgeRes, companyRes] = await Promise.all([
          fetch(`${companyUrl}/knowledge${tenantSuffix}`, { headers, signal: controller.signal }),
          fetch(`${companyUrl}${tenantSuffix}`, { headers, signal: controller.signal }),
        ])

        if (disposed) return

        let knowledgeCount: number | null = null
        let connectorCount: number | null = null

        if (knowledgeRes.ok) {
          const data = (await knowledgeRes.json()) as Record<string, unknown>
          const entries = data.entries
          if (Array.isArray(entries)) knowledgeCount = entries.length
        }

        if (companyRes.ok) {
          const data = (await companyRes.json()) as Record<string, unknown>
          const company = data.company as Record<string, unknown> | undefined
          const connectors = company?.connectors
          if (connectors && typeof connectors === 'object') {
            connectorCount = Object.keys(connectors).length
          }
        }

        if (!disposed) {
          setCounts({
            knowledge: knowledgeCount,
            connectors: connectorCount,
            products: null,
          })
        }
      } catch {
        /* non-blocking — counts will show as "—" */
      }
    }

    void loadCounts()
    return () => {
      disposed = true
      controller.abort()
    }
  }, [companyId, tenantId])

  const summaryItems = [
    { label: 'Industry', value: title },
    { label: 'Company', value: companyId },
    { label: 'Voice', value: voice },
    {
      label: 'Knowledge entries',
      value: counts.knowledge !== null ? String(counts.knowledge) : '—',
    },
    { label: 'Connectors', value: counts.connectors !== null ? String(counts.connectors) : '—' },
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
              <span className="text-muted-foreground text-sm">{item.label}</span>
              <span className="font-medium text-sm text-white">{item.value}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-6 flex justify-between">
        <button
          type="button"
          onClick={onBack}
          className="rounded-full border border-border/50 bg-card/40 px-5 py-2 text-muted-foreground text-sm transition hover:text-white"
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
