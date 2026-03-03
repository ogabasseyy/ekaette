import { useCallback, useEffect, useRef, useState } from 'react'
import type { AdminKnowledgeEntry } from './useWizardApi'
import { parseCsv, useWizardApi } from './useWizardApi'

interface StepKnowledgeProps {
  companyId: string
  tenantId: string
  onNext: () => void
  onBack: () => void
}

export function StepKnowledge({ companyId, tenantId, onNext, onBack }: StepKnowledgeProps) {
  const [title, setTitle] = useState('FAQ')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [entries, setEntries] = useState<AdminKnowledgeEntry[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const [showAllFormats, setShowAllFormats] = useState(false)
  const api = useWizardApi({ tenantId })
  const companyUrl = `/api/v1/admin/companies/${encodeURIComponent(companyId)}`

  const loadEntries = useCallback(async () => {
    try {
      const payload = await api.callJson(`${companyUrl}/knowledge`)
      setEntries(Array.isArray(payload.entries) ? (payload.entries as AdminKnowledgeEntry[]) : [])
    } catch {
      /* non-blocking */
    }
  }, [api, companyUrl])

  useEffect(() => {
    void loadEntries()
  }, [loadEntries])

  const importText = useCallback(async () => {
    if (!text.trim()) return
    await api.runAction(async () => {
      await api.callJson(`${companyUrl}/knowledge/import-text`, {
        method: 'POST',
        idempotencyPrefix: 'wizard-knowledge-text',
        payload: { title, text, tags: parseCsv(title), source: 'wizard' },
      })
      setStatus('Knowledge text imported')
      setText('')
      await loadEntries()
    })
  }, [api, companyUrl, loadEntries, text, title])

  const importFile = useCallback(async () => {
    if (!file) return
    await api.runAction(async () => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('title', title || file.name)
      formData.append('tags', title)
      await api.callFormData(`${companyUrl}/knowledge/import-file`, formData, {
        idempotencyPrefix: 'wizard-knowledge-file',
      })
      setStatus('Knowledge file imported')
      setFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      await loadEntries()
    })
  }, [api, companyUrl, file, loadEntries, title])

  const deleteEntry = useCallback(
    async (knowledgeId: string) => {
      await api.runAction(async () => {
        await api.callJson(`${companyUrl}/knowledge/${encodeURIComponent(knowledgeId)}`, {
          method: 'DELETE',
          idempotencyPrefix: 'wizard-knowledge-delete',
        })
        setStatus(`Deleted: ${knowledgeId}`)
        await loadEntries()
      })
    },
    [api, companyUrl, loadEntries],
  )

  return (
    <>
      <div className="mt-5 space-y-4">
        <h2 className="font-semibold text-white">Knowledge Base</h2>

        <div className="space-y-2">
          <input
            type="text"
            aria-label="Knowledge title"
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="Title"
            className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white outline-none focus:border-primary/60"
          />
          <textarea
            aria-label="Knowledge text"
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder="Paste knowledge text here..."
            rows={3}
            className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white outline-none focus:border-primary/60"
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={api.busy || !text.trim()}
              onClick={importText}
              className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 font-semibold text-primary text-xs transition hover:bg-primary/15 disabled:opacity-50"
            >
              Import Text
            </button>
            <input
              ref={fileInputRef}
              type="file"
              aria-label="Knowledge file"
              accept=".pdf,.docx,.doc,.xlsx,.xls,.xlsm,.pptx,.ppt,.odt,.ods,.html,.htm,.txt,.md,.csv,.tsv,.json,.yaml,.toml,.xml,.epub,.rtf,.eml,.msg,.jpg,.jpeg,.png,.gif,.bmp,.tiff,.tif,.webp,.svg,.zip,.tar,.gz,.7z"
              onChange={e => setFile(e.target.files?.[0] ?? null)}
              className="cursor-pointer text-muted-foreground text-xs transition file:mr-2 file:cursor-pointer file:rounded-full file:border file:border-primary/40 file:bg-primary/10 file:px-3 file:py-1 file:font-medium file:text-primary file:text-xs file:transition hover:text-white file:hover:border-primary/70 file:hover:bg-primary/20"
            />
            <p className="w-full text-[0.6rem] text-muted-foreground/60">
              PDF, DOCX, XLSX, PPTX, Images, HTML, TXT, MD, CSV, JSON{' '}
              <button
                type="button"
                onClick={() => setShowAllFormats(v => !v)}
                className="text-primary/70 underline decoration-primary/30 underline-offset-2 transition hover:text-primary"
              >
                {showAllFormats ? 'show less' : 'and 70+ more'}
              </button>
            </p>
            {showAllFormats ? (
              <div className="w-full rounded-lg border border-border/30 bg-card/20 px-3 py-2 text-[0.6rem] text-muted-foreground/60 leading-relaxed">
                <span className="font-medium text-muted-foreground/80">Documents:</span> PDF, DOCX,
                DOC, ODT, RTF, TXT, MD, EPUB
                <br />
                <span className="font-medium text-muted-foreground/80">Spreadsheets:</span> XLSX,
                XLS, XLSM, ODS, CSV, TSV
                <br />
                <span className="font-medium text-muted-foreground/80">Presentations:</span> PPTX,
                PPT
                <br />
                <span className="font-medium text-muted-foreground/80">Images (OCR):</span> JPEG,
                PNG, GIF, BMP, TIFF, WebP, SVG
                <br />
                <span className="font-medium text-muted-foreground/80">Web &amp; Data:</span> HTML,
                XML, JSON, YAML, TOML
                <br />
                <span className="font-medium text-muted-foreground/80">Email:</span> EML, MSG
                <br />
                <span className="font-medium text-muted-foreground/80">Archives:</span> ZIP, TAR,
                GZ, 7Z
              </div>
            ) : null}
            {file ? (
              <button
                type="button"
                disabled={api.busy}
                onClick={importFile}
                className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 font-semibold text-primary text-xs transition hover:bg-primary/15 disabled:opacity-50"
              >
                Upload File
              </button>
            ) : null}
          </div>
        </div>

        {api.error ? (
          <p className="text-destructive text-xs" role="alert">
            {api.error}
          </p>
        ) : null}
        {status ? <p className="text-emerald-400 text-xs">{status}</p> : null}

        {entries.length > 0 ? (
          <div className="space-y-1">
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Existing entries ({entries.length})
            </p>
            {entries.map(entry => (
              <div
                key={entry.id}
                className="flex items-center justify-between rounded-lg border border-border/40 bg-card/30 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-white">{entry.title ?? entry.id}</p>
                  {entry.tags && entry.tags.length > 0 ? (
                    <p className="text-muted-foreground text-xs">{entry.tags.join(', ')}</p>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => deleteEntry(entry.id)}
                  className="ml-2 shrink-0 text-destructive/70 text-xs hover:text-destructive"
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        ) : null}
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
