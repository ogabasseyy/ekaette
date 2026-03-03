import { useCallback, useEffect, useRef, useState } from 'react'
import type { ContactsResponse, KnownContact } from '../types/marketing'

interface UseContactsOptions {
  tenantId: string
  companyId: string
}

interface UseContactsResult {
  contacts: KnownContact[]
  loading: boolean
  error: string | null
  selected: Set<string>
  selectedContacts: KnownContact[]
  toggle: (phone: string) => void
  selectAll: () => void
  deselectAll: () => void
  refetch: () => void
}

export function useContacts({ tenantId, companyId }: UseContactsOptions): UseContactsResult {
  const [contacts, setContacts] = useState<KnownContact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const abortRef = useRef<AbortController | null>(null)

  const fetchContacts = useCallback(async () => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)

    try {
      const params = new URLSearchParams({ tenantId, companyId })
      const resp = await fetch(`/api/v1/at/analytics/contacts?${params}`, {
        signal: controller.signal,
      })
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`)
      }
      const data: ContactsResponse = await resp.json()
      setContacts(data.contacts)
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      setError(err instanceof Error ? err.message : 'Failed to fetch contacts')
      setContacts([])
    } finally {
      setLoading(false)
    }
  }, [tenantId, companyId])

  useEffect(() => {
    void fetchContacts()
    return () => {
      abortRef.current?.abort()
    }
  }, [fetchContacts])

  const toggle = useCallback((phone: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(phone)) {
        next.delete(phone)
      } else {
        next.add(phone)
      }
      return next
    })
  }, [])

  const selectAll = useCallback(() => {
    setSelected(new Set(contacts.map(c => c.phone)))
  }, [contacts])

  const deselectAll = useCallback(() => {
    setSelected(new Set())
  }, [])

  const selectedContacts = contacts.filter(c => selected.has(c.phone))

  return {
    contacts,
    loading,
    error,
    selected,
    selectedContacts,
    toggle,
    selectAll,
    deselectAll,
    refetch: fetchContacts,
  }
}
