import { type MutableRefObject, useCallback, useEffect, useRef, useState } from 'react'

interface UseAudioWorkletReturn {
  startRecording: () => Promise<void>
  initPlayer: () => Promise<void>
  playAudioChunk: (data: ArrayBuffer) => void
  clearPlaybackBuffer: () => void
  recoverAudioContexts: () => Promise<void>
  stop: () => void
  error: string | null
}

type SpeechActivityState = 'start' | 'end'

interface UseAudioWorkletOptions {
  onSpeechActivity?: (state: SpeechActivityState) => void
  onPlaybackStats?: (stats: PlaybackStats) => void
}

export interface PlaybackStats {
  type: 'playback_stats'
  availableSamples: number
  isBuffering: boolean
  startupPrebufferSamples: number
  currentRebufferSamples: number
  underrunCount: number
}

export function useAudioWorklet(
  onAudioChunk: MutableRefObject<((data: ArrayBuffer) => void) | null>,
  options: UseAudioWorkletOptions = {},
): UseAudioWorkletReturn {
  // FIX: Separate contexts for different sample rates
  const recorderCtxRef = useRef<AudioContext | null>(null)
  const playerCtxRef = useRef<AudioContext | null>(null)
  const recorderNodeRef = useRef<AudioWorkletNode | null>(null)
  const playerNodeRef = useRef<AudioWorkletNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [error, setError] = useState<string | null>(null)
  const onSpeechActivityRef = useRef<UseAudioWorkletOptions['onSpeechActivity']>(
    options.onSpeechActivity,
  )
  const onPlaybackStatsRef = useRef<UseAudioWorkletOptions['onPlaybackStats']>(
    options.onPlaybackStats,
  )

  useEffect(() => {
    onSpeechActivityRef.current = options.onSpeechActivity
  }, [options.onSpeechActivity])

  useEffect(() => {
    onPlaybackStatsRef.current = options.onPlaybackStats
    if (playerNodeRef.current) {
      playerNodeRef.current.port.postMessage({
        command: 'enable_playback_stats',
        enabled: Boolean(options.onPlaybackStats),
      })
    }
  }, [options.onPlaybackStats])

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
        if (
          chunk &&
          typeof chunk === 'object' &&
          'type' in chunk &&
          chunk.type === 'vad' &&
          (chunk.state === 'speech_start' || chunk.state === 'speech_end')
        ) {
          if (chunk.state === 'speech_start') {
            // Signal the server that user started speaking (for manual VAD).
            // Do NOT clear playback buffer here — that causes voice breaks.
            // The server sends an 'interrupted' message when the agent should
            // actually stop; clearPlaybackBuffer is called from App.tsx on that event.
            onSpeechActivityRef.current?.('start')
          } else {
            onSpeechActivityRef.current?.('end')
          }
          return
        }
        if (chunk instanceof ArrayBuffer) {
          onAudioChunk.current?.(chunk)
          return
        }
        if (chunk instanceof Float32Array) {
          // Fallback for non-standard recorder processors that emit Float32 PCM.
          // Convert here so onAudioChunk.current consumers always receive Int16 PCM buffers.
          const pcm16 = new Int16Array(chunk.length)
          for (let i = 0; i < chunk.length; i++) {
            const sample = Math.max(-1, Math.min(1, chunk[i]))
            pcm16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
          }
          onAudioChunk.current?.(pcm16.buffer)
          return
        }
        if (ArrayBuffer.isView(chunk)) {
          const view = chunk as ArrayBufferView
          const normalized = new Uint8Array(view.byteLength)
          normalized.set(new Uint8Array(view.buffer, view.byteOffset, view.byteLength))
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
      player.port.onmessage = (event: MessageEvent) => {
        const payload = event.data
        if (payload && typeof payload === 'object' && payload.type === 'playback_stats') {
          onPlaybackStatsRef.current?.(payload as PlaybackStats)
        }
      }
      player.port.postMessage({
        command: 'enable_playback_stats',
        enabled: Boolean(onPlaybackStatsRef.current),
      })
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

  const recoverAudioContexts = useCallback(async () => {
    const resumes: Promise<unknown>[] = []
    if (playerCtxRef.current?.state === 'suspended') {
      resumes.push(playerCtxRef.current.resume())
    }
    if (recorderCtxRef.current?.state === 'suspended') {
      resumes.push(recorderCtxRef.current.resume())
    }
    if (resumes.length > 0) {
      try {
        await Promise.all(resumes)
      } catch {
        // Ignore resume races; normal playback/recording may still recover on next gesture.
      }
    }
  }, [])

  const stop = useCallback(() => {
    // Gain ramp-down on player to avoid audio pops (pattern from live-api-web-console).
    const playerCtx = playerCtxRef.current
    const playerNode = playerNodeRef.current
    if (playerCtx && playerNode) {
      try {
        const gain = playerCtx.createGain()
        gain.gain.setValueAtTime(1, playerCtx.currentTime)
        gain.gain.linearRampToValueAtTime(0, playerCtx.currentTime + 0.05)
        playerNode.disconnect()
        playerNode.connect(gain)
        gain.connect(playerCtx.destination)
        // Close after ramp completes
        setTimeout(() => {
          playerNode.disconnect()
          if (playerNodeRef.current === playerNode) {
            playerNodeRef.current = null
          }
          playerCtx.close().catch(() => {})
          if (playerCtxRef.current === playerCtx) {
            playerCtxRef.current = null
          }
        }, 80)
      } catch {
        // Fallback: immediate close
        playerNode.disconnect()
        if (playerNodeRef.current === playerNode) {
          playerNodeRef.current = null
        }
        playerCtx.close().catch(() => {})
        if (playerCtxRef.current === playerCtx) {
          playerCtxRef.current = null
        }
      }
    } else {
      playerNodeRef.current?.disconnect()
      playerNodeRef.current = null
      playerCtxRef.current?.close().catch(() => {})
      playerCtxRef.current = null
    }

    // Stop microphone tracks
    streamRef.current?.getTracks().forEach(track => {
      track.stop()
    })
    streamRef.current = null

    // Disconnect recorder
    recorderNodeRef.current?.disconnect()
    recorderNodeRef.current = null

    // Close recorder context
    recorderCtxRef.current?.close().catch(() => {})
    recorderCtxRef.current = null
  }, [])

  return {
    startRecording,
    initPlayer,
    playAudioChunk,
    clearPlaybackBuffer,
    recoverAudioContexts,
    stop,
    error,
  }
}
