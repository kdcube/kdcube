/**
 * @kdcube/components-core/chat — the headless chat engine.
 *
 * `createChatEngine(config)` returns a framework-agnostic controller: it owns the
 * RTK store + transport + orchestration lifted from the widget's
 * `useChatEngine.tsx`, with the `settings` singleton replaced by the injected
 * `EngineConfig` and the `host.ts` postMessage calls replaced by the host event
 * bus. Re-exports the store/slice/reducers/transport/protocol so advanced hosts
 * and the React adapter can reach the internals.
 */
export { createChatEngine } from './engine.ts'

export type {
  ChatEngine,
  ChatEngineStatus,
  HostView,
  AttachContextInput,
  OpenContextInput,
  FeedbackReaction,
  CreateChatEngine,
} from './types.ts'
export { activateContextPin, contextPinActionNotice, ContextPinActionError } from './contextPinActions.ts'
export type { ActionableContext } from './contextPinActions.ts'
export { buildExternalEventBatch, contextRef, isCanvasContext } from './eventBatch.ts'

export type * from './state.ts'
export type * from './protocol.ts'
export { initialState } from './state.ts'
export * from './refs.ts'
export * from './contextChips.ts'
export * from './util.ts'
export type { EngineRuntime, ResolvedTokens } from './runtime.ts'
export { buildRuntime, createLocalId, getClientTimezone } from './runtime.ts'
export * from './transport/index.ts'
export * from './reducers.ts'
export { chatSlice, chatActions, chatReducer } from './slice.ts'
export { createChatStore } from './store.ts'
export type { ChatStore, RootState, AppDispatch } from './store.ts'
