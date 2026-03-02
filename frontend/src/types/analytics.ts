export interface AnalyticsSummary {
  window_days: number
  campaigns_total: number
  total_sent: number
  total_delivered: number
  total_failed: number
  total_replies: number
  total_conversions: number
  total_revenue_kobo: number
  total_revenue_naira: number
  delivery_rate: number
  engagement_rate: number
  conversion_rate: number
}

export interface CampaignSnapshot {
  campaign_id: string
  channel: 'sms' | 'voice' | 'omni'
  tenant_id: string
  company_id: string
  campaign_name: string
  message: string
  created_at: string
  updated_at: string
  recipients_total: number
  sent_total: number
  delivered_total: number
  failed_total: number
  replies_total: number
  conversions_total: number
  revenue_kobo: number
  payments_initialized_total: number
  payments_success_total: number
  delivery_rate: number
  engagement_rate: number
  conversion_rate: number
  avg_order_value_kobo: number
}

export interface AnalyticsOverviewResponse {
  status: string
  tenant_id: string
  company_id: string
  summary: AnalyticsSummary
  campaigns: CampaignSnapshot[]
}

export interface CampaignDetailResponse {
  status: string
  campaign: CampaignSnapshot | null
}
