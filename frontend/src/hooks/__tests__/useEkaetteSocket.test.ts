import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useEkaetteSocket } from '../useEkaetteSocket'
import type { ServerMessage } from '../../types'

interface MockSocket {
  url: string
  binaryType: string
  sent: Array<string | ArrayBuffer>
  onmessage: ((event: MessageEvent) => void) | null
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
    ;(
      globalThis as {
        __lastMockWebSocket?: MockSocket
      }
    ).__lastMockWebSocket = undefined
  })

  afterEach(() => {
    vi.useRealTimers()
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
    expect(ws.url).toContain('/ws/user1/session1')
    expect(ws.binaryType).toBe('arraybuffer')
    expect(result.current.state).toBe('connecting')
  })

  it('transitions to connected on open', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.runAllTimers()
    })

    expect(result.current.state).toBe('connected')
  })

  it('routes JSON messages to messages array', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.runAllTimers()
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
      vi.runAllTimers()
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
      vi.runAllTimers()
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
      vi.runAllTimers()
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
      vi.runAllTimers()
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
      vi.runAllTimers()
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

  it('sendConfig sends industry payload', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.runAllTimers()
    })

    act(() => {
      result.current.sendConfig('hotel')
    })

    const ws = getLastSocket()
    const raw = ws.sent.at(-1)
    expect(typeof raw).toBe('string')
    expect(JSON.parse(raw as string)).toEqual({
      type: 'config',
      industry: 'hotel',
    })
  })

  it('disconnect sets state to disconnected', async () => {
    const { result } = renderHook(() => useEkaetteSocket('user1', 'session1'))
    act(() => {
      result.current.connect()
    })
    await act(async () => {
      vi.runAllTimers()
    })

    act(() => {
      result.current.disconnect()
    })

    expect(result.current.state).toBe('disconnected')
  })

  it('supports demo mode bypass and injected demo messages', () => {
    const { result } = renderHook(() =>
      useEkaetteSocket('user1', 'session1', { demoMode: true }),
    )

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
})
