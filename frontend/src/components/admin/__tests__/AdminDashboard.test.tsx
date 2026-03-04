import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminDashboard } from '../AdminDashboard'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('AdminDashboard', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the admin console header', () => {
    render(<AdminDashboard />)
    expect(screen.getByText('Ekaette Admin Console')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /load snapshot/i })).toBeInTheDocument()
  })

  it('loads companies and providers snapshot', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse({
          apiVersion: 'v1',
          tenantId: 'public',
          companies: [
            {
              id: 'ekaette-telecom',
              templateId: 'telecom',
              displayName: 'Ekaette Telecom',
              status: 'active',
            },
          ],
          count: 1,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          apiVersion: 'v1',
          tenantId: 'public',
          providers: [{ id: 'mock', label: 'Mock Provider', status: 'active' }],
          count: 1,
        }),
      )

    render(<AdminDashboard />)
    fireEvent.click(screen.getByRole('button', { name: /load snapshot/i }))

    await waitFor(() => {
      expect(screen.getByText('Snapshot loaded.')).toBeInTheDocument()
      expect(screen.getByText(/Companies \(1\)/i)).toBeInTheDocument()
      expect(screen.getByText('Ekaette Telecom')).toBeInTheDocument()
      expect(screen.getByText(/Providers \(1\)/i)).toBeInTheDocument()
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('imports knowledge from file using multipart form data', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse(
          {
            apiVersion: 'v1',
            tenantId: 'public',
            companyId: 'ekaette-electronics',
            knowledgeId: 'kb-1',
            created: true,
          },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          apiVersion: 'v1',
          tenantId: 'public',
          companyId: 'ekaette-electronics',
          entries: [],
          count: 0,
        }),
      )

    render(<AdminDashboard />)
    const file = new File(['knowledge content'], 'faq.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText(/knowledge file/i), {
      target: { files: [file] },
    })
    fireEvent.click(screen.getByRole('button', { name: /import file/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    const firstCall = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(firstCall[0]).toContain(
      '/api/v1/admin/companies/ekaette-electronics/knowledge/import-file',
    )
    expect(firstCall[0]).toContain('tenantId=public')
    expect(firstCall[1].method).toBe('POST')
    const headers = firstCall[1].headers as Record<string, string>
    expect(headers['Idempotency-Key']).toMatch(/^admin-knowledge-file-/)
    expect(headers['x-tenant-id']).toBe('public')
    const body = firstCall[1].body
    expect(body).toBeInstanceOf(FormData)
    const formData = body as FormData
    expect(formData.get('title')).toBe('FAQ')
    expect(formData.get('source')).toBe('file')
    expect(formData.get('file')).toBeInstanceOf(File)
  })

  it('updates an existing connector with PUT when in edit mode', async () => {
    const companyPayload = {
      apiVersion: 'v1',
      tenantId: 'public',
      company: {
        id: 'ekaette-electronics',
        templateId: 'electronics',
        displayName: 'Ekaette Electronics',
        status: 'active',
        connectors: {
          crm: {
            provider: 'mock',
            enabled: true,
            capabilities: ['read'],
          },
        },
      },
    }
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(companyPayload))
      .mockResolvedValueOnce(
        jsonResponse({
          apiVersion: 'v1',
          tenantId: 'public',
          companyId: 'ekaette-electronics',
          connectorId: 'crm',
          updated: true,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(companyPayload))

    render(<AdminDashboard />)
    fireEvent.click(screen.getByRole('button', { name: /load company detail/i }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
    fireEvent.change(screen.getByPlaceholderText('Provider'), {
      target: { value: 'salesforce' },
    })
    fireEvent.click(screen.getByRole('button', { name: /update connector/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3)
    })

    const updateCall = fetchMock.mock.calls[1] as [string, RequestInit]
    expect(updateCall[0]).toContain('/api/v1/admin/companies/ekaette-electronics/connectors/crm')
    expect(updateCall[1].method).toBe('PUT')
    const updateBody = JSON.parse(String(updateCall[1].body)) as Record<string, unknown>
    expect(updateBody.connectorId).toBe('crm')
    expect(updateBody.provider).toBe('salesforce')
  })

  it('sends selected runtime data tier in products import payload', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({
        apiVersion: 'v1',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
        collection: 'products',
        written: 1,
      }),
    )

    render(<AdminDashboard />)
    fireEvent.change(screen.getByLabelText(/data tier/i), {
      target: { value: 'demo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /import products/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    const firstCall = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(firstCall[1].method).toBe('POST')
    const payload = JSON.parse(String(firstCall[1].body)) as Record<string, unknown>
    expect(payload.data_tier).toBe('demo')
  })

  it('sends inventory sync payload for google sheets source', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({
        apiVersion: 'v1',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
        sourceType: 'google_sheets',
        written: 2,
      }),
    )

    render(<AdminDashboard />)
    fireEvent.change(screen.getByLabelText(/google sheets url/i), {
      target: { value: 'https://docs.google.com/spreadsheets/d/test/edit#gid=0' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sync inventory source/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    const call = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(call[0]).toContain('/api/v1/admin/companies/ekaette-electronics/inventory/sync')
    expect(call[1].method).toBe('POST')
    const payload = JSON.parse(String(call[1].body)) as Record<string, unknown>
    expect(payload.sourceType).toBe('google_sheets')
    expect(payload.sourceUrl).toBe('https://docs.google.com/spreadsheets/d/test/edit#gid=0')
  })

  it('sends inventory sync config payload', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({
        apiVersion: 'v1',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
        configured: true,
        inventorySync: {
          auto_enabled: true,
          interval_minutes: 30,
        },
      }),
    )

    render(<AdminDashboard />)
    fireEvent.click(screen.getByLabelText(/enable auto sync schedule/i))
    fireEvent.change(screen.getByLabelText(/interval minutes/i), {
      target: { value: '30' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save sync config/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    const call = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(call[0]).toContain('/api/v1/admin/companies/ekaette-electronics/inventory/sync/config')
    expect(call[1].method).toBe('PUT')
    const payload = JSON.parse(String(call[1].body)) as Record<string, unknown>
    expect(payload.autoEnabled).toBe(true)
    expect(payload.intervalMinutes).toBe(30)
  })
})
