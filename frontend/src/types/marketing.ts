export type CampaignChannel = 'sms' | 'voice'

export interface KnownContact {
  phone: string
  last_campaign_id: string
  last_campaign_name: string
  channel: string
}

export interface ContactsResponse {
  status: string
  tenant_id: string
  company_id: string
  contacts: KnownContact[]
  count: number
}

export interface ReadinessResponse {
  sms_enabled: boolean
  voice_enabled: boolean
}
