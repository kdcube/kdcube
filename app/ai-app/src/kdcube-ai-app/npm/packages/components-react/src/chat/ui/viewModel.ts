/**
 * ChatViewModel — the UI-facing seam the default `<Chat/>` (and its sub-components)
 * render against. It is derived from the headless engine controller
 * (`@kdcube/components-core/chat`'s `ChatEngine`) by `context.tsx`, so the UI never
 * touches the store, transport, or any host/iframe API directly.
 *
 * Distinct from core's `ChatEngine` (the controller): this is read-model + bound
 * actions shaped for rendering. Method fields reuse the controller's signatures via
 * indexed access (`ChatEngine['send']`) so they cannot drift; a few are reshaped
 * (e.g. `setHostView` drops the host-bridge `opts`).
 */
import type {
  AgentCapabilitiesState,
  ChatEngine,
  ChatState,
  ConnectionsConsentOpen,
  HostView,
  ReactContextPreviewResponse,
} from '@kdcube/components-core/chat'

export interface ChatViewModel {
  state: ChatState
  ready: boolean
  authed: boolean
  /** Roles from the server profile; re-probed on each auth change (event-driven). */
  roles: string[]
  /** True when the current profile's roles include platform super-admin. Reactive:
   *  updates when auth changes re-probe `/profile`. */
  isSuperAdmin: boolean
  bootError: string | null
  hostView: HostView
  bundleId: string
  /** The bundle agent this chat drives (engine config `agentId`, default 'main'). */
  agentId: string
  /** True when rendered inside a same-origin dev preview frame; hosts may set it. */
  kdcubePreview: boolean

  setBootError: ChatEngine['setBootError']
  /** Set the view form (the host bridge, if any, reacts to the `view-change` event). */
  setHostView: (next: HostView) => void
  /** Flip the view form locally only (no host notification). */
  setHostViewLocal: () => void

  send: ChatEngine['send']
  steer: ChatEngine['steer']
  loadConversation: ChatEngine['loadConversation']
  newChat: ChatEngine['newChat']
  deleteConversation: ChatEngine['deleteConversation']
  refreshConversationList: ChatEngine['refreshConversations']

  attachContext: ChatEngine['attachContext']
  removeContext: (ids: string | string[]) => void
  openContextChip: ChatEngine['openContextChip']

  downloadFile: ChatEngine['downloadFile']
  loadFileBlob: ChatEngine['loadFileBlob']
  submitFeedback: ChatEngine['submitFeedback']
  handleReconnect: ChatEngine['handleReconnect']
  pinConversationToCanvas: ChatEngine['pinConversationToCanvas']
  promptLogin: ChatEngine['promptLogin']

  dryRun: {
    enabled: boolean
    loading: boolean
    preview: ReactContextPreviewResponse | null
    error: string | null
    setEnabled: ChatEngine['setDryRunEnabled']
    clearPreview: ChatEngine['clearDryRunPreview']
  }

  /** Per-user agent capabilities (the composer "+" menu): the state branch plus
   *  the engine's lazy load + optimistic/debounced toggle. Registered users
   *  only — the menu hides when `authed` is false. */
  capabilities: AgentCapabilitiesState & {
    load: ChatEngine['loadAgentCapabilities']
    toggle: ChatEngine['updateAgentSelection']
    /** One explicit cold-cache decision (the confirm picker). */
    decide: ChatEngine['submitAgentSelectionDecision']
  }

  /** Connection-Hub entry point. `available()` reflects whether the host
   *  registered an `open-connections` handler (checked live, e.g. on menu
   *  open); `open()` emits the host event. UI hides when unavailable. */
  connections: {
    available: () => boolean
    open: (source?: string, consent?: ConnectionsConsentOpen) => void
  }
}
