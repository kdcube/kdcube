/**
 * Builds a `ChatViewModel` from the engine binding hooks and provides it via
 * context, so the default `<Chat/>` UI and its sub-components render against one
 * stable seam. Host-actionable methods map straight onto the controller; the host
 * (if any) reacts to the engine event bus — the UI itself has no iframe/postMessage
 * or settings-singleton coupling.
 */
import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { isSuperAdmin } from '@kdcube/components-core/chat'
import type { ConnectionsConsentOpen } from '@kdcube/components-core/chat'
import { useChatEngine, useChatState, useChatStatus } from '../binding.tsx'
import type { ChatViewModel } from './viewModel.ts'

const ChatViewModelContext = createContext<ChatViewModel | null>(null)

export function useChatViewModel(): ChatViewModel {
  const vm = useContext(ChatViewModelContext)
  if (!vm) {
    throw new Error('useChatViewModel must be used within <Chat/> (a ChatViewModelProvider).')
  }
  return vm
}

export interface ChatViewModelProviderProps {
  children: ReactNode
  /** Marks a same-origin dev-preview frame; defaults to false. */
  kdcubePreview?: boolean
}

export function ChatViewModelProvider({ children, kdcubePreview = false }: ChatViewModelProviderProps) {
  const engine = useChatEngine()
  const state = useChatState((s) => s)
  const status = useChatStatus()

  const vm = useMemo<ChatViewModel>(() => ({
    state,
    ready: status.ready,
    authed: status.authed,
    roles: status.roles,
    isSuperAdmin: isSuperAdmin(status.roles),
    bootError: status.bootError,
    hostView: status.hostView,
    bundleId: engine.bundleId,
    agentId: engine.agentId,
    kdcubePreview,

    setBootError: engine.setBootError,
    setHostView: (next) => engine.setHostView(next),
    setHostViewLocal: () =>
      engine.setHostView(status.hostView === 'compact' ? 'expanded' : 'compact', { silent: true }),

    send: engine.send,
    steer: engine.steer,
    loadConversation: engine.loadConversation,
    newChat: engine.newChat,
    deleteConversation: engine.deleteConversation,
    refreshConversationList: engine.refreshConversations,

    attachContext: engine.attachContext,
    removeContext: (ids) => engine.removeContext(ids),
    openContextChip: engine.openContextChip,

    downloadFile: engine.downloadFile,
    loadFileBlob: engine.loadFileBlob,
    submitFeedback: engine.submitFeedback,
    handleReconnect: engine.handleReconnect,
    pinConversationToCanvas: engine.pinConversationToCanvas,
    promptLogin: engine.promptLogin,

    dryRun: {
      enabled: status.dryRun.enabled,
      loading: status.dryRun.loading,
      preview: status.dryRun.preview,
      error: status.dryRun.error,
      setEnabled: engine.setDryRunEnabled,
      clearPreview: engine.clearDryRunPreview,
    },

    capabilities: {
      ...state.capabilities,
      load: engine.loadAgentCapabilities,
      toggle: engine.updateAgentSelection,
      decide: engine.submitAgentSelectionDecision,
    },

    connections: {
      available: () => engine.hasHostHandler('open-connections'),
      open: (source?: string, consent?: ConnectionsConsentOpen) => engine.openConnections(source, consent),
    },
  }), [engine, state, status, kdcubePreview])

  return <ChatViewModelContext.Provider value={vm}>{children}</ChatViewModelContext.Provider>
}
