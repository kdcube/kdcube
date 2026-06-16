/**
 * Chat domain state — the conversation/turn/artifact/timeline model and the
 * `ChatState` the engine exposes to hosts.
 *
 * Ported from the widget's `features/chat/chatTypes.ts`; the only change is that
 * the four wire types come from `./protocol.ts` instead of the `service.ts`
 * barrel. Otherwise verbatim.
 */
import type {
  BannerTone,
  ConversationSummary,
  StepStatus,
  TurnReaction,
} from './protocol.ts'

export type ConnectionState = 'booting' | 'connecting' | 'connected' | 'disconnected'
export type TurnState = 'pending' | 'running' | 'completed' | 'error'
export type TurnTab = 'chat' | 'overview' | 'timeline' | 'steps' | 'links' | 'files' | 'canvases'

export interface Banner {
  id: string
  tone: BannerTone
  text: string
  /** Where the notice renders. `'composer'` = right above the chat input
   *  (chat-send / rate-limit / economic notices). `'top'` (default) =
   *  app-level strip at the top (boot/connection, list errors). */
  placement?: 'top' | 'composer'
}

export interface TurnStep {
  step: string
  title?: string | null
  status: StepStatus
  timestamp: number
  error?: string
  markdown?: string
  agent?: string | null
  data?: Record<string, unknown>
}

export interface LinkArtifact {
  kind: 'citation'
  timestamp: number
  url: string
  title?: string | null
  body?: string | null
  favicon?: string | null
}

export interface FileArtifact {
  kind: 'file'
  timestamp: number
  filename: string
  objectRef: string
  logicalPath?: string | null
  mime?: string | null
  description?: string | null
}

export interface TimelineArtifact {
  kind: 'timeline'
  timestamp: number
  name: string
  markdown: string
}

export interface CanvasArtifact {
  kind: 'canvas'
  timestamp: number
  name: string
  title?: string | null
  format?: string | null
  content: string
}

export interface WebSearchItem {
  url: string
  title?: string | null
  body?: string | null
  favicon?: string | null
  provider?: string
  weightedScore?: number
}

export interface WebSearchArtifact {
  kind: 'web_search'
  timestamp: number
  searchId: string
  name: string
  title?: string | null
  objective?: string
  queries: string[]
  items: WebSearchItem[]
  reportContent?: string | null
}

export interface WebFetchItem {
  url: string
  status?: 'success' | 'timeout' | 'paywall' | 'error'
  mime?: string
  favicon?: string
  content_length?: number
  published_time_iso?: string
  modified_time_iso?: string
}

export interface WebFetchArtifact {
  kind: 'web_fetch'
  timestamp: number
  executionId: string
  name: string
  title?: string | null
  items: WebFetchItem[]
}

export interface NamedServiceSearchItem {
  id: string
  kind: string
  label: string
  title?: string | null
  summary?: string | null
  ref?: string | null
  object_ref?: string | null
  namespace?: string | null
  search_scope?: string | null
  object_kind?: string | null
  event_source_id?: string | null
  mime?: string | null
  filename?: string | null
  score?: number
  data?: Record<string, unknown>
  [key: string]: unknown
}

export interface NamedServiceSearchArtifact {
  kind: 'named_service_search'
  timestamp: number
  searchId: string
  name: string
  title?: string | null
  namespace?: string
  searchScope?: string
  query?: string
  items: NamedServiceSearchItem[]
}

export interface CodeExecContractItem {
  filename: string
  description?: string | null
  mime?: string | null
}

export interface CodeExecStatus {
  status?: 'gen' | 'exec' | 'done' | 'error'
  error?: Record<string, string>
}

export interface CodeExecArtifact {
  kind: 'code_exec'
  timestamp: number
  executionId: string
  name?: string
  title?: string | null
  objective?: string
  language?: string
  program?: string
  contract?: CodeExecContractItem[]
  status?: CodeExecStatus
}

export interface ServiceErrorArtifact {
  kind: 'service_error'
  timestamp: number
  message: string
}

export interface TurnAttachment {
  id: string
  name: string
  size?: number | null
  mime?: string | null
  logicalPath?: string | null
  description?: string | null
  file?: File
}

export interface AdditionalUserMessage {
  id: string
  text: string
  timestamp: number
  attachments: TurnAttachment[]
  eventType: string
}

/** A structured context object dropped or pinned into chat.
 *  Canvas and wizard chips attach the whole current state/snapshot. Canvas
 *  card chips focus individual pins from the board into the next prompt. */
export interface AttachedContext {
  id: string
  kind: string
  label: string
  summary?: string
  ref?: string
  logicalPath?: string
  hostedUri?: string
  mime?: string
  canvasId?: string
  canvasName?: string
  revision?: number
  cardId?: string
  cardType?: string
  selected?: boolean
  eventSourceId?: string
  surface?: string
  data?: Record<string, unknown>
}

export type TimelineEntryKind = 'lifecycle' | 'answer' | 'thinking' | 'timeline' | 'canvas' | 'subsystem' | 'error'
export type TimelineEntryFormat = 'markdown' | 'text' | 'json' | 'code'

export interface TimelineEntry {
  id: string
  timestamp: number
  kind: TimelineEntryKind
  title: string
  body?: string
  format?: TimelineEntryFormat
  agent?: string | null
  status?: string | null
}

export type Artifact =
  | LinkArtifact
  | FileArtifact
  | TimelineArtifact
  | CanvasArtifact
  | WebSearchArtifact
  | WebFetchArtifact
  | NamedServiceSearchArtifact
  | CodeExecArtifact
  | ServiceErrorArtifact

export interface ChatTurn {
  id: string
  state: TurnState
  createdAt: number
  userMessage: string
  userAttachments: TurnAttachment[]
  additionalUserMessages: AdditionalUserMessage[]
  answer: string
  error?: string | null
  steps: Record<string, TurnStep>
  artifacts: Artifact[]
  timeline: TimelineEntry[]
  followups: string[]
  /* Turn accounting, populated at the end of the turn: cost from the
   * `accounting.usage` event (data.cost_total_usd) and wall time from the
   * `chat.turn.summary` event (data.elapsed_ms). Null until those arrive. */
  costUsd?: number | null
  elapsedMs?: number | null
}

export interface ChatState {
  connection: ConnectionState
  sessionId: string | null
  conversationId: string | null
  conversationTitle: string | null
  composerText: string
  composerFiles: File[]
  composerContexts: AttachedContext[]
  turns: ChatTurn[]
  banners: Banner[]
  /** Signed-in user's saved reaction per assistant turn id. */
  feedback: Record<string, TurnReaction>
  inputLocked: boolean
  inputLockMessage: string | null
  conversations: ConversationSummary[]
  conversationsLoading: boolean
  conversationsError: string | null
  conversationLoadingId: string | null
  conversationDeletingId: string | null
}

export const initialState: ChatState = {
  connection: 'booting',
  sessionId: null,
  conversationId: null,
  conversationTitle: null,
  composerText: '',
  composerFiles: [],
  composerContexts: [],
  turns: [],
  banners: [],
  feedback: {},
  inputLocked: false,
  inputLockMessage: null,
  conversations: [],
  conversationsLoading: false,
  conversationsError: null,
  conversationLoadingId: null,
  conversationDeletingId: null,
}
