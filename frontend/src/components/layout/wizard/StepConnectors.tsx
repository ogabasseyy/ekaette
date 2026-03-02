import { useCallback, useEffect, useState } from 'react'
import { cva } from 'class-variance-authority'
import type { AdminConnectorEntry, AdminCompanyDetail } from './useWizardApi'
import { useWizardApi } from './useWizardApi'
import { cn } from '../../../lib/utils'

// ── Backend provider shape (from GET /api/v1/admin/mcp/providers) ──

interface ProviderEntry {
  id: string
  label: string
  status: string
  requiresSecretRef: boolean
  capabilities: string[]
}

// ── Display metadata for known providers (frontend-only) ──

const PROVIDER_DISPLAY: Record<string, { icon: string; description: string; category: string }> = {
  salesforce: { icon: '☁️', description: 'Sync contacts, leads, and deals', category: 'CRM' },
  hubspot: { icon: '🔶', description: 'Marketing, sales, and service hub', category: 'CRM' },
  zendesk: { icon: '🎧', description: 'Customer support ticketing', category: 'CRM' },
  mock: { icon: '🧪', description: 'Test connector for development', category: 'Testing' },
}

const DEFAULT_DISPLAY = { icon: '🔌', description: 'MCP provider', category: 'Integration' }

// ── Card status variants (CVA) ──

type CardStatus = 'available' | 'connecting' | 'connected' | 'testing' | 'error'

const connectorCardVariants = cva(
  'rounded-2xl border px-4 py-4 text-left transition cursor-pointer',
  {
    variants: {
      status: {
        available: 'border-border/70 bg-card/40 hover:border-primary/40',
        connected: 'border-emerald-500/50 bg-emerald-500/5',
        connecting: 'border-primary/60 bg-primary/5 animate-pulse',
        testing: 'border-primary/60 bg-primary/5 animate-pulse',
        error: 'border-destructive/50 bg-destructive/5',
      },
    },
    defaultVariants: { status: 'available' },
  },
)

// ── Helpers ──

function connectorIdForProvider(providerId: string): string {
  if (providerId === 'mock') return 'mock-provider'
  return `crm-${providerId}`
}

function resolveStatus(
  providerId: string,
  connectors: AdminConnectorEntry[],
  actionStates: Record<string, { status: CardStatus; message: string | null }>,
): { status: CardStatus; message: string | null } {
  const cid = connectorIdForProvider(providerId)
  const action = actionStates[cid]
  if (action && (action.status === 'connecting' || action.status === 'testing')) return action
  const existing = connectors.find(c => c.id === cid || c.provider === providerId)
  if (existing) return action ?? { status: 'connected', message: null }
  if (action?.status === 'error') return action
  return { status: 'available', message: null }
}

// ── Component ──

interface StepConnectorsProps {
  companyId: string
  tenantId: string
  onNext: () => void
  onBack: () => void
}

export function StepConnectors({ companyId, tenantId, onNext, onBack }: StepConnectorsProps) {
  const [providers, setProviders] = useState<ProviderEntry[]>([])
  const [connectors, setConnectors] = useState<AdminConnectorEntry[]>([])
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [secretRef, setSecretRef] = useState('')
  const [customUrl, setCustomUrl] = useState('')
  const [customProviderId, setCustomProviderId] = useState('')
  const [customSecret, setCustomSecret] = useState('')
  const [actionStates, setActionStates] = useState<Record<string, { status: CardStatus; message: string | null }>>({})
  const api = useWizardApi({ tenantId })
  const companyUrl = `/api/v1/admin/companies/${encodeURIComponent(companyId)}`

  // Load provider catalog
  const loadProviders = useCallback(async () => {
    try {
      const payload = await api.callJson('/api/v1/admin/mcp/providers')
      if (Array.isArray(payload.providers)) setProviders(payload.providers as ProviderEntry[])
    } catch {
      /* non-blocking — cards will be empty */
    }
  }, [api])

  // Load existing connectors from company detail
  const loadConnectors = useCallback(async () => {
    try {
      const payload = await api.callJson(companyUrl)
      const detail = payload.company as AdminCompanyDetail | undefined
      const map = detail?.connectors ?? {}
      setConnectors(Object.entries(map).map(([id, entry]) => ({ id, ...entry })))
    } catch {
      /* non-blocking */
    }
  }, [api, companyUrl])

  useEffect(() => {
    void loadProviders()
    void loadConnectors()
  }, [loadProviders, loadConnectors])

  // Connect a catalog provider
  const connectProvider = useCallback(
    async (provider: ProviderEntry, secret?: string) => {
      const cid = connectorIdForProvider(provider.id)
      setActionStates(prev => ({ ...prev, [cid]: { status: 'connecting', message: null } }))
      await api.runAction(async () => {
        await api.callJson(`${companyUrl}/connectors`, {
          method: 'POST',
          idempotencyPrefix: 'wizard-connector-create',
          payload: {
            connectorId: cid,
            provider: provider.id,
            enabled: true,
            capabilities: provider.capabilities,
            ...(secret ? { secretRef: secret } : {}),
          },
        })
        setActionStates(prev => ({ ...prev, [cid]: { status: 'connected', message: 'Connected' } }))
        setExpandedId(null)
        setSecretRef('')
        await loadConnectors()
      })
      // On error, api.runAction sets api.error and we reflect it
      if (api.error) {
        setActionStates(prev => ({ ...prev, [cid]: { status: 'error', message: api.error } }))
      }
    },
    [api, companyUrl, loadConnectors],
  )

  // Connect custom MCP server
  const connectCustom = useCallback(async () => {
    const pid = customProviderId.trim() || 'custom'
    const cid = `custom-${pid}`
    setActionStates(prev => ({ ...prev, [cid]: { status: 'connecting', message: null } }))
    await api.runAction(async () => {
      await api.callJson(`${companyUrl}/connectors`, {
        method: 'POST',
        idempotencyPrefix: 'wizard-connector-create',
        payload: {
          connectorId: cid,
          provider: pid,
          enabled: true,
          capabilities: ['read', 'write'],
          config: { endpoint: customUrl.trim() },
          ...(customSecret ? { secretRef: customSecret } : {}),
        },
      })
      setActionStates(prev => ({ ...prev, [cid]: { status: 'connected', message: 'Connected' } }))
      setExpandedId(null)
      setCustomUrl('')
      setCustomProviderId('')
      setCustomSecret('')
      await loadConnectors()
    })
  }, [api, companyUrl, customProviderId, customSecret, customUrl, loadConnectors])

  // Test a connected connector
  const testConnector = useCallback(
    async (connectorId: string) => {
      setActionStates(prev => ({ ...prev, [connectorId]: { status: 'testing', message: null } }))
      await api.runAction(async () => {
        const result = await api.callJson(
          `${companyUrl}/connectors/${encodeURIComponent(connectorId)}/test`,
          { method: 'POST', idempotencyPrefix: 'wizard-connector-test' },
        )
        const ok = result.ok === true
        setActionStates(prev => ({
          ...prev,
          [connectorId]: {
            status: ok ? 'connected' : 'error',
            message: ok ? 'Test passed' : String(result.details ?? 'Test failed'),
          },
        }))
      })
    },
    [api, companyUrl],
  )

  // Remove a connector
  const removeConnector = useCallback(
    async (connectorId: string) => {
      await api.runAction(async () => {
        await api.callJson(
          `${companyUrl}/connectors/${encodeURIComponent(connectorId)}`,
          { method: 'DELETE', idempotencyPrefix: 'wizard-connector-delete' },
        )
        setActionStates(prev => {
          const next = { ...prev }
          delete next[connectorId]
          return next
        })
        await loadConnectors()
      })
    },
    [api, companyUrl, loadConnectors],
  )

  // Handle card click
  const handleCardClick = useCallback(
    (provider: ProviderEntry) => {
      const cid = connectorIdForProvider(provider.id)
      const resolved = resolveStatus(provider.id, connectors, actionStates)
      if (resolved.status === 'connected') return // already connected, use Test/Remove
      if (resolved.status === 'connecting' || resolved.status === 'testing') return // busy

      if (!provider.requiresSecretRef) {
        // No secret needed — connect immediately
        void connectProvider(provider)
      } else {
        // Toggle inline config panel
        setExpandedId(prev => (prev === cid ? null : cid))
        setSecretRef('')
      }
    },
    [actionStates, connectProvider, connectors],
  )

  return (
    <>
      <div className="mt-5 space-y-4">
        <div>
          <h2 className="font-semibold text-white">Integrations</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Connect your tools or MCP servers. You can always add these later.
          </p>
        </div>

        {api.error ? (
          <p className="text-xs text-destructive" role="alert">{api.error}</p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2" role="list" aria-label="Available integrations">
          {providers.map(provider => {
            const cid = connectorIdForProvider(provider.id)
            const display = PROVIDER_DISPLAY[provider.id] ?? DEFAULT_DISPLAY
            const resolved = resolveStatus(provider.id, connectors, actionStates)
            const isExpanded = expandedId === cid
            const isConnected = resolved.status === 'connected'
            const CardEl = isConnected ? 'div' : 'button'

            return (
              <div key={provider.id} className="contents" role="listitem">
                <CardEl
                  {...(!isConnected ? { type: 'button' as const } : {})}
                  aria-expanded={isExpanded}
                  onClick={!isConnected ? () => handleCardClick(provider) : undefined}
                  className={cn(
                    connectorCardVariants({ status: isExpanded ? 'connecting' : resolved.status }),
                    isConnected && 'cursor-default',
                  )}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-lg" aria-hidden>{display.icon}</span>
                      <div>
                        <p className="font-semibold text-white text-sm">{provider.label}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">{display.description}</p>
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full bg-card/60 px-2 py-0.5 text-[0.6rem] uppercase tracking-wider text-muted-foreground">
                      {display.category}
                    </span>
                  </div>

                  <div className="mt-3 flex items-center justify-between">
                    {isConnected ? (
                      <span className="flex items-center gap-1 text-xs text-emerald-400">
                        ✓ Connected
                      </span>
                    ) : resolved.status === 'connecting' || resolved.status === 'testing' ? (
                      <span className="inline-flex items-center gap-1.5 text-xs text-primary">
                        <span className="size-3 animate-spin rounded-full border border-primary/30 border-t-primary" />
                        {resolved.status === 'testing' ? 'Testing...' : 'Connecting...'}
                      </span>
                    ) : resolved.status === 'error' ? (
                      <span className="text-xs text-destructive">{resolved.message}</span>
                    ) : (
                      <span className="text-xs font-medium text-primary">Connect</span>
                    )}

                    {isConnected ? (
                      <span className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => testConnector(cid)}
                          className="text-xs text-primary/70 transition hover:text-primary"
                        >
                          Test
                        </button>
                        <button
                          type="button"
                          onClick={() => removeConnector(cid)}
                          className="text-xs text-destructive/70 transition hover:text-destructive"
                        >
                          Remove
                        </button>
                      </span>
                    ) : null}
                  </div>
                </CardEl>

                {/* Inline config panel (secret entry) */}
                {isExpanded && provider.requiresSecretRef ? (
                  <div className="connector-config-panel col-span-full rounded-xl border border-primary/30 bg-card/40 p-4 space-y-3 sm:col-span-2">
                    <p className="text-xs text-muted-foreground">
                      Enter your {provider.label} API key to connect.
                    </p>
                    <input
                      type="password"
                      aria-label={`${provider.label} API secret`}
                      value={secretRef}
                      onChange={e => setSecretRef(e.target.value)}
                      placeholder="API Key / Secret Reference"
                      className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white placeholder:text-muted-foreground/50 outline-none focus:border-primary/60"
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={api.busy || !secretRef.trim()}
                        onClick={() => connectProvider(provider, secretRef.trim())}
                        className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary transition hover:bg-primary/15 disabled:opacity-50"
                      >
                        Save
                      </button>
                      <button
                        type="button"
                        onClick={() => { setExpandedId(null); setSecretRef('') }}
                        className="rounded-full border border-border/50 bg-card/40 px-4 py-1.5 text-xs text-muted-foreground transition hover:text-white"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            )
          })}

          {/* Custom MCP Server card */}
          <div className="contents" role="listitem">
            <button
              type="button"
              aria-expanded={expandedId === 'custom-mcp'}
              onClick={() => {
                setExpandedId(prev => (prev === 'custom-mcp' ? null : 'custom-mcp'))
                setCustomUrl('')
                setCustomProviderId('')
                setCustomSecret('')
              }}
              className={cn(
                connectorCardVariants({ status: expandedId === 'custom-mcp' ? 'connecting' : 'available' }),
              )}
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-lg" aria-hidden>🔗</span>
                  <div>
                    <p className="font-semibold text-white text-sm">Custom MCP Server</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">Connect any MCP-compatible server</p>
                  </div>
                </div>
                <span className="shrink-0 rounded-full bg-card/60 px-2 py-0.5 text-[0.6rem] uppercase tracking-wider text-muted-foreground">
                  MCP
                </span>
              </div>
              <div className="mt-3">
                <span className="text-xs font-medium text-primary">Configure</span>
              </div>
            </button>

            {expandedId === 'custom-mcp' ? (
              <div className="connector-config-panel col-span-full rounded-xl border border-primary/30 bg-card/40 p-4 space-y-3 sm:col-span-2">
                <p className="text-xs text-muted-foreground">
                  Connect to a custom MCP server by providing its URL.
                </p>
                <input
                  type="url"
                  aria-label="Server URL"
                  value={customUrl}
                  onChange={e => setCustomUrl(e.target.value)}
                  placeholder="https://mcp.example.com/v1"
                  className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white placeholder:text-muted-foreground/50 outline-none focus:border-primary/60"
                />
                <input
                  type="text"
                  aria-label="Provider ID"
                  value={customProviderId}
                  onChange={e => setCustomProviderId(e.target.value)}
                  placeholder="Provider ID (e.g. my-crm)"
                  className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white placeholder:text-muted-foreground/50 outline-none focus:border-primary/60"
                />
                <input
                  type="password"
                  aria-label="API key (optional)"
                  value={customSecret}
                  onChange={e => setCustomSecret(e.target.value)}
                  placeholder="API Key (optional)"
                  className="w-full rounded-xl border border-border/70 bg-card/60 px-3 py-2 text-sm text-white placeholder:text-muted-foreground/50 outline-none focus:border-primary/60"
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={api.busy || !customUrl.trim()}
                    onClick={connectCustom}
                    className="rounded-full border border-primary/50 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary transition hover:bg-primary/15 disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={() => setExpandedId(null)}
                    className="rounded-full border border-border/50 bg-card/40 px-4 py-1.5 text-xs text-muted-foreground transition hover:text-white"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          {/* Show existing custom/unknown connectors */}
          {connectors
            .filter(c => !providers.some(p => connectorIdForProvider(p.id) === c.id))
            .map(entry => (
              <div key={entry.id} className="contents" role="listitem">
                <div className={connectorCardVariants({ status: 'connected' })}>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-lg" aria-hidden>🔌</span>
                      <div>
                        <p className="font-semibold text-white text-sm">{entry.id}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">{entry.provider ?? 'custom'}</p>
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full bg-card/60 px-2 py-0.5 text-[0.6rem] uppercase tracking-wider text-muted-foreground">
                      Custom
                    </span>
                  </div>
                  <div className="mt-3 flex items-center justify-between">
                    <span className="flex items-center gap-1 text-xs text-emerald-400">✓ Connected</span>
                    <span className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => testConnector(entry.id)}
                        className="text-xs text-primary/70 transition hover:text-primary"
                      >
                        Test
                      </button>
                      <button
                        type="button"
                        onClick={() => removeConnector(entry.id)}
                        className="text-xs text-destructive/70 transition hover:text-destructive"
                      >
                        Remove
                      </button>
                    </span>
                  </div>
                </div>
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
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onNext}
            className="rounded-full border border-border/50 bg-card/40 px-5 py-2 text-sm text-muted-foreground transition hover:text-white"
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
