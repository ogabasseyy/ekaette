import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useMarketing } from '../useMarketing'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useMarketing', () => {
  it('sendCampaign posts to /sms/campaign for sms channel', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'ok', campaign_id: 'cmp-001' }),
    })
    global.fetch = fetchMock

    const { result } = renderHook(() => useMarketing())

    let response: unknown
    await act(async () => {
      response = await result.current.sendCampaign({
        channel: 'sms',
        recipients: ['+2348011111111'],
        message: 'Hello',
        campaignName: 'Test Campaign',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
      })
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/at/sms/campaign')
    expect(opts.method).toBe('POST')
    const body = JSON.parse(opts.body)
    expect(body.to).toEqual(['+2348011111111'])
    expect(body.message).toBe('Hello')
    expect(body.campaign_name).toBe('Test Campaign')
    expect(opts.headers['Idempotency-Key']).toBeUndefined() // SMS campaign doesn't use idempotency
    expect(response).toEqual({ status: 'ok', campaign_id: 'cmp-001' })
  })

  it('sendCampaign posts to /voice/campaign for voice channel with idempotency key', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'ok', campaign_id: 'cmp-v-001' }),
    })
    global.fetch = fetchMock

    const { result } = renderHook(() => useMarketing())

    await act(async () => {
      await result.current.sendCampaign({
        channel: 'voice',
        recipients: ['+2348011111111', '+2348022222222'],
        message: 'Follow-up call',
        campaignName: 'Voice Campaign',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
      })
    })

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/at/voice/campaign')
    expect(opts.headers['Idempotency-Key']).toMatch(/^mkt-voice-campaign-/)
  })

  it('quickSms sends single SMS', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'ok' }),
    })
    global.fetch = fetchMock

    const { result } = renderHook(() => useMarketing())

    await act(async () => {
      await result.current.quickSms({
        to: '+2348011111111',
        message: 'Quick hello',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
      })
    })

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/at/sms/send')
    const body = JSON.parse(opts.body)
    expect(body.to).toBe('+2348011111111')
    expect(body.message).toBe('Quick hello')
  })

  it('quickCall initiates a single voice call with idempotency', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'ok' }),
    })
    global.fetch = fetchMock

    const { result } = renderHook(() => useMarketing())

    await act(async () => {
      await result.current.quickCall({
        to: '+2348011111111',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
      })
    })

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/at/voice/call')
    expect(opts.headers['Idempotency-Key']).toMatch(/^mkt-quick-call-/)
    const body = JSON.parse(opts.body)
    expect(body.to).toBe('+2348011111111')
  })

  it('sendCampaign throws on fetch failure', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
    })

    const { result } = renderHook(() => useMarketing())

    await expect(
      act(() =>
        result.current.sendCampaign({
          channel: 'sms',
          recipients: ['+2348011111111'],
          message: 'Fail test',
          campaignName: 'Fail',
          tenantId: 'public',
          companyId: 'ekaette-electronics',
        }),
      ),
    ).rejects.toThrow('502')
  })

  it('quickSms throws on network error', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network failure'))

    const { result } = renderHook(() => useMarketing())

    await expect(
      act(() =>
        result.current.quickSms({
          to: '+2348011111111',
          message: 'Fail',
          tenantId: 'public',
          companyId: 'ekaette-electronics',
        }),
      ),
    ).rejects.toThrow('Network failure')
  })

  it('quickCall throws on network error', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network failure'))

    const { result } = renderHook(() => useMarketing())

    await expect(
      act(() =>
        result.current.quickCall({
          to: '+2348011111111',
          tenantId: 'public',
          companyId: 'ekaette-electronics',
        }),
      ),
    ).rejects.toThrow('Network failure')
  })

  it('tracks sending state during sendCampaign', async () => {
    let resolveFetch!: (value: unknown) => void
    global.fetch = vi.fn().mockReturnValue(
      new Promise(resolve => {
        resolveFetch = resolve
      }),
    )

    const { result } = renderHook(() => useMarketing())
    expect(result.current.sending).toBe(false)

    let sendPromise: Promise<unknown>
    act(() => {
      sendPromise = result.current.sendCampaign({
        channel: 'sms',
        recipients: ['+2348011111111'],
        message: 'Test',
        campaignName: 'Test',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
      })
    })

    expect(result.current.sending).toBe(true)

    await act(async () => {
      resolveFetch({
        ok: true,
        json: () => Promise.resolve({ status: 'ok', campaign_id: 'cmp-001' }),
      })
      await sendPromise!
    })

    expect(result.current.sending).toBe(false)
  })
})
