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
