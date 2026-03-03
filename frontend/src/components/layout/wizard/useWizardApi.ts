import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

// ── Shared helpers (extracted from AdminDashboard patterns) ──

export function makeIdempotencyKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${crypto.randomUUID().slice(0, 12)}`
}

export function parseCsv(raw: string): string[] {
  return raw
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
}

export function withTenant(url: string, tenantId: string): string {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}tenantId=${encodeURIComponent(tenantId)}`
}

async function parseResponseJson(response: Response): Promise<Record<string, unknown>> {
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) return {}
  return (await response.json()) as Record<string, unknown>
}

// ── Admin API types ──

export interface AdminKnowledgeEntry {
  id: string
  title?: string
  text?: string
  tags?: string[]
  source?: string
}

export interface AdminConnectorEntry {
  id: string
  provider?: string
  enabled?: boolean
  capabilities?: string[]
  secret_ref?: string
}

export interface AdminCompanyDetail {
  id: string
  templateId?: string
  displayName?: string
  status?: string
  connectors?: Record<string, AdminConnectorEntry>
  inventorySync?: Record<string, unknown>
}

// ── Hook ──

interface UseWizardApiOptions {
  tenantId: string
  userId?: string
}

export function useWizardApi({ tenantId, userId = 'admin-user' }: UseWizardApiOptions) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortControllersRef = useRef<Set<AbortController>>(new Set())

  useEffect(() => {
    return () => {
      for (const controller of abortControllersRef.current) {
        controller.abort()
      }
      abortControllersRef.current.clear()
    }
  }, [])

  const adminHeaders = useMemo(
    (): Record<string, string> => ({
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'x-user-id': userId,
      'x-tenant-id': tenantId,
      'x-roles': 'tenant_admin',
    }),
    [tenantId, userId],
  )

  const callJson = useCallback(
    async <TPayload extends Record<string, unknown> | undefined>(
      url: string,
      options: {
        method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
        payload?: TPayload
        idempotencyPrefix?: string
      } = {},
    ): Promise<Record<string, unknown>> => {
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
    },
    [adminHeaders, tenantId],
  )

  const callFormData = useCallback(
    async (
      url: string,
      body: FormData,
      options: {
        method?: 'POST' | 'PUT'
        idempotencyPrefix?: string
      } = {},
    ): Promise<Record<string, unknown>> => {
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
    },
    [tenantId, userId],
  )

  const runAction = useCallback(async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(false)
    }
  }, [])

  return { callJson, callFormData, runAction, busy, error, setError, setBusy }
}
