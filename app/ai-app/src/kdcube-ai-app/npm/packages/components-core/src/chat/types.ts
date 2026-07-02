import type { EngineConfig, HostEventEmitter } from '../shared/index.ts'
import type { AttachedContext, ChatState } from './state.ts'
import type { ContextChip } from './contextChips.ts'
import type { ReactContextPreviewResponse, TurnReaction } from './protocol.ts'
import type { ConversationSummary } from './protocol.ts'
import type { ChatStore } from './store.ts'

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

  /** Tear down transport + timers. */
  dispose(): void
}

export type CreateChatEngine = (config: EngineConfig) => ChatEngine
