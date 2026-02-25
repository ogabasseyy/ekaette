import type { ServerMessage, ServerMessageType } from '../types'

export interface DemoStep {
  delayMs: number
  message: ServerMessage
}

const messageTypes: ServerMessageType[] = [
  'transcription',
  'audio',
  'valuation_result',
  'booking_confirmation',
  'product_recommendation',
  'image_received',
  'agent_transfer',
  'error',
  'interrupted',
  'session_started',
  'memory_recall',
  'agent_status',
]

function hasType(value: unknown): value is { type: string } {
  return Boolean(value) && typeof value === 'object' && 'type' in (value as object)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object'
}

export function isServerMessage(value: unknown): value is ServerMessage {
  if (!hasType(value)) return false
  if (!messageTypes.includes(value.type as ServerMessageType)) return false
  const data = value as Record<string, unknown>

  switch (data.type) {
    case 'transcription':
      return (
        (data.role === 'user' || data.role === 'agent') &&
        typeof data.text === 'string' &&
        typeof data.partial === 'boolean'
      )
    case 'audio':
      return typeof data.data === 'string'
    case 'valuation_result':
      return (
        typeof data.deviceName === 'string' &&
        ['Excellent', 'Good', 'Fair', 'Poor'].includes(String(data.condition)) &&
        typeof data.price === 'number' &&
        typeof data.currency === 'string' &&
        typeof data.details === 'string' &&
        typeof data.negotiable === 'boolean'
      )
    case 'booking_confirmation':
      return (
        typeof data.confirmationId === 'string' &&
        typeof data.date === 'string' &&
        typeof data.time === 'string' &&
        typeof data.location === 'string' &&
        typeof data.service === 'string'
      )
    case 'product_recommendation':
      return Array.isArray(data.products)
    case 'image_received':
      return data.status === 'analyzing' || data.status === 'complete'
    case 'agent_transfer':
      return typeof data.from === 'string' && typeof data.to === 'string'
    case 'error':
      return typeof data.code === 'string' && typeof data.message === 'string'
    case 'interrupted':
      return typeof data.interrupted === 'boolean'
    case 'session_started':
      return typeof data.sessionId === 'string' && typeof data.industry === 'string'
    case 'memory_recall':
      return typeof data.previousInteractions === 'number'
    case 'agent_status':
      return (
        typeof data.agent === 'string' &&
        ['active', 'idle', 'processing'].includes(String(data.status))
      )
    default:
      return false
  }
}

export const ELECTRONICS_DEMO_STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: {
      type: 'session_started',
      sessionId: 'demo-session-electronics',
      industry: 'electronics',
    },
  },
  {
    delayMs: 400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Hello, I am Ekaette. What device would you like to trade in today?',
      partial: false,
    },
  },
  {
    delayMs: 800,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Please upload a clear photo of the device front and back.',
      partial: false,
    },
  },
  {
    delayMs: 1200,
    message: {
      type: 'image_received',
      status: 'analyzing',
    },
  },
  {
    delayMs: 1600,
    message: {
      type: 'valuation_result',
      deviceName: 'iPhone 14 Pro',
      condition: 'Good',
      price: 185000,
      currency: 'NGN',
      details: 'Minor frame wear, healthy battery, display intact.',
      negotiable: true,
    },
  },
  {
    delayMs: 2000,
    message: {
      type: 'transcription',
      role: 'user',
      text: 'Can we do 195000?',
      partial: false,
    },
  },
  {
    delayMs: 2400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Best I can do is 190000 and free pickup tomorrow.',
      partial: false,
    },
  },
  {
    delayMs: 2800,
    message: {
      type: 'booking_confirmation',
      confirmationId: 'EKA-2026-0421',
      date: '2026-03-14',
      time: '10:00 AM',
      location: 'Lekki Phase 1, Lagos',
      service: 'Doorstep pickup',
    },
  },
  {
    delayMs: 3200,
    message: {
      type: 'agent_transfer',
      from: 'valuation_agent',
      to: 'booking_agent',
    },
  },
  {
    delayMs: 3600,
    message: {
      type: 'agent_status',
      agent: 'booking_agent',
      status: 'idle',
    },
  },
]

export function validateDemoSteps(steps: DemoStep[]): boolean {
  return steps.every(step => step.delayMs >= 0 && isServerMessage(step.message))
}

export function getDemoStep(stepIndex: number): DemoStep | undefined {
  if (!Number.isInteger(stepIndex) || stepIndex < 0) return undefined
  return ELECTRONICS_DEMO_STEPS[stepIndex]
}

export function cloneDemoMessage(stepIndex: number): ServerMessage | undefined {
  const step = getDemoStep(stepIndex)
  if (!step || !isRecord(step.message)) return undefined
  return { ...step.message } as ServerMessage
}
