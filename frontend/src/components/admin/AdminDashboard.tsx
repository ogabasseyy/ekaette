import { useEffect, useMemo, useRef, useState } from 'react'
import { NavBar } from '../layout/NavBar'
import { makeIdempotencyKey, parseCsv, withTenant } from '../layout/wizard/useWizardApi'

interface AdminCompanyMeta {
  id: string
  templateId?: string
  displayName?: string
  status?: string
}

interface AdminProviderMeta {
  id: string
  label?: string
  status?: string
  requiresSecretRef?: boolean
  capabilities?: string[]
}

interface AdminKnowledgeEntry {
  id: string
  title?: string
  text?: string
  tags?: string[]
  source?: string
}

interface AdminConnectorEntry {
  id: string
  provider?: string
  enabled?: boolean
  capabilities?: string[]
  secret_ref?: string
}

interface AdminInventorySyncState {
  source_type?: string
  source_url?: string
  connector_id?: string
  sheet_name?: string
  data_tier?: string
  dry_run?: boolean
  auto_enabled?: boolean
  interval_minutes?: number
  next_run_at?: string
  configured_at?: string
  last_attempt_at?: string
  last_error?: string
  status?: string
  updated_at?: string
  last_result?: {
    written?: number
    parsed_rows?: number
    normalized_rows?: number
    error_count?: number
  }
}

interface AdminCompanyDetail {
  id: string
  templateId?: string
  displayName?: string
  status?: string
  connectors?: Record<string, AdminConnectorEntry>
  inventorySync?: AdminInventorySyncState
}

type ConnectorMode = 'create' | 'update'

interface AdminCompaniesResponse {
  companies?: AdminCompanyMeta[]
}

interface AdminProvidersResponse {
  providers?: AdminProviderMeta[]
}

interface AdminCompanyResponse {
  company?: AdminCompanyDetail
}

interface AdminKnowledgeResponse {
  entries?: AdminKnowledgeEntry[]
}

async function parseResponseJson(response: Response): Promise<Record<string, unknown>> {
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) return {}
  return (await response.json()) as Record<string, unknown>
}

export function AdminDashboard() {
  const [tenantId, setTenantId] = useState('public')
  const [userId, setUserId] = useState('admin-user')
  const [adminKey, setAdminKey] = useState('')

  const [companyId, setCompanyId] = useState('ekaette-demo')
  const [displayName, setDisplayName] = useState('Ekaette Demo Company')
  const [templateId, setTemplateId] = useState('electronics')
  const [companyStatus, setCompanyStatus] = useState('active')
  const [activeCompanyId, setActiveCompanyId] = useState('ekaette-electronics')

  const [knowledgeTitle, setKnowledgeTitle] = useState('FAQ')
  const [knowledgeText, setKnowledgeText] = useState('We are open from 9am to 6pm.')
  const [knowledgeUrl, setKnowledgeUrl] = useState('')
  const [knowledgeTags, setKnowledgeTags] = useState('faq, policy')
  const [knowledgeFile, setKnowledgeFile] = useState<File | null>(null)

  const [connectorId, setConnectorId] = useState('crm')
  const [connectorProvider, setConnectorProvider] = useState('mock')
  const [connectorCapabilities, setConnectorCapabilities] = useState('read')
  const [connectorSecretRef, setConnectorSecretRef] = useState('')
  const [connectorEnabled, setConnectorEnabled] = useState(true)
  const [connectorMode, setConnectorMode] = useState<ConnectorMode>('create')

  const [productsJson, setProductsJson] = useState(
    JSON.stringify(
      [
        {
          id: 'iphone-13',
          name: 'iPhone 13',
          category: 'phones',
          price: 500,
          currency: 'USD',
          in_stock: true,
        },
      ],
      null,
      2,
    ),
  )
  const [slotsJson, setSlotsJson] = useState(
    JSON.stringify(
      [
        {
          id: 'slot-1',
          date: '2026-03-01',
          time: '10:00',
          available: true,
        },
      ],
      null,
      2,
    ),
  )
  const [runtimeDataTier, setRuntimeDataTier] = useState('admin')
  const [inventorySourceType, setInventorySourceType] = useState<'google_sheets' | 'mcp_connector'>(
    'google_sheets',
  )
  const [inventorySourceUrl, setInventorySourceUrl] = useState('')
  const [inventoryConnectorId, setInventoryConnectorId] = useState('inventory')
  const [inventorySheetName, setInventorySheetName] = useState('')
  const [inventoryDryRun, setInventoryDryRun] = useState(false)
  const [inventoryAutoEnabled, setInventoryAutoEnabled] = useState(false)
  const [inventoryIntervalMinutes, setInventoryIntervalMinutes] = useState('15')
  const [inventoryRunForce, setInventoryRunForce] = useState(true)
  const [inventoryFile, setInventoryFile] = useState<File | null>(null)

  const [companies, setCompanies] = useState<AdminCompanyMeta[]>([])
  const [providers, setProviders] = useState<AdminProviderMeta[]>([])
  const [companyDetail, setCompanyDetail] = useState<AdminCompanyDetail | null>(null)
  const [knowledgeEntries, setKnowledgeEntries] = useState<AdminKnowledgeEntry[]>([])
  const [busy, setBusy] = useState(false)
  const busyCountRef = useRef(0)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const abortControllersRef = useRef<Set<AbortController>>(new Set())

  useEffect(() => {
    return () => {
      for (const controller of abortControllersRef.current) {
        controller.abort()
      }
      abortControllersRef.current.clear()
    }
  }, [])

  useEffect(() => {
    const sync = companyDetail?.inventorySync
    if (!sync) {
      // Clear stale state when switching to a company without inventorySync,
      // but only if a company has actually been loaded (avoid resetting on mount).
      if (companyDetail) {
        setInventorySourceType('google_sheets')
        setInventorySourceUrl('')
        setInventoryConnectorId('')
        setInventorySheetName('')
        setRuntimeDataTier('admin')
        setInventoryDryRun(true)
        setInventoryAutoEnabled(false)
        setInventoryIntervalMinutes('60')
      }
      return
    }
    if (sync.source_type === 'google_sheets' || sync.source_type === 'mcp_connector') {
      setInventorySourceType(sync.source_type)
    }
    if (typeof sync.source_url === 'string') {
      setInventorySourceUrl(sync.source_url)
    }
    if (typeof sync.connector_id === 'string') {
      setInventoryConnectorId(sync.connector_id)
    }
    if (typeof sync.sheet_name === 'string') {
      setInventorySheetName(sync.sheet_name)
    }
    if (typeof sync.data_tier === 'string' && sync.data_tier.trim()) {
      setRuntimeDataTier(sync.data_tier.trim())
    }
    if (typeof sync.dry_run === 'boolean') {
      setInventoryDryRun(sync.dry_run)
    }
    if (typeof sync.auto_enabled === 'boolean') {
      setInventoryAutoEnabled(sync.auto_enabled)
    }
    if (typeof sync.interval_minutes === 'number' && Number.isFinite(sync.interval_minutes)) {
      setInventoryIntervalMinutes(
        String(Math.max(1, Math.min(1440, Math.round(sync.interval_minutes)))),
      )
    }
  }, [companyDetail])

  const adminHeaders = useMemo(() => {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'x-user-id': userId,
      'x-tenant-id': tenantId,
      'x-roles': 'tenant_admin',
    }
    if (adminKey.trim()) {
      headers['x-admin-key'] = adminKey.trim()
    }
    return headers
  }, [adminKey, tenantId, userId])

  async function callAdminJson<TPayload extends Record<string, unknown> | undefined>(
    url: string,
    options: {
      method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
      payload?: TPayload
      idempotencyPrefix?: string
    } = {},
  ): Promise<Record<string, unknown>> {
    const method = options.method ?? 'GET'
    const headers: Record<string, string> = { ...adminHeaders }
    if (options.idempotencyPrefix) {
      headers['Idempotency-Key'] = makeIdempotencyKey(options.idempotencyPrefix)
    }
    const controller = new AbortController()
    abortControllersRef.current.add(controller)
    const response = await fetch(withTenant(url, tenantId), {
      method,
      headers,
      body: options.payload ? JSON.stringify(options.payload) : undefined,
      signal: controller.signal,
    }).finally(() => {
      abortControllersRef.current.delete(controller)
    })
    const payload = await parseResponseJson(response)
    if (!response.ok) {
      const message =
        typeof payload.error === 'string' ? payload.error : `Request failed (${response.status})`
      throw new Error(message)
    }
    return payload
  }

  async function callAdminFormData(
    url: string,
    body: FormData,
    options: {
      method?: 'POST' | 'PUT'
      idempotencyPrefix?: string
    } = {},
  ): Promise<Record<string, unknown>> {
    const method = options.method ?? 'POST'
    const headers: Record<string, string> = {
      Accept: 'application/json',
      'x-user-id': userId,
      'x-tenant-id': tenantId,
      'x-roles': 'tenant_admin',
    }
    if (options.idempotencyPrefix) {
      headers['Idempotency-Key'] = makeIdempotencyKey(options.idempotencyPrefix)
    }
    if (adminKey.trim()) {
      headers['x-admin-key'] = adminKey.trim()
    }
    const controller = new AbortController()
    abortControllersRef.current.add(controller)
    const response = await fetch(withTenant(url, tenantId), {
      method,
      headers,
      body,
      signal: controller.signal,
    }).finally(() => {
      abortControllersRef.current.delete(controller)
    })
    const payload = await parseResponseJson(response)
    if (!response.ok) {
      const message =
        typeof payload.error === 'string' ? payload.error : `Request failed (${response.status})`
      throw new Error(message)
    }
    return payload
  }

  async function runAction(action: () => Promise<void>) {
    busyCountRef.current++
    setBusy(true)
    setErrorMessage(null)
    setStatusMessage(null)
    try {
      await action()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Action failed')
      throw error
    } finally {
      busyCountRef.current--
      setBusy(busyCountRef.current > 0)
    }
  }

  async function loadSnapshot() {
    await runAction(async () => {
      const [companiesPayload, providersPayload] = await Promise.all([
        callAdminJson('/api/v1/admin/companies'),
        callAdminJson('/api/v1/admin/mcp/providers'),
      ])
      const companiesList = (companiesPayload as AdminCompaniesResponse).companies
      const providersList = (providersPayload as AdminProvidersResponse).providers
      const safeCompanies = Array.isArray(companiesList) ? companiesList : []
      const safeProviders = Array.isArray(providersList) ? providersList : []
      setCompanies(safeCompanies)
      setProviders(safeProviders)

      setActiveCompanyId(prev => {
        if (!prev && safeCompanies.length > 0 && safeCompanies[0]?.id) {
          return safeCompanies[0].id
        }
        return prev
      })
      setStatusMessage('Snapshot loaded.')
    })
  }

  async function createCompany() {
    await runAction(async () => {
      await callAdminJson('/api/v1/admin/companies', {
        method: 'POST',
        idempotencyPrefix: 'admin-company-create',
        payload: {
          companyId,
          displayName,
          industryTemplateId: templateId,
          status: companyStatus,
          connectors: {},
        },
      })
      setActiveCompanyId(companyId)
      setStatusMessage(`Company saved: ${companyId}`)
      await loadSnapshot()
    })
  }

  async function loadCompanyDetail() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      const payload = (await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}`,
      )) as AdminCompanyResponse
      setCompanyDetail(payload.company ?? null)
      setStatusMessage(`Loaded company: ${activeCompanyId}`)
    })
  }

  async function loadKnowledge() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      const payload = (await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/knowledge`,
      )) as AdminKnowledgeResponse
      setKnowledgeEntries(Array.isArray(payload.entries) ? payload.entries : [])
      setStatusMessage(`Knowledge loaded for ${activeCompanyId}`)
    })
  }

  async function importKnowledgeText() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/knowledge/import-text`,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-knowledge-text',
          payload: {
            title: knowledgeTitle,
            text: knowledgeText,
            tags: parseCsv(knowledgeTags),
            source: 'text',
            url: knowledgeUrl,
          },
        },
      )
      setStatusMessage(`Knowledge text imported for ${activeCompanyId}`)
      await loadKnowledge()
    })
  }

  async function importKnowledgeUrl() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/knowledge/import-url`,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-knowledge-url',
          payload: {
            title: knowledgeTitle || knowledgeUrl,
            url: knowledgeUrl,
            tags: parseCsv(knowledgeTags),
            source: 'url',
          },
        },
      )
      setStatusMessage(`Knowledge URL imported for ${activeCompanyId}`)
      await loadKnowledge()
    })
  }

  async function importKnowledgeFile() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    if (!knowledgeFile) {
      setErrorMessage('Select a knowledge file first.')
      return
    }
    await runAction(async () => {
      const formData = new FormData()
      formData.append('file', knowledgeFile)
      formData.append('title', knowledgeTitle || knowledgeFile.name)
      formData.append('tags', knowledgeTags)
      formData.append('source', 'file')
      await callAdminFormData(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/knowledge/import-file`,
        formData,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-knowledge-file',
        },
      )
      setStatusMessage(`Knowledge file imported for ${activeCompanyId}`)
      setKnowledgeFile(null)
      await loadKnowledge()
    })
  }

  async function deleteKnowledge(knowledgeId: string) {
    if (!activeCompanyId.trim()) {
      setStatusMessage('No company selected')
      return
    }
    if (!knowledgeId.trim()) {
      setStatusMessage('Missing knowledge entry ID')
      return
    }
    await runAction(async () => {
      await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(
          activeCompanyId,
        )}/knowledge/${encodeURIComponent(knowledgeId)}`,
        {
          method: 'DELETE',
          idempotencyPrefix: 'admin-knowledge-delete',
        },
      )
      setStatusMessage(`Deleted knowledge: ${knowledgeId}`)
      await loadKnowledge()
    })
  }

  function resetConnectorForm() {
    setConnectorMode('create')
    setConnectorId('crm')
    setConnectorProvider('mock')
    setConnectorCapabilities('read')
    setConnectorSecretRef('')
    setConnectorEnabled(true)
  }

  function hydrateConnectorForm(entry: AdminConnectorEntry) {
    setConnectorMode('update')
    setConnectorId(entry.id)
    setConnectorProvider(typeof entry.provider === 'string' ? entry.provider : 'mock')
    setConnectorCapabilities(Array.isArray(entry.capabilities) ? entry.capabilities.join(', ') : '')
    setConnectorSecretRef(typeof entry.secret_ref === 'string' ? entry.secret_ref : '')
    setConnectorEnabled(entry.enabled !== false)
  }

  async function saveConnector() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      const connectorPayload = {
        connectorId,
        provider: connectorProvider,
        enabled: connectorEnabled,
        capabilities: parseCsv(connectorCapabilities),
        secretRef: connectorSecretRef || undefined,
        config: {},
      }
      if (connectorMode === 'update') {
        await callAdminJson(
          `/api/v1/admin/companies/${encodeURIComponent(
            activeCompanyId,
          )}/connectors/${encodeURIComponent(connectorId)}`,
          {
            method: 'PUT',
            idempotencyPrefix: 'admin-connector-update',
            payload: connectorPayload,
          },
        )
        setStatusMessage(`Connector updated: ${connectorId}`)
      } else {
        await callAdminJson(
          `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/connectors`,
          {
            method: 'POST',
            idempotencyPrefix: 'admin-connector-create',
            payload: connectorPayload,
          },
        )
        setStatusMessage(`Connector created: ${connectorId}`)
      }
      await loadCompanyDetail()
    })
  }

  async function testConnector(connectorName: string) {
    if (!activeCompanyId.trim()) {
      setStatusMessage('No company selected')
      return
    }
    await runAction(async () => {
      await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(
          activeCompanyId,
        )}/connectors/${encodeURIComponent(connectorName)}/test`,
        {
          method: 'POST',
        },
      )
      setStatusMessage(`Connector test executed: ${connectorName}`)
    })
  }

  async function deleteConnector(connectorName: string) {
    if (!activeCompanyId.trim()) {
      setStatusMessage('No company selected')
      return
    }
    await runAction(async () => {
      await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(
          activeCompanyId,
        )}/connectors/${encodeURIComponent(connectorName)}`,
        {
          method: 'DELETE',
          idempotencyPrefix: 'admin-connector-delete',
        },
      )
      setStatusMessage(`Connector deleted: ${connectorName}`)
      await loadCompanyDetail()
    })
  }

  function parseJsonArray(raw: string, field: string): Record<string, unknown>[] {
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      throw new Error(`${field} must be valid JSON`)
    }
    if (!Array.isArray(parsed)) {
      throw new Error(`${field} must be a JSON array`)
    }
    return parsed as Record<string, unknown>[]
  }

  async function importProducts() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      const products = parseJsonArray(productsJson, 'Products payload')
      const payload = await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/products/import`,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-products-import',
          payload: { products, data_tier: runtimeDataTier },
        },
      )
      setStatusMessage(`Products import: ${String(payload.written ?? 0)} written`)
    })
  }

  async function importBookingSlots() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      const slots = parseJsonArray(slotsJson, 'Booking slots payload')
      const payload = await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/booking-slots/import`,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-slots-import',
          payload: { slots, data_tier: runtimeDataTier },
        },
      )
      setStatusMessage(`Booking slots import: ${String(payload.written ?? 0)} written`)
    })
  }

  async function purgeDemoData() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/runtime/purge-demo`,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-runtime-purge',
        },
      )
      setStatusMessage(`Purged demo runtime data for ${activeCompanyId}`)
    })
  }

  async function syncInventorySource() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      const payload = await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/inventory/sync`,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-inventory-sync',
          payload: {
            sourceType: inventorySourceType,
            sourceUrl: inventorySourceType === 'google_sheets' ? inventorySourceUrl : undefined,
            connectorId: inventorySourceType === 'mcp_connector' ? inventoryConnectorId : undefined,
            sheetName: inventorySheetName || undefined,
            dataTier: runtimeDataTier,
            dryRun: inventoryDryRun,
          },
        },
      )
      setStatusMessage(`Inventory sync: ${String(payload.written ?? 0)} written`)
      await loadCompanyDetail()
    })
  }

  async function uploadInventoryFile() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    if (!inventoryFile) {
      setErrorMessage('Select a CSV or XLSX inventory file first.')
      return
    }
    await runAction(async () => {
      const formData = new FormData()
      formData.append('file', inventoryFile)
      formData.append('data_tier', runtimeDataTier)
      formData.append('dry_run', String(inventoryDryRun))
      if (inventorySheetName.trim()) {
        formData.append('sheet_name', inventorySheetName.trim())
      }
      const payload = await callAdminFormData(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/inventory/upload`,
        formData,
        {
          method: 'POST',
          idempotencyPrefix: 'admin-inventory-upload',
        },
      )
      setStatusMessage(`Inventory upload: ${String(payload.written ?? 0)} written`)
      setInventoryFile(null)
      await loadCompanyDetail()
    })
  }

  async function saveInventorySyncConfig() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    const parsedInterval = Number.parseInt(inventoryIntervalMinutes.trim(), 10)
    const intervalMinutes = Number.isFinite(parsedInterval)
      ? Math.max(1, Math.min(1440, parsedInterval))
      : 15

    await runAction(async () => {
      const payload = await callAdminJson(
        `/api/v1/admin/companies/${encodeURIComponent(activeCompanyId)}/inventory/sync/config`,
        {
          method: 'PUT',
          idempotencyPrefix: 'admin-inventory-config',
          payload: {
            sourceType: inventorySourceType,
            sourceUrl: inventorySourceType === 'google_sheets' ? inventorySourceUrl : undefined,
            connectorId: inventorySourceType === 'mcp_connector' ? inventoryConnectorId : undefined,
            sheetName: inventorySheetName || undefined,
            dataTier: runtimeDataTier,
            dryRun: inventoryDryRun,
            autoEnabled: inventoryAutoEnabled,
            intervalMinutes,
          },
        },
      )
      setStatusMessage(
        `Inventory sync config saved. Auto=${String(
          (payload.inventorySync as Record<string, unknown> | undefined)?.auto_enabled ??
            inventoryAutoEnabled,
        )} interval=${String(
          (payload.inventorySync as Record<string, unknown> | undefined)?.interval_minutes ??
            intervalMinutes,
        )}m`,
      )
      setInventoryIntervalMinutes(String(intervalMinutes))
      await loadCompanyDetail()
    })
  }

  async function runInventorySyncJobs() {
    if (!activeCompanyId.trim()) {
      setErrorMessage('Active company id is required.')
      return
    }
    await runAction(async () => {
      const payload = await callAdminJson('/api/v1/admin/inventory/sync/run', {
        method: 'POST',
        idempotencyPrefix: 'admin-inventory-run',
        payload: {
          companyId: activeCompanyId,
          maxCompanies: 1,
          force: inventoryRunForce,
          dryRunOverride: inventoryDryRun,
        },
      })
      setStatusMessage(
        `Inventory run: ${String(payload.triggered ?? 0)} triggered, ${String(payload.skipped ?? 0)} skipped`,
      )
      await loadCompanyDetail()
    })
  }

  const connectorList = useMemo(() => {
    const map = companyDetail?.connectors
    if (!map || typeof map !== 'object') return []
    return Object.entries(map).map(([key, value]) => ({
      ...(value ?? {}),
      id: key,
    }))
  }, [companyDetail?.connectors])

  return (
    <main className="min-h-screen bg-background text-foreground">
      <NavBar activePage="admin" />
      <section className="mx-auto flex w-full max-w-6xl flex-col gap-5 rounded-2xl border border-border/70 bg-card/60 p-5 px-4 py-6 sm:p-7 sm:px-8">
        <header className="space-y-2">
          <p className="text-[0.65rem] text-primary uppercase tracking-[0.25em]">Admin</p>
          <h1 className="font-display text-2xl text-white sm:text-3xl">Ekaette Admin Console</h1>
          <p className="text-muted-foreground text-sm">
            Manage tenant setup, knowledge grounding, connector policy, and runtime catalog data.
          </p>
        </header>

        <section className="grid gap-3 rounded-2xl border border-border/70 bg-background/40 p-4 sm:grid-cols-4">
          <label className="flex flex-col gap-1 text-muted-foreground text-xs">
            Tenant ID
            <input
              value={tenantId}
              onChange={event => setTenantId(event.target.value)}
              className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-muted-foreground text-xs">
            User ID
            <input
              value={userId}
              onChange={event => setUserId(event.target.value)}
              className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-muted-foreground text-xs">
            Admin Key
            <input
              type="password"
              value={adminKey}
              onChange={event => setAdminKey(event.target.value)}
              placeholder="Required when backend enforces ADMIN_SHARED_SECRET"
              className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-muted-foreground text-xs">
            Active Company ID
            <input
              value={activeCompanyId}
              onChange={event => setActiveCompanyId(event.target.value)}
              className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
            />
          </label>
          <div className="flex flex-wrap gap-2 sm:col-span-4">
            <button
              type="button"
              onClick={loadSnapshot}
              disabled={busy}
              className="rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
            >
              Load Snapshot
            </button>
            <button
              type="button"
              onClick={loadCompanyDetail}
              disabled={busy}
              className="rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm disabled:opacity-50"
            >
              Load Company Detail
            </button>
            <button
              type="button"
              onClick={loadKnowledge}
              disabled={busy}
              className="rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm disabled:opacity-50"
            >
              Load Knowledge
            </button>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <article className="rounded-2xl border border-border/70 bg-background/40 p-4">
            <h2 className="font-semibold text-white">Create Company</h2>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-muted-foreground text-xs">
                Company ID
                <input
                  value={companyId}
                  onChange={event => setCompanyId(event.target.value)}
                  className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                />
              </label>
              <label className="flex flex-col gap-1 text-muted-foreground text-xs">
                Template ID
                <input
                  value={templateId}
                  onChange={event => setTemplateId(event.target.value)}
                  className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                />
              </label>
              <label className="flex flex-col gap-1 text-muted-foreground text-xs sm:col-span-2">
                Display Name
                <input
                  value={displayName}
                  onChange={event => setDisplayName(event.target.value)}
                  className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                />
              </label>
              <label className="flex flex-col gap-1 text-muted-foreground text-xs">
                Status
                <input
                  value={companyStatus}
                  onChange={event => setCompanyStatus(event.target.value)}
                  className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                />
              </label>
            </div>
            <button
              type="button"
              onClick={createCompany}
              disabled={busy}
              className="mt-3 rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
            >
              Save Company
            </button>
          </article>

          <article className="rounded-2xl border border-border/70 bg-background/40 p-4">
            <h2 className="font-semibold text-white">Knowledge</h2>
            <div className="mt-3 grid gap-2">
              <input
                value={knowledgeTitle}
                onChange={event => setKnowledgeTitle(event.target.value)}
                placeholder="Knowledge title"
                className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
              />
              <textarea
                value={knowledgeText}
                onChange={event => setKnowledgeText(event.target.value)}
                placeholder="Knowledge text"
                className="min-h-24 rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
              />
              <input
                value={knowledgeUrl}
                onChange={event => setKnowledgeUrl(event.target.value)}
                placeholder="Optional URL"
                className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
              />
              <input
                value={knowledgeTags}
                onChange={event => setKnowledgeTags(event.target.value)}
                placeholder="tag1, tag2"
                className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={importKnowledgeText}
                disabled={busy}
                className="rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
              >
                Import Text
              </button>
              <button
                type="button"
                onClick={importKnowledgeUrl}
                disabled={busy}
                className="rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm disabled:opacity-50"
              >
                Import URL
              </button>
              <label className="cursor-pointer rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm">
                Choose File
                <input
                  type="file"
                  aria-label="Knowledge file"
                  className="sr-only"
                  onChange={event => {
                    const selected =
                      event.target.files && event.target.files.length > 0
                        ? event.target.files[0]
                        : null
                    setKnowledgeFile(selected ?? null)
                  }}
                />
              </label>
              <button
                type="button"
                onClick={importKnowledgeFile}
                disabled={busy}
                className="rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm disabled:opacity-50"
              >
                Import File
              </button>
            </div>
            {knowledgeFile ? (
              <p className="mt-2 text-muted-foreground text-xs">
                Selected file: {knowledgeFile.name}
              </p>
            ) : null}
          </article>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <article className="rounded-2xl border border-border/70 bg-background/40 p-4">
            <h2 className="font-semibold text-white">Connectors</h2>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <input
                value={connectorId}
                onChange={event => setConnectorId(event.target.value)}
                placeholder="Connector ID"
                className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
              />
              <input
                value={connectorProvider}
                onChange={event => setConnectorProvider(event.target.value)}
                placeholder="Provider"
                className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
              />
              <input
                value={connectorCapabilities}
                onChange={event => setConnectorCapabilities(event.target.value)}
                placeholder="read, write"
                className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm sm:col-span-2"
              />
              <input
                value={connectorSecretRef}
                onChange={event => setConnectorSecretRef(event.target.value)}
                placeholder="secret ref (optional for mock)"
                className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm sm:col-span-2"
              />
              <label className="flex items-center gap-2 text-muted-foreground text-xs sm:col-span-2">
                <input
                  type="checkbox"
                  checked={connectorEnabled}
                  onChange={event => setConnectorEnabled(event.target.checked)}
                />
                Enabled
              </label>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={saveConnector}
                disabled={busy}
                className="rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
              >
                {connectorMode === 'update' ? 'Update Connector' : 'Create Connector'}
              </button>
              <button
                type="button"
                onClick={resetConnectorForm}
                disabled={busy}
                className="rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm disabled:opacity-50"
              >
                Reset Connector Form
              </button>
            </div>
          </article>

          <article className="rounded-2xl border border-border/70 bg-background/40 p-4">
            <h2 className="font-semibold text-white">Runtime Imports</h2>
            <div className="mt-3 grid gap-2">
              <label className="flex flex-col gap-1 text-muted-foreground text-xs">
                Data Tier
                <select
                  value={runtimeDataTier}
                  onChange={event => setRuntimeDataTier(event.target.value)}
                  className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                >
                  <option value="admin">admin</option>
                  <option value="demo">demo</option>
                  <option value="seed">seed</option>
                </select>
              </label>
              <textarea
                value={productsJson}
                onChange={event => setProductsJson(event.target.value)}
                className="min-h-28 rounded-xl border border-border bg-background px-3 py-2 text-foreground text-xs"
              />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={importProducts}
                  disabled={busy}
                  className="rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
                >
                  Import Products
                </button>
              </div>

              <textarea
                value={slotsJson}
                onChange={event => setSlotsJson(event.target.value)}
                className="min-h-28 rounded-xl border border-border bg-background px-3 py-2 text-foreground text-xs"
              />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={importBookingSlots}
                  disabled={busy}
                  className="rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
                >
                  Import Slots
                </button>
                <button
                  type="button"
                  onClick={purgeDemoData}
                  disabled={busy}
                  className="rounded-full border border-red-500/60 bg-red-500/10 px-4 py-2 font-semibold text-red-300 text-sm disabled:opacity-50"
                >
                  Purge Demo Data
                </button>
              </div>

              <div className="mt-4 rounded-xl border border-border/70 bg-card/30 p-3">
                <p className="font-semibold text-foreground text-xs">Inventory Sync</p>
                <p className="mt-1 text-[0.72rem] text-muted-foreground">
                  Sync from Google Sheets link, connector payload, or uploaded CSV/XLSX.
                </p>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <label className="flex flex-col gap-1 text-muted-foreground text-xs">
                    Source Type
                    <select
                      value={inventorySourceType}
                      onChange={event =>
                        setInventorySourceType(
                          event.target.value as 'google_sheets' | 'mcp_connector',
                        )
                      }
                      className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                    >
                      <option value="google_sheets">google_sheets</option>
                      <option value="mcp_connector">mcp_connector</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-muted-foreground text-xs">
                    Sheet Name (optional)
                    <input
                      value={inventorySheetName}
                      onChange={event => setInventorySheetName(event.target.value)}
                      placeholder="Sheet1"
                      className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                    />
                  </label>
                  {inventorySourceType === 'google_sheets' ? (
                    <label className="flex flex-col gap-1 text-muted-foreground text-xs sm:col-span-2">
                      Google Sheets URL
                      <input
                        value={inventorySourceUrl}
                        onChange={event => setInventorySourceUrl(event.target.value)}
                        placeholder="https://docs.google.com/spreadsheets/d/<id>/edit#gid=0"
                        className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                      />
                    </label>
                  ) : (
                    <label className="flex flex-col gap-1 text-muted-foreground text-xs sm:col-span-2">
                      Connector ID
                      <input
                        value={inventoryConnectorId}
                        onChange={event => setInventoryConnectorId(event.target.value)}
                        placeholder="inventory"
                        className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                      />
                    </label>
                  )}
                  <label className="flex items-center gap-2 text-muted-foreground text-xs sm:col-span-2">
                    <input
                      type="checkbox"
                      checked={inventoryDryRun}
                      onChange={event => setInventoryDryRun(event.target.checked)}
                    />
                    Dry run (validate only, do not write)
                  </label>
                  <label className="flex items-center gap-2 text-muted-foreground text-xs sm:col-span-2">
                    <input
                      type="checkbox"
                      checked={inventoryAutoEnabled}
                      onChange={event => setInventoryAutoEnabled(event.target.checked)}
                    />
                    Enable auto sync schedule
                  </label>
                  <label className="flex flex-col gap-1 text-muted-foreground text-xs">
                    Interval Minutes
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={inventoryIntervalMinutes}
                      onChange={event => setInventoryIntervalMinutes(event.target.value)}
                      className="rounded-xl border border-border bg-background px-3 py-2 text-foreground text-sm"
                    />
                  </label>
                  <label className="flex items-center gap-2 self-end text-muted-foreground text-xs">
                    <input
                      type="checkbox"
                      checked={inventoryRunForce}
                      onChange={event => setInventoryRunForce(event.target.checked)}
                    />
                    Force run now
                  </label>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={syncInventorySource}
                    disabled={busy}
                    className="rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
                  >
                    Sync Inventory Source
                  </button>
                  <label className="cursor-pointer rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm">
                    Choose Inventory File
                    <input
                      type="file"
                      aria-label="Inventory file"
                      accept=".csv,.xlsx"
                      className="sr-only"
                      onChange={event => {
                        const selected =
                          event.target.files && event.target.files.length > 0
                            ? event.target.files[0]
                            : null
                        setInventoryFile(selected ?? null)
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    onClick={uploadInventoryFile}
                    disabled={busy}
                    className="rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm disabled:opacity-50"
                  >
                    Upload Inventory File
                  </button>
                  <button
                    type="button"
                    onClick={saveInventorySyncConfig}
                    disabled={busy}
                    className="rounded-full border border-primary/60 bg-primary/10 px-4 py-2 font-semibold text-primary text-sm disabled:opacity-50"
                  >
                    Save Sync Config
                  </button>
                  <button
                    type="button"
                    onClick={runInventorySyncJobs}
                    disabled={busy}
                    className="rounded-full border border-border bg-background px-4 py-2 font-semibold text-foreground text-sm disabled:opacity-50"
                  >
                    Run Sync Jobs
                  </button>
                </div>
                {inventoryFile ? (
                  <p className="mt-2 text-muted-foreground text-xs">
                    Selected inventory file: {inventoryFile.name}
                  </p>
                ) : null}
                {companyDetail?.inventorySync ? (
                  <p className="mt-2 text-muted-foreground text-xs">
                    Last sync status: {companyDetail.inventorySync.status ?? 'unknown'} · updated{' '}
                    {companyDetail.inventorySync.updated_at ?? 'n/a'} · written{' '}
                    {String(companyDetail.inventorySync.last_result?.written ?? 0)} · next run{' '}
                    {companyDetail.inventorySync.next_run_at ?? 'n/a'} · auto{' '}
                    {String(companyDetail.inventorySync.auto_enabled ?? false)}
                  </p>
                ) : null}
              </div>
            </div>
          </article>
        </section>

        {statusMessage ? (
          <p aria-live="polite" className="text-emerald-400 text-sm">
            {statusMessage}
          </p>
        ) : null}
        {errorMessage ? (
          <p aria-live="assertive" className="text-red-400 text-sm">
            {errorMessage}
          </p>
        ) : null}

        <section className="grid gap-4 lg:grid-cols-3">
          <article className="rounded-2xl border border-border/70 bg-background/40 p-4">
            <h2 className="font-semibold text-white">Companies ({companies.length})</h2>
            <ul className="mt-3 space-y-2 text-sm">
              {companies.map(company => (
                <li key={company.id} className="rounded-xl border border-border/70 px-3 py-2">
                  <p className="font-medium text-foreground">{company.displayName ?? company.id}</p>
                  <p className="text-muted-foreground text-xs">
                    {company.id} · {company.templateId ?? 'n/a'} · {company.status ?? 'unknown'}
                  </p>
                </li>
              ))}
            </ul>
          </article>

          <article className="rounded-2xl border border-border/70 bg-background/40 p-4">
            <h2 className="font-semibold text-white">Knowledge ({knowledgeEntries.length})</h2>
            <ul className="mt-3 space-y-2 text-sm">
              {knowledgeEntries.map(entry => (
                <li key={entry.id} className="rounded-xl border border-border/70 px-3 py-2">
                  <p className="font-medium text-foreground">{entry.title ?? entry.id}</p>
                  <p className="text-muted-foreground text-xs">
                    {entry.source ?? 'unknown source'}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      void deleteKnowledge(entry.id)
                    }}
                    className="mt-2 rounded-full border border-red-500/50 bg-red-500/10 px-3 py-1 font-semibold text-red-300 text-xs"
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          </article>

          <article className="rounded-2xl border border-border/70 bg-background/40 p-4">
            <h2 className="font-semibold text-white">
              Connectors ({connectorList.length}) · Providers ({providers.length})
            </h2>
            <ul className="mt-3 space-y-2 text-sm">
              {connectorList.map(entry => (
                <li key={entry.id} className="rounded-xl border border-border/70 px-3 py-2">
                  <p className="font-medium text-foreground">{entry.id}</p>
                  <p className="text-muted-foreground text-xs">
                    {entry.provider ?? 'unknown'} ·{' '}
                    {(entry.capabilities ?? []).join(', ') || 'none'}
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        hydrateConnectorForm(entry)
                      }}
                      className="rounded-full border border-border bg-background px-3 py-1 font-semibold text-foreground text-xs"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void testConnector(entry.id)
                      }}
                      className="rounded-full border border-border bg-background px-3 py-1 font-semibold text-foreground text-xs"
                    >
                      Test
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void deleteConnector(entry.id)
                      }}
                      className="rounded-full border border-red-500/50 bg-red-500/10 px-3 py-1 font-semibold text-red-300 text-xs"
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </article>
        </section>
      </section>
    </main>
  )
}
