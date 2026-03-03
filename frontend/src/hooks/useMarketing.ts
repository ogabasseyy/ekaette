import { useCallback, useState } from 'react'
import type { CampaignChannel } from '../types/marketing'

const IDEMPOTENCY_RANDOM_BYTES = 6

function makeIdempotencyKey(prefix: string): string {
  const bytes = new Uint8Array(IDEMPOTENCY_RANDOM_BYTES)
  crypto.getRandomValues(bytes)
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
  return `${prefix}-${Date.now()}-${hex}`
}

interface SendCampaignParams {
  channel: CampaignChannel
  recipients: string[]
  message: string
  campaignName: string
  tenantId: string
  companyId: string
}

interface QuickSmsParams {
  to: string
  message: string
  tenantId: string
  companyId: string
}

interface QuickCallParams {
  to: string
  tenantId: string
  companyId: string
}

interface UseMarketingResult {
  sending: boolean
  error: string | null
  sendCampaign: (params: SendCampaignParams) => Promise<unknown>
  quickSms: (params: QuickSmsParams) => Promise<unknown>
  quickCall: (params: QuickCallParams) => Promise<unknown>
}

export function useMarketing(): UseMarketingResult {
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sendCampaign = useCallback(async (params: SendCampaignParams) => {
    setError(null)
    setSending(true)
    try {
      const isVoice = params.channel === 'voice'
      const url = isVoice ? '/api/v1/at/voice/campaign' : '/api/v1/at/sms/campaign'

      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (isVoice) {
        headers['Idempotency-Key'] = makeIdempotencyKey('mkt-voice-campaign')
      }

      const body = {
        to: params.recipients,
        message: params.message,
        tenant_id: params.tenantId,
        company_id: params.companyId,
        campaign_name: params.campaignName,
      }

      const resp = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      })

      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`)
      }

      return await resp.json()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      throw err
    } finally {
      setSending(false)
    }
  }, [])

  const quickSms = useCallback(async (params: QuickSmsParams) => {
    setError(null)
    setSending(true)
    try {
      const resp = await fetch('/api/v1/at/sms/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to: params.to,
          message: params.message,
          tenant_id: params.tenantId,
          company_id: params.companyId,
        }),
      })

      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`)
      }

      return await resp.json()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      throw err
    } finally {
      setSending(false)
    }
  }, [])

  const quickCall = useCallback(async (params: QuickCallParams) => {
    setError(null)
    setSending(true)
    try {
      const resp = await fetch('/api/v1/at/voice/call', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': makeIdempotencyKey('mkt-quick-call'),
        },
        body: JSON.stringify({
          to: params.to,
          tenant_id: params.tenantId,
          company_id: params.companyId,
        }),
      })

      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`)
      }

      return await resp.json()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      throw err
    } finally {
      setSending(false)
    }
  }, [])

  return { sending, error, sendCampaign, quickSms, quickCall }
}
