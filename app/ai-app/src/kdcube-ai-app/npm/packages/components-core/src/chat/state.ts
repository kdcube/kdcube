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
import type { ConnectionsConsentOpen } from '../shared/index.ts'
import type { AgentCapabilitiesState } from './capabilities.ts'
import { initialCapabilitiesState } from './capabilities.ts'

export type ConnectionState = 'booting' | 'connecting' | 'connected' | 'disconnected'
export type TurnState = 'pending' | 'running' | 'completed' | 'error'
export type TurnTab = 'chat' | 'overview' | 'timeline' | 'steps' | 'links' | 'files' | 'canvases'

export interface Banner {
  id: string
  tone: BannerTone
  text: string
  actionLabel?: string
  actionUrl?: string
  /** Structured Connection-Hub open payload when the banner is a
   *  connected-account consent card; hosts that route `open-connections`
   *  receive it instead of the plain `actionUrl` navigation. */
  consent?: ConnectionsConsentOpen
  /** Consent identity (`provider|sorted-claims`): one banner per provider at a
   *  time (a new consent state supersedes the older banner) and the dismissal
   *  memory key (an identical state stays quiet after dismiss). */
  consentSignature?: string
  /** Tools blocked by the missing claims — the banner's second option lets
   *  the user turn these off instead of granting the access. */
  consentTools?: string[]
  /** The claims the consent asks for, rendered as compact chips after the
   *  sentence (the text itself stays short). */
  consentClaims?: string[]
  /** Capability-picker spotlight entries when the banner is a capability-fix
   *  card (a user-fixable internal denial): the action opens Capabilities
   *  with these entries highlighted. */
  fixEntries?: string[]
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
  surface: 'links'
  timestamp: number
  url: string
  title?: string | null
  body?: string | null
  favicon?: string | null
}

export interface FileArtifact {
  kind: 'file'
  surface: 'files'
  timestamp: number
  filename: string
  objectRef: string
  logicalPath?: string | null
  mime?: string | null
  description?: string | null
}

export interface TimelineArtifact {
  kind: 'timeline'
  surface: 'timeline'
  timestamp: number
  name: string
  markdown: string
}

export interface CanvasArtifact {
  kind: 'canvas'
  surface: 'artifacts'
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
  surface: 'artifacts'
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
  surface: 'artifacts'
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
  surface: 'artifacts'
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
  surface: 'artifacts'
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
  surface: 'artifacts'
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

/** A turn triggered by an agent, not the user (a subagent's converged
 *  completion reactively opens a parent continuation turn). Read off the
 *  triggering input's `authored_by: "agent"` contract: `agentTitle` is the
 *  helper persona name the delegating agent chose, `handoff` the helper's own
 *  message back to the parent (its `react.contribute` report), absent when the
 *  helper made no contribution. The turn renders as this persona in place of
 *  the "You" bubble. */
export interface TurnAgentPersona {
  agentTitle: string
  handoff?: string | null
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
  namespace?: string
  object_kind?: string
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
  /* Present when the turn was triggered by an agent rather than the user (a
   * subagent completion opening a parent continuation turn). Renders the
   * helper persona in place of the "You" bubble. */
  authoredBy?: TurnAgentPersona | null
  /* Transient, live-only notice shown when this turn took over a conversation
   * whose previous response was interrupted before it finished (a crash, reload,
   * or superseded turn). Set from the live `external_event.handler.reclaim` step;
   * it is not part of the persisted turn, so it is absent after a reload. */
  interruptedNotice?: string | null
  /* Turn accounting, populated at the end of the turn: cost from the
   * `accounting.usage` event (data.cost_total_usd) and wall time from the
   * `chat.turn.summary` event (data.elapsed_ms). Null until those arrive. */
  costUsd?: number | null
  elapsedMs?: number | null
}

/** Thread lifecycle over the two sources that can populate it:
 *  `live` = stamped emissions are streaming in on the parent channel;
 *  `stub` = only the fork descriptor is known (reload) — expanding fetches;
 *  `loading`/`ready`/`error` = the child-conversation fetch states. */
export type SubagentThreadHydration = 'live' | 'stub' | 'loading' | 'ready' | 'error'

/** Live status from the `subagent.charter`/`.converged`/`.failed` lane
 *  events; `running` until a terminal one arrives; `unknown` for reloaded
 *  stubs whose completion the stored parent hasn't surfaced. */
export type SubagentThreadStatus = 'running' | 'converged' | 'failed' | 'unknown'

/** One `react.contribute` milestone the child sent back mid-run. */
export interface SubagentContribution {
  id: string
  timestamp: number
  text: string
  refs?: string[]
}

/** A subagent thread: the child conversation rendered as a collapsible
 *  sub-conversation anchored under the parent turn it forked from. Keyed by
 *  `childConversationId` in `ChatState.threads`; the child's turns reuse the
 *  SAME `ChatTurn` model (and reducers) as the main lane. */
export interface SubagentThread {
  childConversationId: string
  parentTurnId: string
  parentConversationId?: string | null
  /** The sub-agent persona name the delegating agent chose (react.delegate
   *  `agent_title`), carried on the stamp and the reload fork descriptor.
   *  Names the thread header and the continuation-turn persona alike; empty
   *  falls back to "Sub-agent" at render. */
  agentTitle: string
  charterGoal: string
  forkedAt: number
  status: SubagentThreadStatus
  /** Terminal note: the converged report line or the failure reason. */
  statusDetail?: string | null
  contributions: SubagentContribution[]
  turns: ChatTurn[]
  hydration: SubagentThreadHydration
  hydrationError?: string | null
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
  /** Consent signatures dismissed this conversation: the identical consent
   *  state stays quiet; a changed claims set shows again. Reset on
   *  conversation switch. */
  dismissedConsentSignatures: string[]
  /** Composer-menu spotlight request: open the tools menu with these tools
   *  highlighted (set by the consent banner's "turn off the tools" option). */
  toolSpotlight: { tools: string[]; nonce: number } | null
  /** Signed-in user's saved reaction per assistant turn id. */
  feedback: Record<string, TurnReaction>
  inputLocked: boolean
  inputLockMessage: string | null
  conversations: ConversationSummary[]
  conversationsLoading: boolean
  conversationsError: string | null
  conversationLoadingId: string | null
  conversationDeletingId: string | null
  /** Current conversation's agent capability inventory + selection (the
   *  composer "+" menu). Lazy: loaded on first open; `disabled` is the local
   *  draft/effective deny-list. */
  capabilities: AgentCapabilitiesState
  /** Subagent threads of the OPEN conversation, keyed by child conversation
   *  id. Fed live by stamped emissions and on reload by the parent turns'
   *  `forks` descriptors. Reset on conversation switch. */
  threads: Record<string, SubagentThread>
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
  dismissedConsentSignatures: [],
  toolSpotlight: null,
  feedback: {},
  inputLocked: false,
  inputLockMessage: null,
  conversations: [],
  conversationsLoading: false,
  conversationsError: null,
  conversationLoadingId: null,
  conversationDeletingId: null,
  capabilities: initialCapabilitiesState,
  threads: {},
}
