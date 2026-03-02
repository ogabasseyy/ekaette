import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CampaignDetail } from '../CampaignDetail'
import type { CampaignSnapshot } from '../../../types/analytics'

const MOCK_CAMPAIGN: CampaignSnapshot = {
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
}

describe('CampaignDetail', () => {
  it('renders campaign name', () => {
    render(<CampaignDetail campaign={MOCK_CAMPAIGN} onClose={vi.fn()} />)
    expect(screen.getByText('Weekend Promo')).toBeInTheDocument()
  })

  it('renders all metric labels', () => {
    render(<CampaignDetail campaign={MOCK_CAMPAIGN} onClose={vi.fn()} />)
    expect(screen.getByText('Sent')).toBeInTheDocument()
    expect(screen.getByText('Delivered')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('Replies')).toBeInTheDocument()
    expect(screen.getByText('Conversions')).toBeInTheDocument()
  })

  it('renders message preview', () => {
    render(<CampaignDetail campaign={MOCK_CAMPAIGN} onClose={vi.fn()} />)
    expect(screen.getByText('5% off this weekend')).toBeInTheDocument()
  })

  it('close button calls onClose', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<CampaignDetail campaign={MOCK_CAMPAIGN} onClose={onClose} />)
    await user.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
