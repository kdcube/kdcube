/**
 * Wire types for the versatile main chat API.
 *
 * Moved verbatim from src/service.ts as part of the Wave 4 modular
 * refactor. Every type is re-exported by service.ts (now a thin barrel)
 * so existing `from './service.ts'` imports continue to work.
 */

export type BannerTone = 'info' | 'warning' | 'error'
export type StepStatus = 'started' | 'running' | 'completed' | 'error' | 'skipped'

export interface ServiceInfo {
  request_id: string
  tenant?: string | null
  project?: string | null
  user?: string | null
}

export interface ConversationInfo {
  session_id: string
  conversation_id: string
  turn_id: string
}

export interface BaseEnvelope {
  type: string
  timestamp: string
  service: ServiceInfo
  conversation: ConversationInfo
  event: {
    agent?: string | null
    step: string
    status: StepStatus
    title?: string | null
    markdown?: string
  }
  data?: Record<string, unknown>
}

export interface ChatStartEnvelope extends BaseEnvelope {
  type: 'chat.start'
}

export interface ChatStepEnvelope extends BaseEnvelope {
  type: 'chat.step'
}

export interface ChatDeltaEnvelope extends BaseEnvelope {
  type: 'chat.delta'
  delta: {
    text: string
    marker: 'thinking' | 'answer' | 'canvas' | 'timeline_text' | 'subsystem' | string
    index: number
    completed?: boolean
  }
  extra: Record<string, unknown>
}

export interface ChatCompleteEnvelope extends BaseEnvelope {
  type: 'chat.complete'
  data: {
    final_answer?: string
    followups?: string[]
    error_message?: string
    [key: string]: unknown
  }
}

export interface ChatErrorEnvelope extends BaseEnvelope {
  type: 'chat.error'
  data: {
    error?: string
    [key: string]: unknown
  }
}

export interface ConvStatusEnvelope {
  type: 'conv.status'
  timestamp: string
  conversation: ConversationInfo
  data: {
    state: 'idle' | 'in_progress' | 'error'
    current_turn_id?: string | null
  }
}

export interface RateLimitPayload {
  retry_after_sec?: number | null
  reset_text?: string | null
  user_message?: string | null
  notification_type?: BannerTone | null
}

export interface ChatServiceEnvelope {
  type: string
  data?: {
    rate_limit?: RateLimitPayload
    user_message?: string
    notification_type?: BannerTone
    reason?: string
    has_personal_budget?: boolean
    usd_short?: number
    [key: string]: unknown
  }
}

export interface ConversationSummary {
  id: string
  title?: string | null
  startedAt?: number | null
  lastActivityAt?: number | null
}

interface ConversationListResponse {
  items?: Array<{
    conversation_id: string
    last_activity_at?: string | null
    started_at?: string | null
    title?: string | null
  }>
}

export interface ConversationArtifactDTO {
  type: string
  ts?: string
  data?: {
    text?: string
    payload?: Record<string, unknown>
    meta?: Record<string, unknown>
  }
}

export interface ConversationTurnDTO {
  turn_id: string
  artifacts: ConversationArtifactDTO[]
}

export interface ConversationDTO {
  conversation_id: string
  conversation_title?: string | null
  bundle_id?: string | null
  turns: ConversationTurnDTO[]
}

export interface ChatHistoryItem {
  role: 'user'
  content: string
  timestamp: string
  id: number
}

export type ContinuationKind = 'regular' | 'followup' | 'steer'

export interface OpenChatStreamOptions {
  sessionId?: string | null
  timeoutMs?: number
  onChatStart?: (payload: ChatStartEnvelope) => void
  onChatStep?: (payload: ChatStepEnvelope) => void
  onChatDelta?: (payload: ChatDeltaEnvelope) => void
  onChatComplete?: (payload: ChatCompleteEnvelope) => void
  onChatError?: (payload: ChatErrorEnvelope) => void
  onConversationStatus?: (payload: ConvStatusEnvelope) => void
  onChatService?: (payload: ChatServiceEnvelope) => void
  onDisconnect?: () => void
}

export interface OpenChatStreamResult {
  eventSource: EventSource
  sessionId: string
  streamId: string
}

export interface SubmitChatMessageParams {
  streamId: string
  bundleId: string
  conversationId?: string | null
  turnId?: string
  text: string
  files: File[]
  chatHistory: ChatHistoryItem[]
  messageKind?: ContinuationKind
  continuationKind?: ContinuationKind
  activeTurnId?: string
  targetTurnId?: string
  followup?: boolean
  steer?: boolean
}

interface SubmitChatMessageApiResponse {
  status?: string
  task_id?: string
  session_id?: string
  conversation_id?: string
  turn_id?: string
  conversation_created?: boolean
  user_type?: string
  message_kind?: string | null
  active_turn_id?: string | null
  target_turn_id?: string | null
  queued_turn_id?: string | null
  event_id?: string | null
  external_event_sequence?: number | null
  live_owner_detected?: boolean | null
  message?: string
}

export interface SubmitChatMessageResponse {
  status?: string
  taskId?: string
  sessionId?: string
  conversationId: string
  turnId?: string
  conversationCreated: boolean
  userType?: string
  messageKind?: string | null
  activeTurnId?: string | null
  targetTurnId?: string | null
  queuedTurnId?: string | null
  eventId?: string | null
  externalEventSequence?: number | null
  liveOwnerDetected?: boolean | null
  message?: string
}

interface ResourceByRnResponse {
  metadata?: {
    download_url?: string
  }
}
