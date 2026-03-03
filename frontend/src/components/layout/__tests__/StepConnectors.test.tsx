import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// Stub fetch globally
const fetchSpy = vi.spyOn(globalThis, 'fetch')

function mockProviders(overrides: Record<string, unknown>[] = []) {
  const providers =
    overrides.length > 0
      ? overrides
      : [
          {
            id: 'mock',
            label: 'Mock Provider',
            status: 'active',
            requiresSecretRef: false,
            capabilities: ['read'],
          },
          {
            id: 'salesforce',
            label: 'Salesforce',
            status: 'preview',
            requiresSecretRef: true,
            capabilities: ['read', 'write'],
          },
        ]
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ apiVersion: 'v1', providers, count: providers.length }),
  }
}

function mockCompanyDetail(connectors: Record<string, unknown> = {}) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({
      apiVersion: 'v1',
      company: { id: 'acme', connectors },
    }),
  }
}

function mockSuccess(body: Record<string, unknown> = {}) {
  return {
    ok: true,
    status: 201,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ apiVersion: 'v1', ...body }),
  }
}

function mockError(status: number, error: string) {
  return {
    ok: false,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ error }),
  }
}

// Route fetch calls based on URL
function routeFetch(
  overrides: {
    providers?: ReturnType<typeof mockProviders>
    company?: ReturnType<typeof mockCompanyDetail>
    connector?: ReturnType<typeof mockSuccess>
  } = {},
) {
  fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (url.includes('/mcp/providers'))
      return overrides.providers ?? (mockProviders() as unknown as Response)
    // Match specific connector ID path (e.g. /connectors/crm/test, /connectors/crm)
    if (/\/connectors\/[^/]+/.test(url))
      return overrides.connector ?? (mockSuccess() as unknown as Response)
    // Match POST/DELETE to /connectors collection (create new connector)
    if (url.includes('/connectors') && init?.method && init.method !== 'GET')
      return mockSuccess() as unknown as Response
    // GET /connectors falls through to company detail (list connectors via company)
    if (url.includes('/companies/'))
      return overrides.company ?? (mockCompanyDetail() as unknown as Response)
    return overrides.company ?? (mockCompanyDetail() as unknown as Response)
  })
}

// Lazy import after mocks
async function renderStep(props?: Partial<{ companyId: string; tenantId: string }>) {
  const { StepConnectors } = await import('../wizard/StepConnectors')
  const onNext = vi.fn()
  const onBack = vi.fn()
  render(
    <StepConnectors
      companyId={props?.companyId ?? 'acme'}
      tenantId={props?.tenantId ?? 'public'}
      onNext={onNext}
      onBack={onBack}
    />,
  )
  return { onNext, onBack }
}

describe('StepConnectors', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fetchSpy.mockReset()
  })

  it('renders provider cards fetched from backend', async () => {
    routeFetch()
    await renderStep()
    await waitFor(() => {
      expect(screen.getByText('Mock Provider')).toBeInTheDocument()
      expect(screen.getByText('Salesforce')).toBeInTheDocument()
    })
  })

  it('shows Custom MCP Server card', async () => {
    routeFetch()
    await renderStep()
    await waitFor(() => {
      expect(screen.getByText(/custom mcp/i)).toBeInTheDocument()
    })
  })

  it('shows Connected badge for pre-connected connectors', async () => {
    routeFetch({
      company: mockCompanyDetail({
        'mock-provider': { id: 'mock-provider', provider: 'mock', enabled: true },
      }),
    })
    await renderStep()
    await waitFor(() => {
      expect(screen.getByText(/connected/i)).toBeInTheDocument()
    })
  })

  it('mock connector: click Connect creates immediately without secret prompt', async () => {
    routeFetch()
    const user = userEvent.setup()
    await renderStep()
    await waitFor(() => expect(screen.getByText('Mock Provider')).toBeInTheDocument())

    const mockCard = screen.getByText('Mock Provider').closest('li')!
    const mockConnect = within(mockCard as HTMLElement).getByRole('button', { name: /connect/i })
    await user.click(mockConnect)

    // Should call POST to /connectors (not show a secret input)
    await waitFor(() => {
      const postCalls = fetchSpy.mock.calls.filter(
        ([url, opts]) =>
          typeof url === 'string' &&
          url.includes('/connectors') &&
          (opts as RequestInit)?.method === 'POST',
      )
      expect(postCalls.length).toBeGreaterThan(0)
    })
  })

  it('secret-required connector: click Connect shows inline panel', async () => {
    routeFetch()
    const user = userEvent.setup()
    await renderStep()
    await waitFor(() => expect(screen.getByText('Salesforce')).toBeInTheDocument())

    const sfCard = screen.getByText('Salesforce').closest('li')!
    const sfConnect = within(sfCard as HTMLElement).getByRole('button', { name: /connect/i })
    await user.click(sfConnect)

    // Should show the secret input
    await waitFor(() => {
      expect(screen.getByLabelText(/api key|secret/i)).toBeInTheDocument()
    })
  })

  it('inline panel Save calls POST with secretRef in payload', async () => {
    routeFetch()
    const user = userEvent.setup()
    await renderStep()
    await waitFor(() => expect(screen.getByText('Salesforce')).toBeInTheDocument())

    const sfCard = screen.getByText('Salesforce').closest('li')!
    await user.click(within(sfCard as HTMLElement).getByRole('button', { name: /connect/i }))

    const secretInput = await screen.findByLabelText(/api key|secret/i)
    await user.type(secretInput, 'sk-test-123')

    const saveButton = screen.getByRole('button', { name: /save/i })
    await user.click(saveButton)

    await waitFor(() => {
      const postCalls = fetchSpy.mock.calls.filter(
        ([url, opts]) =>
          typeof url === 'string' &&
          url.includes('/connectors') &&
          (opts as RequestInit)?.method === 'POST',
      )
      expect(postCalls.length).toBeGreaterThan(0)
      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string)
      expect(body.secretRef).toBe('sk-test-123')
      expect(body.provider).toBe('salesforce')
    })
  })

  it('Test button calls POST test endpoint', async () => {
    routeFetch({
      company: mockCompanyDetail({
        'crm-salesforce': { id: 'crm-salesforce', provider: 'salesforce', enabled: true },
      }),
      connector: mockSuccess({ ok: true, details: 'Connector probe passed.' }) as ReturnType<
        typeof mockSuccess
      >,
    })
    const user = userEvent.setup()
    await renderStep()

    const testButton = await screen.findByRole('button', { name: /^test$/i })
    await user.click(testButton)

    await waitFor(() => {
      const testCalls = fetchSpy.mock.calls.filter(
        ([url, opts]) =>
          typeof url === 'string' &&
          url.includes('/test') &&
          (opts as RequestInit)?.method === 'POST',
      )
      expect(testCalls.length).toBeGreaterThan(0)
    })
  })

  it('Remove button calls DELETE', async () => {
    routeFetch({
      company: mockCompanyDetail({
        'crm-salesforce': { id: 'crm-salesforce', provider: 'salesforce', enabled: true },
      }),
    })
    const user = userEvent.setup()
    await renderStep()

    const removeButton = await screen.findByRole('button', { name: /remove/i })
    await user.click(removeButton)

    await waitFor(() => {
      const deleteCalls = fetchSpy.mock.calls.filter(
        ([, opts]) => (opts as RequestInit)?.method === 'DELETE',
      )
      expect(deleteCalls.length).toBeGreaterThan(0)
    })
  })

  it('Skip button calls onNext', async () => {
    routeFetch()
    const user = userEvent.setup()
    const { onNext } = await renderStep()

    const skipButton = screen.getByRole('button', { name: /skip/i })
    await user.click(skipButton)
    expect(onNext).toHaveBeenCalled()
  })

  it('Custom MCP card shows URL and provider ID inputs when expanded', async () => {
    routeFetch()
    const user = userEvent.setup()
    await renderStep()

    const customCard = await screen.findByText(/custom mcp/i)
    await user.click(customCard)

    await waitFor(() => {
      expect(screen.getByLabelText(/server url/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/provider id/i)).toBeInTheDocument()
    })
  })

  it('Back button calls onBack', async () => {
    routeFetch()
    const user = userEvent.setup()
    const { onBack } = await renderStep()

    const backButton = screen.getByRole('button', { name: /back/i })
    await user.click(backButton)
    expect(onBack).toHaveBeenCalled()
  })

  it('shows error from failed API call', async () => {
    fetchSpy.mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.includes('/mcp/providers')) return mockProviders() as unknown as Response
      if (url.includes('/connectors'))
        return mockError(400, 'Provider not allowed') as unknown as Response
      return mockCompanyDetail() as unknown as Response
    })
    const user = userEvent.setup()
    await renderStep()
    await waitFor(() => expect(screen.getByText('Mock Provider')).toBeInTheDocument())

    const mockCard = screen.getByText('Mock Provider').closest('li')!
    await user.click(within(mockCard as HTMLElement).getByRole('button', { name: /connect/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
  })
})
