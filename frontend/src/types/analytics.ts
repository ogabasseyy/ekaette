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

export interface VoiceAnalyticsSummary {
  window_days: number
  calls_total: number
  calls_completed: number
  avg_duration_seconds: number
  transfers_total: number
  transfer_rate: number
  callback_requests_total: number
  callback_triggered_total: number
  transcript_coverage_rate: number
}

export interface VoiceCallSnapshot {
  session_id: string
  tenant_id: string
  company_id: string
  channel: 'voice' | 'whatsapp' | 'sms' | 'omni'
  status: 'active' | 'completed'
  started_at: string
  updated_at: string
  ended_at: string | null
  duration_seconds: number
  transfer_count: number
  callback_requested: boolean
  callback_triggered: boolean
  transcript_messages_total: number
  transcript_preview: string
  agent_path: string[]
}

export interface VoiceAnalyticsOverviewResponse {
  status: string
  tenant_id: string
  company_id: string
  summary: VoiceAnalyticsSummary
  recent_calls: VoiceCallSnapshot[]
}
