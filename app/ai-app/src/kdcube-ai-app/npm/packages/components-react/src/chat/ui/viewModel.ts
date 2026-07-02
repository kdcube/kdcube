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
  ChatEngine,
  ChatState,
  HostView,
  ReactContextPreviewResponse,
} from '@kdcube/components-core/chat'

export interface ChatViewModel {
  state: ChatState
  ready: boolean
  authed: boolean
  bootError: string | null
  hostView: HostView
  bundleId: string
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
}
