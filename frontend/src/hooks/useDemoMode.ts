import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ServerMessage } from '../types'
import { ELECTRONICS_DEMO_STEPS, type DemoStep } from '../utils/mockData'

interface UseDemoModeOptions {
  steps?: DemoStep[]
  onEmit?: (message: ServerMessage) => void
}

interface UseDemoModeReturn {
  isPlaying: boolean
  isPaused: boolean
  currentStep: number
  messages: ServerMessage[]
  play: () => void
  pause: () => void
  resume: () => void
  reset: () => void
}

function getStepDelay(steps: DemoStep[], index: number): number {
  if (index <= 0) return Math.max(0, steps[0]?.delayMs ?? 0)
  const current = steps[index]?.delayMs ?? 0
  const previous = steps[index - 1]?.delayMs ?? 0
  return Math.max(0, current - previous)
}

export function useDemoMode(options: UseDemoModeOptions = {}): UseDemoModeReturn {
  const steps = options.steps ?? ELECTRONICS_DEMO_STEPS
  const onEmit = options.onEmit

  const [messages, setMessages] = useState<ServerMessage[]>([])
  const [currentStep, setCurrentStep] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isPaused, setIsPaused] = useState(false)

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const playingRef = useRef(false)
  const pausedRef = useRef(false)
  const stepRef = useRef(0)

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const emitStep = useCallback(
    (index: number) => {
      if (!playingRef.current || pausedRef.current) return
      const step = steps[index]
      if (!step) {
        playingRef.current = false
        pausedRef.current = false
        setIsPlaying(false)
        setIsPaused(false)
        return
      }

      const nextMessage = { ...step.message }
      setMessages(prev => [...prev, nextMessage])
      onEmit?.(nextMessage)

      const nextStep = index + 1
      stepRef.current = nextStep
      setCurrentStep(nextStep)

      if (!steps[nextStep]) {
        playingRef.current = false
        pausedRef.current = false
        setIsPlaying(false)
        setIsPaused(false)
        return
      }

      const delay = getStepDelay(steps, nextStep)
      timerRef.current = setTimeout(() => {
        emitStep(nextStep)
      }, delay)
    },
    [onEmit, steps],
  )

  const play = useCallback(() => {
    if (playingRef.current) return
    playingRef.current = true
    pausedRef.current = false
    setIsPlaying(true)
    setIsPaused(false)
    clearTimer()
    const delay = getStepDelay(steps, stepRef.current)
    timerRef.current = setTimeout(() => {
      emitStep(stepRef.current)
    }, delay)
  }, [clearTimer, emitStep, steps])

  const pause = useCallback(() => {
    if (!playingRef.current || pausedRef.current) return
    pausedRef.current = true
    setIsPaused(true)
    clearTimer()
  }, [clearTimer])

  const resume = useCallback(() => {
    if (!playingRef.current || !pausedRef.current) return
    pausedRef.current = false
    setIsPaused(false)
    clearTimer()
    const delay = getStepDelay(steps, stepRef.current)
    timerRef.current = setTimeout(() => {
      emitStep(stepRef.current)
    }, delay)
  }, [clearTimer, emitStep, steps])

  const reset = useCallback(() => {
    clearTimer()
    playingRef.current = false
    pausedRef.current = false
    stepRef.current = 0
    setMessages([])
    setCurrentStep(0)
    setIsPlaying(false)
    setIsPaused(false)
  }, [clearTimer])

  useEffect(() => {
    return () => {
      clearTimer()
    }
  }, [clearTimer])

  return useMemo(
    () => ({
      isPlaying,
      isPaused,
      currentStep,
      messages,
      play,
      pause,
      resume,
      reset,
    }),
    [currentStep, isPaused, isPlaying, messages, pause, play, reset, resume],
  )
}
