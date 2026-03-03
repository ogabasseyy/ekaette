import { useCallback, useRef, useState } from 'react'
import { useWizardApi } from './useWizardApi'

interface StepCatalogProps {
  companyId: string
  tenantId: string
  onNext: () => void
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
  const [file, setFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const api = useWizardApi({ tenantId })
  const companyUrl = `/api/v1/admin/companies/${encodeURIComponent(companyId)}`

  const importProducts = useCallback(async () => {
    await api.runAction(async () => {
      const products = JSON.parse(productsJson)
      const payload = await api.callJson(`${companyUrl}/products/import`, {
        method: 'POST',
        idempotencyPrefix: 'wizard-products-import',
        payload: { products, data_tier: 'admin' },
      })
      const written = typeof payload.written === 'number' ? payload.written : '?'
      setStatus(`Imported ${written} products`)
    })
  }, [api, companyUrl, productsJson])

  const syncFromSheets = useCallback(async () => {
    if (!sourceUrl.trim()) return
    await api.runAction(async () => {
      const payload = await api.callJson(`${companyUrl}/inventory/sync`, {
        method: 'POST',
        idempotencyPrefix: 'wizard-inventory-sync',
        payload: { source_type: 'google_sheets', source_url: sourceUrl },
      })
      const written = typeof payload.written === 'number' ? payload.written : '?'
      setStatus(`Synced ${written} items from Google Sheets`)
    })
  }, [api, companyUrl, sourceUrl])

  const uploadFile = useCallback(async () => {
    if (!file) return
    await api.runAction(async () => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('data_tier', 'admin')
      const payload = await api.callFormData(`${companyUrl}/inventory/upload`, formData, {
        idempotencyPrefix: 'wizard-inventory-upload',
      })
      const written = typeof payload.written === 'number' ? payload.written : '?'
      setStatus(`Uploaded ${written} items`)
      setFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
    })
  }, [api, companyUrl, file])

  return (
    <>
      <div className="mt-5 space-y-4">
        <h2 className="font-semibold text-white">Product Catalog</h2>

        <div className="space-y-2">
          <p className="text-muted-foreground text-xs uppercase tracking-wider">Import JSON</p>
          <textarea
            aria-label="Products JSON"
            value={productsJson}
            onChange={e => setProductsJson(e.target.value)}
            rows={5}
            className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 font-mono text-white text-xs outline-none focus:border-primary/60"
          />
          <button
            type="button"
            disabled={api.busy}
            onClick={importProducts}
            className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 font-semibold text-primary text-xs transition hover:bg-primary/15 disabled:opacity-50"
          >
            Import Products
          </button>
        </div>

        <div className="space-y-2">
          <p className="text-muted-foreground text-xs uppercase tracking-wider">
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
            disabled={api.busy || !sourceUrl.trim()}
            onClick={syncFromSheets}
            className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 font-semibold text-primary text-xs transition hover:bg-primary/15 disabled:opacity-50"
          >
            Sync Sheets
          </button>
        </div>

        <div className="space-y-2">
          <p className="text-muted-foreground text-xs uppercase tracking-wider">Upload CSV/XLSX</p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              aria-label="Inventory file"
              accept=".csv,.xlsx"
              onChange={e => setFile(e.target.files?.[0] ?? null)}
              className="text-muted-foreground text-xs file:mr-2 file:rounded-full file:border-0 file:bg-primary/10 file:px-3 file:py-1 file:font-medium file:text-primary file:text-xs"
            />
            {file ? (
              <button
                type="button"
                disabled={api.busy}
                onClick={uploadFile}
                className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 font-semibold text-primary text-xs transition hover:bg-primary/15 disabled:opacity-50"
              >
                Upload
              </button>
            ) : null}
          </div>
        </div>

        {api.error ? (
          <p className="text-destructive text-xs" role="alert">
            {api.error}
          </p>
        ) : null}
        {status ? (
          <p className="text-emerald-400 text-xs">{status}</p>
        ) : (
          <p className="text-muted-foreground/60 text-xs">
            No products imported yet. Add products via JSON, Google Sheets, or file upload above, or
            skip this step to add them later.
          </p>
        )}
      </div>

      <div className="mt-6 flex justify-between">
        <button
          type="button"
          onClick={onBack}
          className="rounded-full border border-border/50 bg-card/40 px-5 py-2 text-muted-foreground text-sm transition hover:text-white"
        >
          Back
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onNext}
            className="rounded-full border border-border/50 bg-card/40 px-5 py-2 text-muted-foreground text-sm transition hover:text-white"
          >
            Skip
          </button>
          <button
            type="button"
            onClick={onNext}
            className="rounded-full bg-[color:var(--industry-accent)] px-5 py-2 font-semibold text-black text-sm transition hover:brightness-110"
          >
            Next
          </button>
        </div>
      </div>
    </>
  )
}
