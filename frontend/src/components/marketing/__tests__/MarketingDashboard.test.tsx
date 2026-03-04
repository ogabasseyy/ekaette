import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AnalyticsOverviewResponse } from '../../../types/analytics'
import type { ContactsResponse } from '../../../types/marketing'
import { MarketingDashboard } from '../MarketingDashboard'

vi.mock('../../../lib/navigation', () => ({
  NAV_ITEMS: [
    { page: 'voice', label: 'Voice', iconName: 'Mic' },
    { page: 'analytics', label: 'Analytics', iconName: 'BarChart3' },
    { page: 'marketing', label: 'Marketing', iconName: 'Megaphone' },
    { page: 'admin', label: 'Admin', iconName: 'Settings' },
  ],
  navigateTo: vi.fn(),
  currentPage: () => 'marketing',
}))

const MOCK_CONTACTS: ContactsResponse = {
  status: 'ok',
  tenant_id: 'public',
  company_id: 'ekaette-electronics',
  contacts: [
    {
      phone: '+2348011111111',
      last_campaign_id: 'cmp-001',
      last_campaign_name: 'Promo A',
      channel: 'sms',
    },
    {
      phone: '+2348022222222',
      last_campaign_id: 'cmp-002',
      last_campaign_name: 'Follow-up',
      channel: 'voice',
    },
  ],
  count: 2,
}

const MOCK_EMPTY_CONTACTS: ContactsResponse = {
  status: 'ok',
  tenant_id: 'public',
  company_id: 'ekaette-electronics',
  contacts: [],
  count: 0,
}

const MOCK_ANALYTICS: AnalyticsOverviewResponse = {
  status: 'ok',
  tenant_id: 'public',
  company_id: 'ekaette-electronics',
  summary: {
    window_days: 30,
    campaigns_total: 0,
    total_sent: 0,
    total_delivered: 0,
    total_failed: 0,
    total_replies: 0,
    total_conversions: 0,
    total_revenue_kobo: 0,
    total_revenue_naira: 0,
    delivery_rate: 0,
    engagement_rate: 0,
    conversion_rate: 0,
  },
  campaigns: [],
}

function mockFetchResponses(
  contactsResp: ContactsResponse,
  analyticsResp: AnalyticsOverviewResponse = MOCK_ANALYTICS,
) {
  return vi.fn((url: string) => {
    if (url.includes('/analytics/contacts')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(contactsResp) })
    }
    if (url.includes('/analytics/overview')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(analyticsResp) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'ok' }) })
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MarketingDashboard', () => {
  it('renders page title', () => {
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)
    expect(screen.getByText('Marketing Campaigns')).toBeInTheDocument()
  })

  it('renders NavBar with marketing active', () => {
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)
    const marketingTab = screen.getByRole('tab', { name: /marketing/i })
    expect(marketingTab).toHaveAttribute('aria-current', 'page')
  })

  it('shows loading state while fetching', () => {
    global.fetch = vi.fn().mockReturnValue(new Promise(() => {}))
    render(<MarketingDashboard />)
    expect(screen.getByText('Loading contacts…')).toBeInTheDocument()
  })

  it('shows empty state when no contacts', async () => {
    global.fetch = mockFetchResponses(MOCK_EMPTY_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText(/no contacts yet/i)).toBeInTheDocument()
    })
  })

  it('renders contact list after data loads', async () => {
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })
    expect(screen.getByText('+2348022222222')).toBeInTheDocument()
  })

  it('renders channel badges on contact rows', async () => {
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })
    // SMS and VOICE badges should exist
    const badges = screen.getAllByText(/sms|voice/i)
    expect(badges.length).toBeGreaterThanOrEqual(2)
  })

  it('toggling a contact checkbox selects it', async () => {
    const user = userEvent.setup()
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    const checkboxes = screen.getAllByRole('checkbox')
    await user.click(checkboxes[0])

    // After selection, the recipient count should show
    expect(screen.getByText(/1 recipient/i)).toBeInTheDocument()
  })

  it('Select All selects all contacts', async () => {
    const user = userEvent.setup()
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    const selectAllBtn = screen.getByRole('button', { name: /select all/i })
    await user.click(selectAllBtn)

    expect(screen.getByText(/2 recipients/i)).toBeInTheDocument()
  })

  it('Clear deselects all contacts', async () => {
    const user = userEvent.setup()
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /select all/i }))
    expect(screen.getByText(/2 recipients/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /clear/i }))
    expect(screen.getByText(/0 recipients/i)).toBeInTheDocument()
  })

  it('renders channel toggle with SMS and Voice options', async () => {
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    // Channel toggle buttons contain text "SMS" and "Voice" (not aria-label)
    const smsButtons = screen.getAllByRole('button', { name: /^sms$/i })
    expect(smsButtons.length).toBeGreaterThanOrEqual(1)
    const voiceButtons = screen.getAllByRole('button', { name: /^voice$/i })
    expect(voiceButtons.length).toBeGreaterThanOrEqual(1)
  })

  it('renders campaign name and message fields', async () => {
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    expect(screen.getByPlaceholderText(/campaign name/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/message/i)).toBeInTheDocument()
  })

  it('Send Campaign button is disabled without recipients or message', async () => {
    global.fetch = mockFetchResponses(MOCK_CONTACTS)
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    const sendBtn = screen.getByRole('button', { name: /send campaign/i })
    expect(sendBtn).toBeDisabled()
  })

  it('quick SMS button triggers fetch to /sms/send', async () => {
    const user = userEvent.setup()
    const fetchMock = mockFetchResponses(MOCK_CONTACTS)
    global.fetch = fetchMock
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    const row = screen.getByText('+2348011111111').closest('[data-contact-row]')!
    const smsBtn = within(row as HTMLElement).getByRole('button', { name: /sms/i })
    await user.click(smsBtn)

    // Quick SMS should prompt or send — either way fetch was called
    await waitFor(() => {
      const smsCalls = fetchMock.mock.calls.filter(([url]: [string]) => url.includes('/sms/send'))
      expect(smsCalls.length).toBe(1)
    })
  })

  it('quick Call button triggers fetch to /voice/call', async () => {
    const user = userEvent.setup()
    const fetchMock = mockFetchResponses(MOCK_CONTACTS)
    global.fetch = fetchMock
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText('+2348011111111')).toBeInTheDocument()
    })

    const row = screen.getByText('+2348011111111').closest('[data-contact-row]')!
    const callBtn = within(row as HTMLElement).getByRole('button', { name: /call/i })
    await user.click(callBtn)

    await waitFor(() => {
      const callCalls = fetchMock.mock.calls.filter(([url]: [string]) =>
        url.includes('/voice/call'),
      )
      expect(callCalls.length).toBe(1)
    })
  })

  it('shows error on fetch failure', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: 'Server Error' })
    render(<MarketingDashboard />)

    await waitFor(() => {
      expect(screen.getByText(/500/)).toBeInTheDocument()
    })
  })
})
