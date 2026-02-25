import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ServerMessage } from '../../types'
import { useEkaetteSocket } from '../useEkaetteSocket'

interface MockSocket {
  url: string
  binaryType: string
  sent: Array<string | ArrayBuffer>
  readyState?: number
  bufferedAmount?: number
  onmessage: ((event: MessageEvent) => void) | null
  onclose?: ((event: CloseEvent) => void) | null
  close?: () => void
}

function getLastSocket(): MockSocket {
  const ws = (
    globalThis as {
      __lastMockWebSocket?: MockSocket
    }
  ).__lastMockWebSocket
  if (!ws) {
    throw new Error('Expected mock websocket instance')
  }
  return ws
}

describe('useEkaetteSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(globalThis.WebSocket as unknown as { instances?: unknown[] }).instances = []
    ;(
      globalThis as {
        __lastMockWebSocket?: MockSocket
      }
    ).__lastMockWebSocket = undefined
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('starts disconnected', () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    expect(result.current.state).toBe('disconnected')
  })

  it('connects with websocket path and arraybuffer binary type', () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })

    const ws = getLastSocket()
    expect(ws.url).toContain('/ws/user1/session1?industry=electronics')
    expect(ws.binaryType).toBe('arraybuffer')
    expect(result.current.state).toBe('connecting')
  })

  it('connects with industry query parameter when provided', () => {
    const { result } = renderHook(() =>
      useEkaetteSocket('user1', 'session1', { industry: 'hotel' }),
    )
    act(() => {
      result.current.connect()
    })

    const ws = getLastSocket()
    expect(ws.url).toContain('/ws/user1/session1?industry=hotel')
  })

  it('transitions to connected on open', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    expect(result.current.state).toBe('connected')
  })

  it('routes JSON messages to messages array', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    act(() => {
      ws.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'transcription',
            role: 'agent',
            text: 'Hello!',
            partial: false,
          }),
        }),
      )
    })

    expect(result.current.messages).toHaveLength(1)
    const first = result.current.messages[0]
    expect(first.type).toBe('transcription')
  })

  it('caps messages at 500 and drops oldest entries', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    act(() => {
      for (let i = 0; i < 505; i += 1) {
        ws.onmessage?.(
          new MessageEvent('message', {
            data: JSON.stringify({
              type: 'transcription',
              role: 'agent',
              text: `message-${i}`,
              partial: false,
            }),
          }),
        )
      }
    })

    expect(result.current.messages).toHaveLength(500)
    const first = result.current.messages[0]
    expect(first.type).toBe('transcription')
    if (first.type === 'transcription') {
      expect(first.text).toBe('message-5')
    }
  })

  it('reconnects when server indicates voice change requires reconnect', async () => {
    const { result } = renderHook(() =>
      useEkaetteSocket('user1', 'session1', { industry: 'hotel' }),
    )
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const firstWs = getLastSocket()
    act(() => {
      firstWs.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'session_started',
            sessionId: 'session1',
            industry: 'hotel',
            voiceChangeRequiresReconnect: true,
          }),
        }),
      )
    })

    // Advance past 150ms voice reconnect delay + 1ms for onopen setTimeout(0).
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    const secondWs = getLastSocket()

    expect(secondWs).not.toBe(firstWs)
    expect(secondWs.url).toContain('/ws/user1/session1?industry=hotel')
    expect(result.current.state).toBe('connected')
  })

  it('routes ArrayBuffer messages to onAudioData callback', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    const received: ArrayBuffer[] = []
    result.current.onAudioData.current = chunk => {
      received.push(chunk)
    }

    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    const chunk = new Uint8Array([1, 2, 3]).buffer
    act(() => {
      ws.onmessage?.(new MessageEvent('message', { data: chunk }))
    })

    expect(received).toHaveLength(1)
    expect(received[0].byteLength).toBe(3)
  })

  it('routes Blob messages to onAudioData callback', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    const received: ArrayBuffer[] = []
    result.current.onAudioData.current = chunk => {
      received.push(chunk)
    }

    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    const blob = new Blob([new Uint8Array([4, 5, 6])])
    await act(async () => {
      ws.onmessage?.(new MessageEvent('message', { data: blob }))
      await Promise.resolve()
    })

    expect(received).toHaveLength(1)
    expect(received[0].byteLength).toBe(3)
  })

  it('sendText serializes correctly', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    act(() => {
      result.current.sendText('hello')
    })

    const ws = getLastSocket()
    const raw = ws.sent.at(-1)
    expect(typeof raw).toBe('string')
    expect(JSON.parse(raw as string)).toEqual({ type: 'text', text: 'hello' })
  })

  it('sendImage includes mimeType', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    act(() => {
      result.current.sendImage('base64data', 'image/jpeg')
    })

    const ws = getLastSocket()
    const raw = ws.sent.at(-1)
    expect(typeof raw).toBe('string')
    expect(JSON.parse(raw as string)).toEqual({
      type: 'image',
      data: 'base64data',
      mimeType: 'image/jpeg',
    })
  })

  it('sendNegotiate sends expected payload', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    act(() => {
      result.current.sendNegotiate(195000, 'counter')
    })

    const ws = getLastSocket()
    const raw = ws.sent.at(-1)
    expect(typeof raw).toBe('string')
    expect(JSON.parse(raw as string)).toEqual({
      type: 'negotiate',
      counterOffer: 195000,
      action: 'counter',
    })
  })

  it('sendActivityStart and sendActivityEnd only send when manual VAD is enabled by server', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    const sentBefore = ws.sent.length
    act(() => {
      result.current.sendActivityStart()
    })
    expect(ws.sent.length).toBe(sentBefore)
    act(() => {
      result.current.sendActivityEnd()
    })
    expect(ws.sent.length).toBe(sentBefore)

    // Simulate backend capability handshake enabling manual VAD.
    act(() => {
      ws.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'session_started',
            sessionId: 'session1',
            industry: 'electronics',
            manualVadActive: true,
            vadMode: 'manual',
          }),
        }),
      )
    })

    act(() => {
      result.current.sendActivityStart()
    })
    let raw = ws.sent.at(-1)
    expect(typeof raw).toBe('string')
    expect(JSON.parse(raw as string)).toEqual({ type: 'activity_start' })

    act(() => {
      result.current.sendActivityEnd()
    })
    raw = ws.sent.at(-1)
    expect(typeof raw).toBe('string')
    expect(JSON.parse(raw as string)).toEqual({ type: 'activity_end' })
  })

  it('clearMessages removes accumulated messages', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    act(() => {
      ws.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'transcription',
            role: 'agent',
            text: 'Hello',
            partial: false,
          }),
        }),
      )
    })
    expect(result.current.messages.length).toBe(1)

    act(() => {
      result.current.clearMessages()
    })
    expect(result.current.messages).toEqual([])
  })

  it('disconnect sets state to disconnected', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    act(() => {
      result.current.disconnect()
    })

    expect(result.current.state).toBe('disconnected')
  })

  it('supports demo mode bypass and injected demo messages', () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1', { demoMode: true }))

    act(() => {
      result.current.connect()
    })
    expect(result.current.state).toBe('connected')

    const message: ServerMessage = {
      type: 'session_started',
      sessionId: 'demo',
      industry: 'electronics',
    }
    act(() => {
      result.current.injectDemoMessage(message)
    })

    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].type).toBe('session_started')
  })

  it('falls back to backend proxy when direct-live token preflight fails', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: 'unavailable' }),
    } as Response)

    const { result } = renderHook(() =>
      useEkaetteSocket('user1', 'session1', {
        transportMode: 'direct-live',
        tenantId: 'public',
        companyId: 'ekaette-electronics',
      }),
    )

    act(() => {
      result.current.connect()
    })
    await act(async () => {
      await Promise.resolve()
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/token',
      expect.objectContaining({
        method: 'POST',
      }),
    )
    expect(ws.url).toContain('/ws/user1/session1?industry=electronics')
    const fallback = result.current.messages.find(
      message => message.type === 'error' && message.code === 'DIRECT_MODE_FALLBACK',
    )
    expect(fallback).toBeDefined()
  })

  it('emits rapid disconnect error after repeated short-lived disconnects', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))

    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    let ws = getLastSocket()
    act(() => {
      ws.close?.()
    })
    await act(async () => {
      vi.advanceTimersByTime(1001)
    })

    ws = getLastSocket()
    act(() => {
      ws.close?.()
    })
    await act(async () => {
      vi.advanceTimersByTime(2001)
    })

    ws = getLastSocket()
    act(() => {
      ws.close?.()
    })

    expect(result.current.state).toBe('disconnected')
    const rapid = result.current.messages.find(
      message => message.type === 'error' && message.code === 'RAPID_DISCONNECT',
    )
    expect(rapid).toBeDefined()
  })

  it('tracks sendAudio drops when disconnected and backpressure when websocket is open', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    const droppedChunk = new Uint8Array([1, 2, 3]).buffer

    act(() => {
      result.current.sendAudio(droppedChunk)
    })
    expect(result.current.debugMetrics.audioTxDropCount).toBe(1)

    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.advanceTimersByTime(1)
    })

    const ws = getLastSocket()
    ws.bufferedAmount = 300 * 1024
    const chunk = new Uint8Array([9, 8, 7, 6]).buffer
    act(() => {
      result.current.sendAudio(chunk)
    })

    expect(ws.sent.at(-1)).toBe(chunk)
    expect(result.current.debugMetrics.audioTxChunks).toBe(1)
    expect(result.current.debugMetrics.audioTxBytes).toBe(4)
    expect(result.current.debugMetrics.audioTxBackpressureCount).toBe(1)
    expect(result.current.debugMetrics.audioTxDropCount).toBe(1)
  })
})
