/** Default chat UI surface (built on the engine binding). */
export { Chat, type ChatProps } from './Chat.tsx'
export { ChatShell, type ChatShellProps } from './ChatShell.tsx'
export { ChatViewModelProvider, useChatViewModel, type ChatViewModelProviderProps } from './context.tsx'
export type { ChatViewModel } from './viewModel.ts'
export {
  CapabilityPickerPage,
  ComposerMenu,
  useCapabilityPickerBody,
  type ComposerMenuSectionContext,
  type ComposerMenuSectionDescriptor,
} from './features/composer/ComposerMenu.tsx'
export {
  useStandaloneCapabilitiesVm,
  type StandaloneCapabilitiesResponse,
  type StandaloneCapabilityRuntime,
  type StandaloneSelectionWriteOptions,
} from './features/composer/CapabilityPickerStandalone.tsx'
export { SubagentThreads } from './features/chat/SubagentThreads.tsx'
export { ConversationSearchPage } from './features/conversations/ConversationSearchPage.tsx'
export { ConversationSearchControls } from './features/conversations/ConversationSearchControls.tsx'
export {
  useConversationSearch,
  type ConversationSearchSeed,
  type ConversationSearchVm,
  type ConversationSearchScope,
} from './features/conversations/useConversationSearch.ts'
