import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { CampaignSnapshot } from '../../../types/analytics'
import { CampaignTable } from '../CampaignTable'

const MOCK_CAMPAIGNS: CampaignSnapshot[] = [
  {
    campaign_id: 'cmp-sms-001',
    channel: 'sms',
    tenant_id: 'public',
    company_id: 'ekaette-electronics',
    campaign_name: 'Weekend Promo',
    message: '5% off this weekend',
    created_at: '2026-02-28T10:00:00+00:00',
    updated_at: '2026-02-28T12:00:00+00:00',
    recipients_total: 50,
    sent_total: 50,
    delivered_total: 40,
    failed_total: 10,
    replies_total: 5,
    conversions_total: 3,
    revenue_kobo: 30000,
    payments_initialized_total: 4,
    payments_success_total: 3,
    delivery_rate: 0.8,
    engagement_rate: 0.125,
    conversion_rate: 0.075,
    avg_order_value_kobo: 10000,
  },
  {
    campaign_id: 'cmp-voice-002',
    channel: 'voice',
    tenant_id: 'public',
    company_id: 'ekaette-electronics',
    campaign_name: 'Flash Sale',
    message: 'Flash sale alert',
    created_at: '2026-02-27T08:00:00+00:00',
    updated_at: '2026-02-27T10:00:00+00:00',
    recipients_total: 100,
    sent_total: 100,
    delivered_total: 90,
    failed_total: 10,
    replies_total: 15,
    conversions_total: 8,
    revenue_kobo: 80000,
    payments_initialized_total: 10,
    payments_success_total: 8,
    delivery_rate: 0.9,
    engagement_rate: 0.167,
    conversion_rate: 0.089,
    avg_order_value_kobo: 10000,
  },
]

describe('CampaignTable', () => {
  it('renders campaign names', () => {
    render(<CampaignTable campaigns={MOCK_CAMPAIGNS} onSelect={vi.fn()} />)
    expect(screen.getByText('Weekend Promo')).toBeInTheDocument()
    expect(screen.getByText('Flash Sale')).toBeInTheDocument()
  })

  it('renders channel badges', () => {
    render(<CampaignTable campaigns={MOCK_CAMPAIGNS} onSelect={vi.fn()} />)
    expect(screen.getByText('SMS')).toBeInTheDocument()
    expect(screen.getByText('VOICE')).toBeInTheDocument()
  })

  it('calls onSelect with correct campaign ID on row click', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<CampaignTable campaigns={MOCK_CAMPAIGNS} onSelect={onSelect} />)

    const row = screen.getByText('Weekend Promo').closest('tr')!
    await user.click(row)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('cmp-sms-001')
  })

  it('renders empty state when no campaigns', () => {
    render(<CampaignTable campaigns={[]} onSelect={vi.fn()} />)
    expect(screen.getByText('No campaigns yet')).toBeInTheDocument()
  })

  it('displays delivery rate for each campaign', () => {
    render(<CampaignTable campaigns={MOCK_CAMPAIGNS} onSelect={vi.fn()} />)
    expect(screen.getByText('80.0%')).toBeInTheDocument()
    expect(screen.getByText('90.0%')).toBeInTheDocument()
  })
})
