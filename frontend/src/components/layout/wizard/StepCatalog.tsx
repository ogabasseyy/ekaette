import { useCallback, useEffect, useState } from 'react'
import { useWizardApi } from './useWizardApi'

interface StepCatalogProps {
  companyId: string
  tenantId: string
  onNext: (count: number) => void
  onBack: () => void
}

const DEFAULT_PRODUCTS_JSON = JSON.stringify(
  [{ id: 'sample-1', name: 'Sample Product', price: 100, currency: 'USD', in_stock: true }],
  null,
  2,
)

export function StepCatalog({ companyId, tenantId, onNext, onBack }: StepCatalogProps) {
  const [productsJson, setProductsJson] = useState(DEFAULT_PRODUCTS_JSON)
  const [sourceUrl, setSourceUrl] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [productCount, setProductCount] = useState<number | null | undefined>(undefined)
  const [file, setFile] = useState<File | null>(null)
  const [fileInputKey, setFileInputKey] = useState(0)
  const { callJson, callFormData, runAction, busy, error } = useWizardApi({ tenantId })
  const companyUrl = `/api/v1/admin/companies/${encodeURIComponent(companyId)}`

  const loadCatalogSummary = useCallback(async () => {
    try {
      const payload = await callJson(`${companyUrl}/export`, {
        method: 'POST',
        payload: { includeRuntimeData: true },
      })
      const counts =
        payload.counts && typeof payload.counts === 'object'
          ? (payload.counts as Record<string, unknown>)
          : {}
      setProductCount(typeof counts.products === 'number' ? counts.products : 0)
    } catch (error) {
      console.error('Failed to load catalog summary:', error)
      setProductCount(null)
    }
  }, [callJson, companyUrl])

  useEffect(() => {
    void loadCatalogSummary()
  }, [loadCatalogSummary])

  const importProducts = useCallback(async () => {
    await runAction(async () => {
      const products = JSON.parse(productsJson)
      const payload = await callJson(`${companyUrl}/products/import`, {
        method: 'POST',
        idempotencyPrefix: 'wizard-products-import',
        payload: { products, data_tier: 'admin' },
      })
      const written = typeof payload.written === 'number' ? payload.written : '?'
      setStatus(`Imported ${written} products`)
      await loadCatalogSummary()
    })
  }, [callJson, runAction, companyUrl, loadCatalogSummary, productsJson])

  const syncFromSheets = useCallback(async () => {
    if (!sourceUrl.trim()) return
    await runAction(async () => {
      const payload = await callJson(`${companyUrl}/inventory/sync`, {
        method: 'POST',
        idempotencyPrefix: 'wizard-inventory-sync',
        payload: { source_type: 'google_sheets', source_url: sourceUrl },
      })
      const written = typeof payload.written === 'number' ? payload.written : '?'
      setStatus(`Synced ${written} items from Google Sheets`)
      await loadCatalogSummary()
    })
  }, [callJson, runAction, companyUrl, loadCatalogSummary, sourceUrl])

  const uploadFile = useCallback(async () => {
    if (!file) return
    await runAction(async () => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('data_tier', 'admin')
      const payload = await callFormData(`${companyUrl}/inventory/upload`, formData, {
        idempotencyPrefix: 'wizard-inventory-upload',
      })
      const written = typeof payload.written === 'number' ? payload.written : '?'
      setStatus(`Uploaded ${written} items`)
      setFile(null)
      setFileInputKey(key => key + 1)
      await loadCatalogSummary()
    })
  }, [callFormData, runAction, companyUrl, file, loadCatalogSummary])

  return (
    <>
      <div className="mt-5 space-y-4">
        <h2 className="font-semibold text-white">Product Catalog</h2>
        <div className="rounded-lg border border-border/40 bg-card/30 px-4 py-2.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wider">Current catalog</p>
          <p className="mt-1 text-sm text-white">
            {productCount === undefined
              ? 'Loading catalog…'
              : productCount !== null
                ? `${productCount} product${productCount === 1 ? '' : 's'} connected`
                : 'Unable to load catalog summary'}
          </p>
        </div>

        <div className="space-y-2">
          <p className="text-xs text-muted-foreground uppercase tracking-wider">Import JSON</p>
          <textarea
            aria-label="Products JSON"
            value={productsJson}
            onChange={e => setProductsJson(e.target.value)}
            rows={5}
            className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 font-mono text-xs text-white outline-none focus:border-primary/60"
          />
          <button
            type="button"
            disabled={busy}
            onClick={importProducts}
            className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary transition hover:bg-primary/15 disabled:opacity-50"
          >
            Import Products
          </button>
        </div>

        <div className="space-y-2">
          <p className="text-xs text-muted-foreground uppercase tracking-wider">
            Sync from Google Sheets
          </p>
          <input
            type="url"
            aria-label="Google Sheets URL"
            value={sourceUrl}
            onChange={e => setSourceUrl(e.target.value)}
            placeholder="https://docs.google.com/spreadsheets/d/<id>/edit#gid=0"
            className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white outline-none focus:border-primary/60"
          />
          <button
            type="button"
            disabled={busy || !sourceUrl.trim()}
            onClick={syncFromSheets}
            className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary transition hover:bg-primary/15 disabled:opacity-50"
          >
            Sync Sheets
          </button>
        </div>

        <div className="space-y-2">
          <p className="text-xs text-muted-foreground uppercase tracking-wider">Upload CSV/XLSX</p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              key={fileInputKey}
              type="file"
              aria-label="Inventory file"
              accept=".csv,.xlsx"
              onChange={e => setFile(e.target.files?.[0] ?? null)}
              className="text-xs text-muted-foreground file:mr-2 file:rounded-full file:border-0 file:bg-primary/10 file:px-3 file:py-1 file:text-xs file:font-medium file:text-primary"
            />
            {file ? (
              <button
                type="button"
                disabled={busy}
                onClick={uploadFile}
                className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary transition hover:bg-primary/15 disabled:opacity-50"
              >
                Upload
              </button>
            ) : null}
          </div>
        </div>

        {error ? (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        {status ? <p className="text-xs text-emerald-400">{status}</p> : null}
      </div>

      <div className="mt-6 flex justify-between">
        <button
          type="button"
          onClick={onBack}
          className="rounded-full border border-border/50 bg-card/40 px-5 py-2 text-sm text-muted-foreground transition hover:text-white"
        >
          Back
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => onNext(productCount ?? 0)}
            className="rounded-full border border-border/50 bg-card/40 px-5 py-2 text-sm text-muted-foreground transition hover:text-white"
          >
            Skip
          </button>
          <button
            type="button"
            onClick={() => onNext(productCount ?? 0)}
            className="rounded-full bg-[color:var(--industry-accent)] px-5 py-2 font-semibold text-black text-sm transition hover:brightness-110"
          >
            Next
          </button>
        </div>
      </div>
    </>
  )
}
