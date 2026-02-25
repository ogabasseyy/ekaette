// ═══ Server → Client Messages ═══

export type ServerMessageType =
  | 'transcription'
  | 'audio'
  | 'valuation_result'
  | 'booking_confirmation'
  | 'product_recommendation'
  | 'image_received'
  | 'agent_transfer'
  | 'error'
  | 'interrupted'
  | 'session_started'
  | 'session_ending'
  | 'memory_recall'
  | 'agent_status'
  | 'telemetry'
  | 'ping'

export interface TranscriptionMessage {
  type: 'transcription'
  role: 'user' | 'agent'
  text: string
  partial: boolean
}

export interface AudioMessage {
  type: 'audio'
  data: string // base64 PCM
}

export interface ValuationResult {
  type: 'valuation_result'
  deviceName: string
  condition: 'Excellent' | 'Good' | 'Fair' | 'Poor'
  price: number
  currency: string
  details: string
  negotiable: boolean
}

export interface BookingConfirmation {
  type: 'booking_confirmation'
  confirmationId: string
  date: string
  time: string
  location: string
  service: string
}

export interface ProductRecommendation {
  type: 'product_recommendation'
  products: Array<{
    name: string
    price: number
    currency: string
    available: boolean
    description: string
  }>
}

export interface ImageReceivedMessage {
  type: 'image_received'
  status: 'analyzing' | 'complete'
  previewUrl?: string
}

export interface AgentTransferMessage {
  type: 'agent_transfer'
  from: string
  to: string
}

export interface ErrorMessage {
  type: 'error'
  code: string
  message: string
}

export interface InterruptedMessage {
  type: 'interrupted'
  interrupted: boolean
}

export interface SessionStartedMessage {
  type: 'session_started'
  sessionId: string
  industry: string
  companyId?: string
  voice?: string
  voiceChangeRequiresReconnect?: boolean
  manualVadActive?: boolean
  vadMode?: 'auto' | 'manual'
}

export interface MemoryRecallMessage {
  type: 'memory_recall'
  customerName?: string
  previousInteractions: number
}

export interface AgentStatusMessage {
  type: 'agent_status'
  agent: string
  status: 'active' | 'idle' | 'processing'
}

export interface TelemetryMessage {
  type: 'telemetry'
  promptTokens: number
  completionTokens: number
  totalTokens: number
  sessionPromptTokens: number
  sessionCompletionTokens: number
  sessionTotalTokens: number
  sessionCostUsd: number
}

export interface SessionEndingMessage {
  type: 'session_ending'
  reason: 'go_away' | 'session_resumption' | 'live_session_ended'
  timeLeftMs?: number | null
  resumptionToken?: string
}

export interface PingMessage {
  type: 'ping'
  ts: number
}

export type ServerMessage =
  | TranscriptionMessage
  | AudioMessage
  | ValuationResult
  | BookingConfirmation
  | ProductRecommendation
  | ImageReceivedMessage
  | AgentTransferMessage
  | ErrorMessage
  | InterruptedMessage
  | SessionStartedMessage
  | SessionEndingMessage
  | MemoryRecallMessage
  | AgentStatusMessage
  | TelemetryMessage
  | PingMessage

// ═══ Client → Server Messages ═══

export interface TextClientMessage {
  type: 'text'
  text: string
}

export interface ImageClientMessage {
  type: 'image'
  data: string // base64
  mimeType: string
}

export interface NegotiateClientMessage {
  type: 'negotiate'
  counterOffer: number
  action: 'accept' | 'decline' | 'counter'
}

export interface ActivityStartClientMessage {
  type: 'activity_start'
}

export interface ActivityEndClientMessage {
  type: 'activity_end'
}

export type ClientMessage =
  | TextClientMessage
  | ImageClientMessage
  | NegotiateClientMessage
  | ActivityStartClientMessage
  | ActivityEndClientMessage

// ═══ Connection State ═══

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting'

export type TransportMode = 'backend-proxy' | 'direct-live'

// ═══ Industry ═══

export type Industry = 'electronics' | 'hotel' | 'automotive' | 'fashion'
