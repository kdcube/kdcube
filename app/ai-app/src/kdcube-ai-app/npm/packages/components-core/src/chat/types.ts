import type { ConnectionsConsentOpen, EngineConfig, HostEventEmitter, HostEventName } from '../shared/index.ts'
import type { AttachedContext, ChatState } from './state.ts'
import type { ContextChip } from './contextChips.ts'
import type { ReactContextPreviewResponse, TurnReaction } from './protocol.ts'
import type { ConversationSummary } from './protocol.ts'
import type { ChatStore } from './store.ts'
import type { AgentSelectionPatch } from './capabilities.ts'

/** Input accepted by attachContext — the structured context object a host drops
 *  or pins into chat. */
export type AttachContextInput = AttachedContext

/** Input accepted by openContextChip (a composer chip or a board pin). */
export type OpenContextInput = AttachedContext | ContextChip

/** A turn reaction, or `null` to clear an existing one. */
export type FeedbackReaction = TurnReaction | null

/** The host view form the engine tracks (compact embedded vs. expanded). */
export type HostView = 'compact' | 'expanded'

/** Engine-level status that lives outside the Redux `ChatState` (boot/auth/view
 *  and the dry-run preview). Read via getStatus(); subscribe via subscribeStatus(). */
export interface ChatEngineStatus {
  ready: boolean
  authed: boolean
  /** Roles from the server profile (e.g. `kdcube:role:super-admin`). Re-probed on
   *  every auth resolution, so it stays current when the host broadcasts an auth
   *  change. Empty for anonymous/unauthenticated callers. */
  roles: string[]
  bootError: string | null
  hostView: HostView
  dryRun: {
    enabled: boolean
    loading: boolean
    preview: ReactContextPreviewResponse | null
    error: string | null
  }
}

/**
 * ChatEngine — the framework-agnostic controller. A React/Angular/vanilla host
 * drives chat entirely through this surface; rendering is the host's concern.
 *
 * Differs from the widget's `useChatEngine()` in two ways:
 *   - state is read via getState()/subscribe() + getStatus()/subscribeStatus()
 *     (and the internal RTK store), not React hooks; and
 *   - host-actionable signals are emitted on the event bus (`on(...)`), not
 *     pushed via iframe postMessage. The host listens and reacts.
 */
export interface ChatEngine extends Pick<HostEventEmitter, 'on'> {
  /** Internal RTK store. The React adapter binds react-redux to it; app code
   *  should prefer getState()/subscribe(). */
  readonly store: ChatStore
  readonly bundleId: string
  /** The bundle agent this engine drives (config `agentId`, default 'main'). */
  readonly agentId: string

  getState(): ChatState
  subscribe(listener: () => void): () => void
  getStatus(): ChatEngineStatus
  subscribeStatus(listener: () => void): () => void

  /** Re-resolve auth + (re)connect. The host calls this after an external login
   *  change; it is also run once on creation. */
  refreshAuth(): void

  send(textOverride?: string, requestedReactiveEventType?: string): void
  steer(): void

  loadConversation(conversationId: string): void
  newChat(): void
  deleteConversation(conversation: ConversationSummary): void
  refreshConversations(): void

  attachContext(contexts: AttachContextInput | AttachContextInput[]): void
  /** Remove attached context chip(s). `silent` suppresses the `context-removed`
   *  event — use it for host-driven removals so a host-bridge doesn't echo. */
  removeContext(ids: string | string[], opts?: { silent?: boolean }): void
  openContextChip(context: OpenContextInput): void

  downloadFile(ref: string, filename?: string, mime?: string): void
  loadFileBlob(ref: string, filename?: string, mime?: string): Promise<Blob>
  submitFeedback(turnId: string, reaction: FeedbackReaction, text?: string): void

  handleReconnect(): void
  pinConversationToCanvas(): void
  promptLogin(): void
  /** Set the host view form. `silent` suppresses the `view-change` event — use it
   *  when applying a host-pushed view so a host-bridge doesn't echo. */
  setHostView(next: HostView, opts?: { silent?: boolean }): void
  setBootError(value: string | null): void
  setDryRunEnabled(value: boolean): void
  clearDryRunPreview(): void

  /** Load the agent's capability inventory + the user's saved selection into
   *  `state.capabilities`. Lazy: call on first menu open; no-op when already
   *  loaded unless `force`. */
  loadAgentCapabilities(opts?: { force?: boolean }): void
  /** Apply a selection toggle patch optimistically and queue the debounced
   *  `agent_selection_update` merge-write (only the changed toggles are sent).
   *  Takes effect from the next message. */
  updateAgentSelection(patch: AgentSelectionPatch): void
  /** One explicit cold-cache decision (the confirm picker): immediate write
   *  with `apply` = now | next_conversation | when_cold and an optional
   *  standing `cachePolicy` ("remember my choice"). */
  submitAgentSelectionDecision(
    patch: AgentSelectionPatch,
    options?: { apply?: 'now' | 'next_conversation' | 'when_cold'; cachePolicy?: Record<string, string> },
  ): void

  /** Ask the host to open its connections surface (Connection Hub). Emits the
   *  `open-connections` host event; the host adapter routes it (e.g. a scene
   *  surface command targeting the connection-hub settings widget). `consent`
   *  carries the structured deep-link when the open comes from a
   *  connected-account consent card. */
  openConnections(source?: string, consent?: ConnectionsConsentOpen): void
  /** True when the host registered a handler for `event`. UI hides entry
   *  points (like the connections row) the host chose not to wire. */
  hasHostHandler(event: HostEventName): boolean

  /** Tear down transport + timers. */
  dispose(): void
}

export type CreateChatEngine = (config: EngineConfig) => ChatEngine
