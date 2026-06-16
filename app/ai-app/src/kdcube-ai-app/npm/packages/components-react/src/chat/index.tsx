/**
 * @kdcube/components-react/chat — React bindings over the headless chat engine.
 *
 * These are intentionally THIN: all behaviour lives in
 * `@kdcube/components-core/chat`. This package only adapts the controller to
 * React idioms:
 *   - `<ChatStoreProvider>` owns one engine instance and also provides its RTK
 *     store via react-redux, so view components can `useSelector`/`useDispatch`;
 *   - `useChatEngine()` reaches the controller (methods + event bus);
 *   - `useChatState(sel)` / `useChatStatus(sel)` subscribe to the Redux chat
 *     state and the engine-level status (ready/auth/hostView/dryRun).
 *
 * Usage in a host app (no iframe):
 *
 *   <ChatStoreProvider config={{ connection: { baseUrl, tenant, project, bundleId } }}>
 *     <MyChatUI />
 *   </ChatStoreProvider>
 *
 *   function MyChatUI() {
 *     const engine = useChatEngine()
 *     const turns = useChatState(s => s.turns)
 *     const { authed } = useChatStatus()
 *     useEffect(() => engine.on('unauthorized', () => showLogin()), [engine])
 *     return <button onClick={() => engine.send('hi')}>send</button>
 *   }
 */
import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { Provider } from 'react-redux'
import type { EngineConfig } from '@kdcube/components-core'
import {
  createChatEngine,
  type ChatEngine,
  type ChatEngineStatus,
  type ChatState,
} from '@kdcube/components-core/chat'

const ChatEngineContext = createContext<ChatEngine | null>(null)

export interface ChatStoreProviderProps {
  config: EngineConfig
  children: ReactNode
}

export function ChatStoreProvider({ config, children }: ChatStoreProviderProps) {
  // One engine per provider instance, created once. (Unlike the in-tree widget,
  // which shared a module-singleton store, each provider here is isolated — so a
  // page can host more than one chat.) `config` is read once at creation.
  const [engine] = useState<ChatEngine>(() => createChatEngine(config))
  useEffect(() => () => engine.dispose(), [engine])
  return (
    <ChatEngineContext.Provider value={engine}>
      <Provider store={engine.store}>{children}</Provider>
    </ChatEngineContext.Provider>
  )
}

export function useChatEngine(): ChatEngine {
  const engine = useContext(ChatEngineContext)
  if (!engine) {
    throw new Error('useChatEngine must be used within a <ChatStoreProvider>.')
  }
  return engine
}

/** Subscribe to the Redux chat state with an optional selector. */
export function useChatState<T = ChatState>(selector?: (state: ChatState) => T): T {
  const engine = useChatEngine()
  const select = selector ?? ((s: ChatState) => s as unknown as T)
  const selectorRef = useRef(select)
  selectorRef.current = select
  return useSyncExternalStore(
    (onChange) => engine.subscribe(onChange),
    () => selectorRef.current(engine.getState()),
    () => selectorRef.current(engine.getState()),
  )
}

/** Subscribe to engine-level status (ready/authed/bootError/hostView/dryRun). */
export function useChatStatus<T = ChatEngineStatus>(selector?: (status: ChatEngineStatus) => T): T {
  const engine = useChatEngine()
  const select = selector ?? ((s: ChatEngineStatus) => s as unknown as T)
  const selectorRef = useRef(select)
  selectorRef.current = select
  return useSyncExternalStore(
    (onChange) => engine.subscribeStatus(onChange),
    () => selectorRef.current(engine.getStatus()),
    () => selectorRef.current(engine.getStatus()),
  )
}

export type { ChatEngine, ChatEngineStatus, ChatState }
