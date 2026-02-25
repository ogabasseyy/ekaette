import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ServerMessage } from '../types'
import { type DemoStep, ELECTRONICS_DEMO_STEPS } from '../utils/mockData'

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
  const timerStartedAtRef = useRef<number | null>(null)
  const scheduledDelayRef = useRef(0)
  const remainingDelayRef = useRef<number | null>(null)

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    timerStartedAtRef.current = null
    scheduledDelayRef.current = 0
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
      timerStartedAtRef.current = Date.now()
      scheduledDelayRef.current = delay
      remainingDelayRef.current = null
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
    timerStartedAtRef.current = Date.now()
    scheduledDelayRef.current = delay
    remainingDelayRef.current = null
    timerRef.current = setTimeout(() => {
      emitStep(stepRef.current)
    }, delay)
  }, [clearTimer, emitStep, steps])

  const pause = useCallback(() => {
    if (!playingRef.current || pausedRef.current) return
    pausedRef.current = true
    setIsPaused(true)
    if (timerRef.current && timerStartedAtRef.current !== null) {
      const elapsed = Math.max(0, Date.now() - timerStartedAtRef.current)
      remainingDelayRef.current = Math.max(0, scheduledDelayRef.current - elapsed)
    }
    clearTimer()
  }, [clearTimer])

  const resume = useCallback(() => {
    if (!playingRef.current || !pausedRef.current) return
    pausedRef.current = false
    setIsPaused(false)
    clearTimer()
    const delay = remainingDelayRef.current ?? getStepDelay(steps, stepRef.current)
    timerStartedAtRef.current = Date.now()
    scheduledDelayRef.current = delay
    remainingDelayRef.current = null
    timerRef.current = setTimeout(() => {
      emitStep(stepRef.current)
    }, delay)
  }, [clearTimer, emitStep, steps])

  const reset = useCallback(() => {
    clearTimer()
    playingRef.current = false
    pausedRef.current = false
    stepRef.current = 0
    remainingDelayRef.current = null
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
