import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ContactsResponse, KnownContact } from '../../types/marketing'
import { useContacts } from '../useContacts'

const MOCK_CONTACT_A: KnownContact = {
  phone: '+2348011111111',
  last_campaign_id: 'cmp-sms-001',
  last_campaign_name: 'Weekend Promo',
  channel: 'sms',
}

const MOCK_CONTACT_B: KnownContact = {
  phone: '+2348022222222',
  last_campaign_id: 'cmp-voice-001',
  last_campaign_name: 'Follow-up',
  channel: 'voice',
}

const MOCK_RESPONSE: ContactsResponse = {
  status: 'ok',
  tenant_id: 'public',
  company_id: 'ekaette-electronics',
  contacts: [MOCK_CONTACT_A, MOCK_CONTACT_B],
  count: 2,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useContacts', () => {
  it('starts in loading state', () => {
    global.fetch = vi.fn().mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )
    expect(result.current.loading).toBe(true)
    expect(result.current.contacts).toEqual([])
  })

  it('fetches contacts on mount', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_RESPONSE),
    })

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.contacts).toHaveLength(2)
    expect(result.current.contacts[0].phone).toBe('+2348011111111')
  })

  it('builds correct fetch URL with query params', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_RESPONSE),
    })

    renderHook(() => useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }))

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled()
    })

    const url = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain('/api/v1/at/analytics/contacts')
    expect(url).toContain('tenantId=public')
    expect(url).toContain('companyId=ekaette-electronics')
  })

  it('sets error on fetch failure', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Server Error',
    })

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.error).toBeTruthy()
    expect(result.current.contacts).toEqual([])
  })

  it('toggle selects and deselects a contact', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_RESPONSE),
    })

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.contacts).toHaveLength(2)
    })

    act(() => {
      result.current.toggle('+2348011111111')
    })
    expect(result.current.selected.has('+2348011111111')).toBe(true)

    act(() => {
      result.current.toggle('+2348011111111')
    })
    expect(result.current.selected.has('+2348011111111')).toBe(false)
  })

  it('selectAll selects all contacts', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_RESPONSE),
    })

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.contacts).toHaveLength(2)
    })

    act(() => {
      result.current.selectAll()
    })
    expect(result.current.selected.size).toBe(2)
    expect(result.current.selected.has('+2348011111111')).toBe(true)
    expect(result.current.selected.has('+2348022222222')).toBe(true)
  })

  it('deselectAll clears selection', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_RESPONSE),
    })

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.contacts).toHaveLength(2)
    })

    act(() => {
      result.current.selectAll()
    })
    expect(result.current.selected.size).toBe(2)

    act(() => {
      result.current.deselectAll()
    })
    expect(result.current.selected.size).toBe(0)
  })

  it('selectedContacts returns only selected KnownContact objects', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_RESPONSE),
    })

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.contacts).toHaveLength(2)
    })

    act(() => {
      result.current.toggle('+2348022222222')
    })

    expect(result.current.selectedContacts).toHaveLength(1)
    expect(result.current.selectedContacts[0].phone).toBe('+2348022222222')
  })

  it('refetch re-fetches contacts and transitions loading state', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(MOCK_RESPONSE),
    })
    global.fetch = fetchMock

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    const callsBefore = fetchMock.mock.calls.length

    act(() => {
      result.current.refetch()
    })

    expect(result.current.loading).toBe(true)

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore)
    })

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.contacts).toHaveLength(2)
  })

  it('returns empty contacts on network error', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network failure'))

    const { result } = renderHook(() =>
      useContacts({ tenantId: 'public', companyId: 'ekaette-electronics' }),
    )

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.error).toBeTruthy()
    expect(result.current.contacts).toEqual([])
  })
})
