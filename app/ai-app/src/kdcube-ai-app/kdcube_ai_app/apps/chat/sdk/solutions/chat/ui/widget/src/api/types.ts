/**
 * Wire types for the reusable chat API.
 *
 * Moved verbatim from src/service.ts as part of the Wave 4 modular
 * refactor. Every type is re-exported by service.ts (now a thin barrel)
 * so existing `from './service.ts'` imports continue to work.
 */

export type BannerTone = 'info' | 'warning' | 'error'
export type StepStatus = 'started' | 'running' | 'completed' | 'error' | 'skipped'
/** A signed-in user's reaction to one assistant turn. `'neutral'` is a
 *  comment-only reaction (server-supported) with no thumb highlighted. */
export type TurnReaction = 'ok' | 'not_ok' | 'neutral'

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

export interface ExternalEvent {
  event_id?: string
  type?: string
  event_source_id: string
  logical_path?: string
  hosted_uri?: string
  reactive?: boolean
  story_id?: string
  agent_id?: string
  payload?: {
    mime?: string
    event?: unknown
    event_ref?: string
    iteration_credit?: number
    [key: string]: unknown
  }
  [key: string]: unknown
}

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
  onDisconnect?: (reason?: string) => void
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
  reactiveEventType?: string
  activeTurnId?: string
  targetTurnId?: string
  payload?: Record<string, unknown>
  target?: Record<string, unknown>
  externalEvents?: ExternalEvent[]
}

export interface ReactContextPreviewParams {
  bundleId: string
  conversationId?: string | null
  turnId?: string
  externalEvents: ExternalEvent[]
  target?: Record<string, unknown>
}

export interface ReactContextPreviewResponse {
  ok?: boolean
  dry_run?: boolean
  conversation_id?: string
  turn_id?: string
  event_count?: number
  block_count?: number
  announce_block_count?: number
  timeline_text?: string
  announce_text?: string
  rendered_text?: string
  debug_paths?: Record<string, string>
  error?: string
  status?: number
}

export type ObjectActionName = 'capabilities' | 'describe' | 'preview' | 'open' | 'download' | 'rehost'

export interface ObjectActionCapabilities {
  preview?: boolean
  open?: boolean
  download?: boolean
  rehost?: boolean
  [key: string]: boolean | undefined
}

export interface ObjectActionUiEvent {
  type?: string
  subject?: string
  request_id?: string
  source?: string
  object_ref?: string
  target_surface?: string
  mode?: string
  title?: string
  [key: string]: unknown
}

export interface ObjectActionResponse {
  ok?: boolean
  action?: string
  ref?: string
  object_ref?: string
  namespace?: string
  resolver?: string
  resolver_status?: string
  capabilities?: ObjectActionCapabilities
  default_open_effect_action?: ObjectActionName
  title?: string
  summary?: string
  mime?: string
  text?: string
  json?: unknown
  download_url?: string
  content_base64?: string
  filename?: string
  size?: number
  ui_event?: ObjectActionUiEvent
  error?: string
  message?: string
  status?: number
  [key: string]: unknown
}

export interface ResolveObjectActionParams {
  objectRef: string
  action: ObjectActionName
  mime?: string | null
  filename?: string | null
  payload?: Record<string, unknown>
}

interface SubmitChatMessageApiResponse {
  status?: string
  task_id?: string
  session_id?: string
  conversation_id?: string
  turn_id?: string
  conversation_created?: boolean
  user_type?: string
  is_continuation?: boolean | null
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
  isContinuation?: boolean | null
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
