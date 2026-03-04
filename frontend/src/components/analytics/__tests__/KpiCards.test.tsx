import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AnalyticsSummary } from '../../../types/analytics'
import { KpiCards } from '../KpiCards'

const MOCK_SUMMARY: AnalyticsSummary = {
  window_days: 30,
  campaigns_total: 5,
  total_sent: 200,
  total_delivered: 160,
  total_failed: 40,
  total_replies: 20,
  total_conversions: 10,
  total_revenue_kobo: 250000,
  total_revenue_naira: 2500,
  delivery_rate: 0.8,
  engagement_rate: 0.125,
  conversion_rate: 0.0625,
}

describe('KpiCards', () => {
  it('renders all 6 KPI labels', () => {
    render(<KpiCards summary={MOCK_SUMMARY} />)
    expect(screen.getByText('Campaigns')).toBeInTheDocument()
    expect(screen.getByText('Delivery Rate')).toBeInTheDocument()
    expect(screen.getByText('Engagement')).toBeInTheDocument()
    expect(screen.getByText('Conversion')).toBeInTheDocument()
    expect(screen.getByText('Revenue')).toBeInTheDocument()
    expect(screen.getByText('Messages Sent')).toBeInTheDocument()
  })

  it('displays campaign count', () => {
    render(<KpiCards summary={MOCK_SUMMARY} />)
    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('displays formatted rate percentages', () => {
    render(<KpiCards summary={MOCK_SUMMARY} />)
    expect(screen.getByText('80.0%')).toBeInTheDocument()
    expect(screen.getByText('12.5%')).toBeInTheDocument()
    expect(screen.getByText('6.3%')).toBeInTheDocument()
  })

  it('displays sent count', () => {
    render(<KpiCards summary={MOCK_SUMMARY} />)
    expect(screen.getByText('200')).toBeInTheDocument()
  })

  it('renders empty state when summary is null', () => {
    render(<KpiCards summary={null} />)
    expect(screen.getByText('No data available')).toBeInTheDocument()
  })
})
