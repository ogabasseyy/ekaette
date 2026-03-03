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
  'session_ending',
  'memory_recall',
  'agent_status',
  'telemetry',
  'ping',
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
      return (
        Array.isArray(data.products) &&
        data.products.every(
          (p: unknown) =>
            typeof p === 'object' &&
            p !== null &&
            'name' in p &&
            'price' in p &&
            typeof (p as Record<string, unknown>).name === 'string' &&
            typeof (p as Record<string, unknown>).price === 'number',
        )
      )
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
    case 'session_ending':
      return (
        (data.reason === 'go_away' ||
          data.reason === 'session_resumption' ||
          data.reason === 'live_session_ended') &&
        (data.timeLeftMs == null || typeof data.timeLeftMs === 'number') &&
        (data.resumptionToken == null || typeof data.resumptionToken === 'string')
      )
    case 'memory_recall':
      return typeof data.previousInteractions === 'number'
    case 'agent_status':
      return (
        typeof data.agent === 'string' &&
        ['active', 'idle', 'processing'].includes(String(data.status))
      )
    case 'telemetry':
      return (
        typeof data.promptTokens === 'number' &&
        typeof data.completionTokens === 'number' &&
        typeof data.totalTokens === 'number' &&
        typeof data.sessionPromptTokens === 'number' &&
        typeof data.sessionCompletionTokens === 'number' &&
        typeof data.sessionTotalTokens === 'number' &&
        typeof data.sessionCostUsd === 'number'
      )
    case 'ping':
      return typeof data.ts === 'number'
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
      tenantId: 'public',
      industryTemplateId: 'electronics',
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

export const HOTEL_DEMO_STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: {
      type: 'session_started',
      sessionId: 'demo-session-hotel',
      industry: 'hotel',
      tenantId: 'public',
      industryTemplateId: 'hotel',
    },
  },
  {
    delayMs: 400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Welcome to Ekaette Suites. How can I assist with your stay today?',
      partial: false,
    },
  },
  {
    delayMs: 800,
    message: {
      type: 'transcription',
      role: 'user',
      text: 'I need a room for two nights starting Friday.',
      partial: false,
    },
  },
  {
    delayMs: 1200,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Let me check availability for you. We have a Deluxe Suite available at ₦45,000 per night.',
      partial: false,
    },
  },
  {
    delayMs: 1600,
    message: {
      type: 'booking_confirmation',
      confirmationId: 'HTL-2026-0087',
      date: '2026-03-14',
      time: '2:00 PM',
      location: 'Ekaette Suites, Victoria Island',
      service: 'Deluxe Suite — 2 nights',
    },
  },
  {
    delayMs: 2000,
    message: {
      type: 'agent_status',
      agent: 'booking_agent',
      status: 'idle',
    },
  },
]

export const AUTOMOTIVE_DEMO_STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: {
      type: 'session_started',
      sessionId: 'demo-session-automotive',
      industry: 'automotive',
      tenantId: 'public',
      industryTemplateId: 'automotive',
    },
  },
  {
    delayMs: 400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Welcome to the Automotive Service Lane. Do you need an inspection or trade-in estimate?',
      partial: false,
    },
  },
  {
    delayMs: 800,
    message: {
      type: 'transcription',
      role: 'user',
      text: 'I want to get my car inspected for a trade-in.',
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
      type: 'transcription',
      role: 'agent',
      text: 'The inspection shows good overall condition. Minor tire wear noted.',
      partial: false,
    },
  },
  {
    delayMs: 2000,
    message: {
      type: 'agent_transfer',
      from: 'vision_agent',
      to: 'valuation_agent',
    },
  },
  {
    delayMs: 2400,
    message: {
      type: 'booking_confirmation',
      confirmationId: 'AUT-2026-0033',
      date: '2026-03-15',
      time: '9:00 AM',
      location: 'Ekaette Auto Center, Ikeja',
      service: 'Vehicle inspection & trade-in appraisal',
    },
  },
]

export const FASHION_DEMO_STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: {
      type: 'session_started',
      sessionId: 'demo-session-fashion',
      industry: 'fashion',
      tenantId: 'public',
      industryTemplateId: 'fashion',
    },
  },
  {
    delayMs: 400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Welcome to the Fashion Client Studio. Looking for style recommendations today?',
      partial: false,
    },
  },
  {
    delayMs: 800,
    message: {
      type: 'transcription',
      role: 'user',
      text: 'Yes, I need an outfit for a formal event.',
      partial: false,
    },
  },
  {
    delayMs: 1200,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'I have some great options. Let me pull up our catalog.',
      partial: false,
    },
  },
  {
    delayMs: 1600,
    message: {
      type: 'product_recommendation',
      products: [
        {
          name: 'Classic Navy Blazer',
          price: 85000,
          currency: 'NGN',
          available: true,
          description: 'Tailored fit, Italian wool blend.',
        },
        {
          name: 'Silk Evening Dress',
          price: 120000,
          currency: 'NGN',
          available: true,
          description: 'Floor-length, emerald green.',
        },
      ],
    },
  },
  {
    delayMs: 2000,
    message: {
      type: 'agent_status',
      agent: 'catalog_agent',
      status: 'idle',
    },
  },
]

export const TELECOM_DEMO_STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: {
      type: 'session_started',
      sessionId: 'demo-session-telecom',
      industry: 'telecom',
      tenantId: 'public',
      industryTemplateId: 'telecom',
    },
  },
  {
    delayMs: 400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Welcome to Telecom Support. How can I help with your plan today?',
      partial: false,
    },
  },
  {
    delayMs: 800,
    message: {
      type: 'transcription',
      role: 'user',
      text: 'I want to compare data plans.',
      partial: false,
    },
  },
  {
    delayMs: 1200,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Our Premium plan offers 50GB at ₦5,000/month. The Basic plan is 10GB at ₦2,000/month.',
      partial: false,
    },
  },
  {
    delayMs: 1600,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Your current usage suggests the Premium plan would be the best value.',
      partial: false,
    },
  },
  {
    delayMs: 2000,
    message: {
      type: 'agent_status',
      agent: 'support_agent',
      status: 'idle',
    },
  },
]

export const AVIATION_DEMO_STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: {
      type: 'session_started',
      sessionId: 'demo-session-aviation',
      industry: 'aviation',
      tenantId: 'public',
      industryTemplateId: 'aviation-support',
    },
  },
  {
    delayMs: 400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Welcome to Aviation Support. I can help with flight status and policies.',
      partial: false,
    },
  },
  {
    delayMs: 800,
    message: {
      type: 'transcription',
      role: 'user',
      text: 'What is the status of flight EK-204?',
      partial: false,
    },
  },
  {
    delayMs: 1200,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Flight EK-204 is on time. Departure at 3:45 PM from Terminal 2, Gate B12.',
      partial: false,
    },
  },
  {
    delayMs: 1600,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Would you like to know about baggage policies or anything else?',
      partial: false,
    },
  },
  {
    delayMs: 2000,
    message: {
      type: 'agent_status',
      agent: 'support_agent',
      status: 'idle',
    },
  },
]

/** Generic support demo used as fallback for unknown template IDs. */
export const GENERIC_SUPPORT_DEMO_STEPS: DemoStep[] = [
  {
    delayMs: 0,
    message: {
      type: 'session_started',
      sessionId: 'demo-session-generic',
      industry: 'support',
      tenantId: 'public',
      industryTemplateId: 'generic-support',
    },
  },
  {
    delayMs: 400,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Hello, I am Ekaette. How can I assist you today?',
      partial: false,
    },
  },
  {
    delayMs: 800,
    message: {
      type: 'transcription',
      role: 'user',
      text: 'I have a question about your services.',
      partial: false,
    },
  },
  {
    delayMs: 1200,
    message: {
      type: 'transcription',
      role: 'agent',
      text: 'Of course! I am happy to help. What would you like to know?',
      partial: false,
    },
  },
  {
    delayMs: 1600,
    message: {
      type: 'agent_status',
      agent: 'support_agent',
      status: 'idle',
    },
  },
]

/** Mapping from industry template ID to demo steps. */
export const DEMO_STEPS_BY_TEMPLATE: Record<string, DemoStep[]> = {
  electronics: ELECTRONICS_DEMO_STEPS,
  hotel: HOTEL_DEMO_STEPS,
  automotive: AUTOMOTIVE_DEMO_STEPS,
  fashion: FASHION_DEMO_STEPS,
  telecom: TELECOM_DEMO_STEPS,
  aviation: AVIATION_DEMO_STEPS, // legacy alias during template-id migration
  'aviation-support': AVIATION_DEMO_STEPS,
}

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
  return structuredClone(step.message) as ServerMessage
}
