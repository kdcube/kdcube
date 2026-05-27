/**
 * Domain types for the versatile main chat UI.
 *
 * Moved verbatim from src/App.tsx as the first step of the modular refactor.
 * No behavioural change: every interface, union, and literal here matches the
 * pre-refactor `App.tsx` shape exactly.
 */

import type {
  BannerTone,
  ContinuationKind,
  ConversationSummary,
  StepStatus,
  TurnReaction,
} from '../../service.ts'

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
  rn: string
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
  rn?: string | null
  hostedUri?: string | null
  description?: string | null
  file?: File
}

export interface AdditionalUserMessage {
  id: string
  text: string
  timestamp: number
  attachments: TurnAttachment[]
  continuationKind: Exclude<ContinuationKind, 'regular'>
}

/** A structured context object the host page dropped onto the chat (e.g. a
 *  "Why / What / How KDCube" card dragged from the landing). The chat
 *  recognizes known objects, shows them as removable chips next to the
 *  composer, and folds them into the next turn. A forerunner of a first-class
 *  "context" turn input alongside text + attachments. */
export interface AttachedContext {
  id: string
  kind: string
  label: string
  summary?: string
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
