import { useCallback, useRef, useState, type MutableRefObject } from 'react'

interface UseAudioWorkletReturn {
  startRecording: () => Promise<void>
  initPlayer: () => Promise<void>
  playAudioChunk: (data: ArrayBuffer) => void
  clearPlaybackBuffer: () => void
  stop: () => void
  error: string | null
}

export function useAudioWorklet(
  onAudioChunk: MutableRefObject<((data: ArrayBuffer) => void) | null>,
): UseAudioWorkletReturn {
  // FIX: Separate contexts for different sample rates
  const recorderCtxRef = useRef<AudioContext | null>(null)
  const playerCtxRef = useRef<AudioContext | null>(null)
  const recorderNodeRef = useRef<AudioWorkletNode | null>(null)
  const playerNodeRef = useRef<AudioWorkletNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [error, setError] = useState<string | null>(null)

  const startRecording = useCallback(async () => {
    try {
      setError(null)

      // 16kHz for recording (Gemini Live API expects 16kHz PCM)
      const ctx = new AudioContext({ sampleRate: 16000 })
      recorderCtxRef.current = ctx

      // FIX: Safari — resume suspended context
      if (ctx.state === 'suspended') {
        await ctx.resume()
      }

      await ctx.audioWorklet.addModule('/pcm-recorder-processor.js')

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })
      streamRef.current = stream

      const source = ctx.createMediaStreamSource(stream)
      const recorder = new AudioWorkletNode(ctx, 'pcm-recorder-processor')
      recorderNodeRef.current = recorder

      // Use ref pattern to avoid stale closure
      recorder.port.onmessage = (event: MessageEvent) => {
        const chunk = event.data
        if (chunk instanceof ArrayBuffer) {
          onAudioChunk.current?.(chunk)
          return
        }
        if (ArrayBuffer.isView(chunk)) {
          const view = chunk as ArrayBufferView
          const normalized = new Uint8Array(view.byteLength)
          normalized.set(
            new Uint8Array(view.buffer, view.byteOffset, view.byteLength),
          )
          onAudioChunk.current?.(normalized.buffer)
        }
      }

      source.connect(recorder)
      // FIX: Do NOT connect recorder to ctx.destination — that creates echo feedback!
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Microphone access denied')
    }
  }, [onAudioChunk])

  const initPlayer = useCallback(async () => {
    try {
      setError(null)
      // 24kHz for playback (Gemini Live API sends 24kHz PCM)
      const ctx = new AudioContext({ sampleRate: 24000 })
      playerCtxRef.current = ctx

      // FIX: Safari — resume suspended context
      if (ctx.state === 'suspended') {
        await ctx.resume()
      }

      await ctx.audioWorklet.addModule('/pcm-player-processor.js')
      const player = new AudioWorkletNode(ctx, 'pcm-player-processor')
      playerNodeRef.current = player
      player.connect(ctx.destination)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Audio playback failed')
    }
  }, [])

  const playAudioChunk = useCallback((data: ArrayBuffer) => {
    if (playerNodeRef.current) {
      playerNodeRef.current.port.postMessage(data)
    }
  }, [])

  const clearPlaybackBuffer = useCallback(() => {
    if (playerNodeRef.current) {
      playerNodeRef.current.port.postMessage({ command: 'endOfAudio' })
    }
  }, [])

  const stop = useCallback(() => {
    // Stop microphone tracks
    streamRef.current?.getTracks().forEach(track => track.stop())
    streamRef.current = null

    // Disconnect nodes
    recorderNodeRef.current?.disconnect()
    playerNodeRef.current?.disconnect()
    recorderNodeRef.current = null
    playerNodeRef.current = null

    // Close both contexts
    recorderCtxRef.current?.close()
    playerCtxRef.current?.close()
    recorderCtxRef.current = null
    playerCtxRef.current = null
  }, [])

  return {
    startRecording,
    initPlayer,
    playAudioChunk,
    clearPlaybackBuffer,
    stop,
    error,
  }
}
