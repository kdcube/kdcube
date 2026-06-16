/**
 * In-tree engine root (the default). Renders the local Redux store + the in-tree
 * `useChatEngine` host. This is one of the two interchangeable engine roots behind
 * the `@chat/engine-root` alias; the other is `packageEngine.tsx` (the npm:// package
 * engine). `vite.config.ts` picks which one this alias resolves to at build time,
 * keyed on `VITE_CHAT_ENGINE` — so switching engines is a single env var, and this
 * default build never pulls the `@kdcube/*` packages into its graph.
 */
import type { ReactNode } from 'react'
import { Provider } from 'react-redux'
import { store } from './store.ts'
import { ChatEngineHost } from './useChatEngine.tsx'

export function EngineRoot({ children }: { children: ReactNode }) {
  return (
    <Provider store={store}>
      <ChatEngineHost>{children}</ChatEngineHost>
    </Provider>
  )
}
