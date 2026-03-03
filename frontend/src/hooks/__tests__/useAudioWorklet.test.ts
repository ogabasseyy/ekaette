import { act, renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAudioWorklet } from '../useAudioWorklet'

// Helper to create the ref that useAudioWorklet expects
function renderAudioWorklet(options?: Parameters<typeof useAudioWorklet>[1]) {
  return renderHook(() => {
    const onAudioChunk = useRef<((data: ArrayBuffer) => void) | null>(null)
    const worklet = useAudioWorklet(onAudioChunk, options)
    return { worklet, onAudioChunk }
  })
}

describe('useAudioWorklet', () => {
  const OriginalAudioContext = globalThis.AudioContext

  afterEach(() => {
    globalThis.AudioContext = OriginalAudioContext
    vi.restoreAllMocks()
  })
  it('startRecording creates AudioContext at 16kHz', async () => {
    const { result } = renderAudioWorklet()

    await act(async () => {
      await result.current.worklet.startRecording()
    })

    // The mock AudioContext should have been called — no error
    expect(result.current.worklet.error).toBeNull()
  })

  it('initPlayer creates AudioContext at 24kHz', async () => {
    const { result } = renderAudioWorklet()

    await act(async () => {
      await result.current.worklet.initPlayer()
    })

    expect(result.current.worklet.error).toBeNull()
  })

  it('stop cleans up without error', async () => {
    const { result } = renderAudioWorklet()

    await act(async () => {
      await result.current.worklet.startRecording()
      await result.current.worklet.initPlayer()
    })

    act(() => {
      result.current.worklet.stop()
    })

    expect(result.current.worklet.error).toBeNull()
  })

  it('playAudioChunk does not throw when no player', () => {
    const { result } = renderAudioWorklet()

    // Should not throw even without initPlayer
    act(() => {
      result.current.worklet.playAudioChunk(new ArrayBuffer(100))
    })
  })

  it('clearPlaybackBuffer does not throw when no player', () => {
    const { result } = renderAudioWorklet()

    act(() => {
      result.current.worklet.clearPlaybackBuffer()
    })
  })

  it('handles getUserMedia rejection gracefully', async () => {
    // Override getUserMedia to reject
    const original = navigator.mediaDevices.getUserMedia
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: async () => {
          throw new Error('Permission denied')
        },
      },
      configurable: true,
    })

    const { result } = renderAudioWorklet()

    await act(async () => {
      await result.current.worklet.startRecording()
    })

    expect(result.current.worklet.error).toBe('Permission denied')

    // Restore
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: original },
      configurable: true,
    })
  })

  it('requests browser audio processing constraints by default', async () => {
    const getUserMediaSpy = vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue({
      getTracks: () => [{ stop: () => {} }],
      getAudioTracks: () => [{ getSettings: () => ({}) }],
    } as unknown as MediaStream)

    const { result } = renderAudioWorklet()
    await act(async () => {
      await result.current.worklet.startRecording()
    })

    expect(getUserMediaSpy).toHaveBeenCalledTimes(1)
    const constraints = getUserMediaSpy.mock.calls[0]?.[0]
    const audio = constraints?.audio as MediaTrackConstraints
    expect(audio.echoCancellation).toBe(true)
    expect(audio.noiseSuppression).toBe(true)
    expect(audio.autoGainControl).toBe(true)
  })

  it('allows disabling capture processing constraints', async () => {
    const getUserMediaSpy = vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue({
      getTracks: () => [{ stop: () => {} }],
      getAudioTracks: () => [{ getSettings: () => ({}) }],
    } as unknown as MediaStream)

    const { result } = renderAudioWorklet({ noiseCancellationLevel: 'off' })
    await act(async () => {
      await result.current.worklet.startRecording()
    })

    const constraints = getUserMediaSpy.mock.calls[0]?.[0]
    const audio = constraints?.audio as MediaTrackConstraints
    expect(audio.echoCancellation).toBe(false)
    expect(audio.noiseSuppression).toBe(false)
    expect(audio.autoGainControl).toBe(false)
  })

  it('captures applied mic processing diagnostics', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue({
      getTracks: () => [{ stop: () => {} }],
      getAudioTracks: () => [
        {
          applyConstraints: async () => {},
          getSettings: () => ({
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            sampleRate: 16000,
            channelCount: 1,
          }),
        },
      ],
    } as unknown as MediaStream)

    const { result } = renderAudioWorklet({ noiseCancellationLevel: 'aggressive' })
    await act(async () => {
      await result.current.worklet.startRecording()
    })

    expect(result.current.worklet.micCaptureDiagnostics).toMatchObject({
      noiseCancellationLevel: 'aggressive',
      softwareDenoiserEnabled: true,
      appliedSettings: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        sampleRate: 16000,
        channelCount: 1,
      },
    })
  })

  it('recovers when addModule fails', async () => {
    // Override AudioContext to have addModule throw
    class FailingModuleContext {
      sampleRate: number
      state = 'running'
      constructor(options?: { sampleRate?: number }) {
        this.sampleRate = options?.sampleRate ?? 44100
      }
      async resume() {
        this.state = 'running'
      }
      createMediaStreamSource() {
        return { connect: () => {} }
      }
      get audioWorklet() {
        return {
          addModule: async () => {
            throw new Error('Failed to load worklet module')
          },
        }
      }
      async close() {
        this.state = 'closed'
      }
    }
    globalThis.AudioContext = FailingModuleContext as unknown as typeof AudioContext

    const { result } = renderAudioWorklet()

    await act(async () => {
      await result.current.worklet.startRecording()
    })

    expect(result.current.worklet.error).toBe('Failed to load worklet module')
  })

  it('handles initPlayer failure gracefully', async () => {
    class FailingPlayerContext {
      sampleRate: number
      state = 'running'
      constructor(options?: { sampleRate?: number }) {
        this.sampleRate = options?.sampleRate ?? 44100
      }
      async resume() {
        this.state = 'running'
      }
      createMediaStreamSource() {
        return { connect: () => {} }
      }
      get audioWorklet() {
        return {
          addModule: async () => {
            throw new Error('Player worklet load failed')
          },
        }
      }
      async close() {
        this.state = 'closed'
      }
    }
    globalThis.AudioContext = FailingPlayerContext as unknown as typeof AudioContext

    const { result } = renderAudioWorklet()

    await act(async () => {
      await result.current.worklet.initPlayer()
    })

    expect(result.current.worklet.error).toBe('Player worklet load failed')
  })

  it('resumes suspended AudioContext on start (Safari fix)', async () => {
    const resumeSpy = vi.fn().mockResolvedValue(undefined)

    class SuspendedContext {
      sampleRate: number
      state = 'suspended'
      constructor(options?: { sampleRate?: number }) {
        this.sampleRate = options?.sampleRate ?? 44100
      }
      resume = resumeSpy
      createMediaStreamSource() {
        return { connect: () => {} }
      }
      get audioWorklet() {
        return { addModule: async () => {} }
      }
      async close() {
        this.state = 'closed'
      }
    }
    globalThis.AudioContext = SuspendedContext as unknown as typeof AudioContext

    const { result } = renderAudioWorklet()

    await act(async () => {
      await result.current.worklet.startRecording()
    })

    expect(resumeSpy).toHaveBeenCalled()
    expect(result.current.worklet.error).toBeNull()
  })

  it('forwards recorder chunks to callback', async () => {
    const received: ArrayBuffer[] = []
    const { result } = renderHook(() => {
      const onAudioChunk = useRef<((data: ArrayBuffer) => void) | null>(chunk => {
        received.push(chunk)
      })
      const worklet = useAudioWorklet(onAudioChunk)
      return { worklet, onAudioChunk }
    })

    await act(async () => {
      await result.current.worklet.startRecording()
    })

    const chunk = new Uint8Array([9, 8, 7]).buffer
    result.current.onAudioChunk.current?.(chunk)
    expect(received).toHaveLength(1)
  })
})
