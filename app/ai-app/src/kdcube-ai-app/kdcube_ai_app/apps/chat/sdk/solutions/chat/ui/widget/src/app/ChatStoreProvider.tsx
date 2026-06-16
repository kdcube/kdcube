/**
 * ChatStoreProvider — the public entry point for embedding the reusable chat
 * engine with your own UI:
 *
 *   <ChatStoreProvider config={...}>
 *     <MyOwnChatUI />        // calls useChatEngine() inside
 *   </ChatStoreProvider>
 *
 * It applies any caller-supplied connection config to `settings` BEFORE the engine
 * boots, then renders the active **engine root**. There are two interchangeable
 * roots behind the `@chat/engine-root` alias:
 *
 *   - `localEngineRoot.tsx`  — the in-tree Redux store + `useChatEngine` (DEFAULT)
 *   - `packageEngine.tsx`    — the framework-agnostic `@kdcube/components-react/chat`
 *                              engine + iframe host-bridge (the npm:// packages)
 *
 * `vite.config.ts` resolves the alias to one or the other at build time, keyed on
 * `VITE_CHAT_ENGINE` (`package` → package engine, else in-tree). Switching engines
 * is therefore a single env var in the widget's build command — no code changes —
 * and the default build never pulls `@kdcube/*` into its module graph.
 *
 * When `config` is omitted the engine keeps today's behavior: it resolves
 * baseUrl/tenant/project/bundle/auth from query params, the served route, and the
 * parent-frame CONFIG handshake (settings.setupParentListener()).
 */
import type { ReactNode } from 'react'
import { useState } from 'react'
// Resolved at build time by vite.config (engine-root swap); both targets export an
// `EngineRoot({ children })` with identical behavior from the host's point of view.
import { EngineRoot } from '@chat/engine-root'
import type { AppSettings } from '../settings.ts'
import { settings } from '../settings.ts'

export type ChatEngineConfig = Partial<AppSettings>

export function ChatStoreProvider({
  config,
  children,
}: {
  config?: ChatEngineConfig
  children: ReactNode
}) {
  /* Apply caller config exactly once, synchronously, before children (and the
   * engine boot) first render. A useState initializer runs during render, ahead of
   * child effects. Both engine roots read the same `settings` singleton. */
  useState(() => {
    if (config && Object.keys(config).length > 0) settings.update(config)
    return true
  })

  return <EngineRoot>{children}</EngineRoot>
}
