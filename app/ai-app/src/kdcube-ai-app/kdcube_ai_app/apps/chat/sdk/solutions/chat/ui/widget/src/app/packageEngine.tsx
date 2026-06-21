/**
 * Package-backed engine path (opt-in). Drives chat through the framework-agnostic
 * `@kdcube/components-react/chat` engine instead of the in-tree `useChatEngine`,
 * and adds the **iframe host-bridge**: the engine emits host events and exposes
 * methods; this adapter maps those to/from the scene's `postMessage` protocol via
 * the widget's existing `host.ts`, and re-shapes the engine into the same
 * view-model `App.tsx` already consumes (provided through `ChatEngineContext`).
 *
 * Enabled only when `ChatStoreProvider` selects the package path; the default
 * remains the local engine. See `ChatStoreProvider.tsx`.
 */
import { useEffect, useState, type ReactNode } from 'react'
import {
  ChatStoreProvider as PkgChatStoreProvider,
  useChatEngine as usePkgEngine,
} from '@kdcube/components-react/chat'
import type { EngineConfig } from '@kdcube/components-core'
import type { AttachContextInput } from '@kdcube/components-core/chat'
import {
  recognizeContextMessage,
  recognizeContextRemoval,
  requestAuthRequired,
  requestHostObjectOpen,
  requestHostView,
} from '../host.ts'
import {
  BUILT_BUNDLE_ID,
  CHAT_CANVAS_PATCH_MESSAGE,
  CHAT_CANVAS_PATCH_SOURCE,
  CHAT_CONTEXT_REFRESH_SOURCE,
  CHAT_CONTEXT_REMOVE_MESSAGE,
  settings,
} from '../settings.ts'

const SURFACE_COMMAND_MESSAGE_TYPE = 'kdcube.surface.command'

function buildEngineConfig(): EngineConfig {
  return {
    connection: {
      baseUrl: settings.getBaseUrl(),
      tenant: settings.getTenant(),
      project: settings.getProject(),
      bundleId: settings.getBundleId() || BUILT_BUNDLE_ID,
    },
    // Boot compact when embedded in a host iframe (the compact tile), expanded
    // standalone — matching the in-tree widget (useChatEngine's initial hostView).
    // Without this the package engine defaulted to 'expanded', so the embedded chat
    // rendered the wide/sidebar layout in the compact iframe.
    initialHostView:
      typeof window !== 'undefined' && window.parent !== window ? 'compact' : 'expanded',
    // Token mode with live-reading callbacks: returns null for anonymous (so the
    // request falls back to the cookie via credentials:'include') and the tokens
    // once the host re-posts config with them — covering the anon→authed handoff.
    auth: {
      mode: 'token',
      getAccessToken: () => settings.getAccessToken(),
      getIdToken: () => settings.getIdToken(),
      idTokenHeader: settings.getIdTokenHeader(),
    },
  }
}

function conversationIdFromSurfaceCommand(data: Record<string, unknown>): string {
  const target = String(data.target_surface || '').trim().toLowerCase()
  const action = String(data.action || '').trim().toLowerCase()
  if (
    data.type !== SURFACE_COMMAND_MESSAGE_TYPE ||
    (target && target !== 'sdk.chat.conversation' && target !== 'sdk.chat.viewer') ||
    action !== 'open'
  ) return ''
  const context = data.context && typeof data.context === 'object' ? data.context as Record<string, unknown> : {}
  const contextData = context.data && typeof context.data === 'object' ? context.data as Record<string, unknown> : {}
  const direct = String(data.conversation_id || contextData.conversation_id || context.conversation_id || '').trim()
  if (direct) return direct
  const ref = String(data.object_ref || context.object_ref || context.ref || context.logical_path || '').trim()
  if (!ref.startsWith('conv:')) return ''
  const parts = ref.slice('conv:'.length).split('/')
  return (parts[parts.length - 1] || '').trim()
}

/**
 * Engine-root entry behind the `@chat/engine-root` alias (selected when the widget
 * is built with `VITE_CHAT_ENGINE=package`). Same `EngineRoot({ children })` shape
 * as `localEngineRoot.tsx` so `ChatStoreProvider` renders either interchangeably.
 */
export { PackageChatRoot as EngineRoot }

/** Resolve config from the parent handshake, then mount the package engine. */
export function PackageChatRoot({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<EngineConfig | null>(null)
  useEffect(() => {
    let mounted = true
    const finish = () => { if (mounted) setConfig(buildEngineConfig()) }
    void settings.setupParentListener().then(finish).catch(finish)
    return () => { mounted = false }
  }, [])
  if (!config) return null
  return (
    <PkgChatStoreProvider config={config}>
      <PackageEngineHost>{children}</PackageEngineHost>
    </PkgChatStoreProvider>
  )
}

function PackageEngineHost({ children }: { children: ReactNode }) {
  const engine = usePkgEngine()

  // --- outbound: engine events -> host postMessage (via host.ts) ---
  useEffect(() => {
    const offs = [
      engine.on('unauthorized', () => requestAuthRequired()),
      engine.on('view-change', ({ view }) => requestHostView(view as 'compact' | 'expanded')),
      engine.on('object-open', ({ ref }) => {
        const r = (ref || {}) as Record<string, unknown>
        requestHostObjectOpen({
          response: (r.response as Record<string, unknown>) || {},
          source: (r.source as Record<string, unknown>) || {},
        })
      }),
      engine.on('pin-conversation', ({ conversationId, title, context, contexts, ref }) => {
        if (typeof window === 'undefined' || window.parent === window) return
        const ctx = (context || ref || null) as Record<string, unknown> | null
        const objectRef = typeof ctx?.ref === 'string'
          ? ctx.ref
          : typeof ctx?.object_ref === 'string'
            ? ctx.object_ref
            : conversationId
              ? `conv:${conversationId}`
              : ''
        window.parent.postMessage({
          type: SURFACE_COMMAND_MESSAGE_TYPE,
          target_surface: 'sdk.canvas.pinboard',
          action: 'pin',
          conversation_id: conversationId,
          title: title || 'Conversation',
          agent: 'main',
          context: ctx,
          contexts: contexts || (ctx ? [ctx] : undefined),
          object_ref: objectRef || undefined,
          source_surface: 'versatile.chat',
        }, '*')
      }),
      engine.on('canvas-patch', ({ event }) => {
        if (typeof window === 'undefined' || !window.parent) return
        window.parent.postMessage({ type: CHAT_CANVAS_PATCH_MESSAGE, source: CHAT_CANVAS_PATCH_SOURCE, event }, '*')
      }),
      engine.on('context-removed', ({ ids }) => {
        try {
          if (window.parent !== window) {
            window.parent.postMessage({ type: CHAT_CONTEXT_REMOVE_MESSAGE, ids }, '*')
          }
        } catch { /* best-effort parent sync */ }
      }),
    ]
    return () => offs.forEach((off) => off())
  }, [engine])

  // --- inbound: host postMessage -> engine methods (silent to avoid echo) ---
  useEffect(() => {
    function onHostMessage(event: MessageEvent) {
      const data = event.data
      if (!data || typeof data !== 'object') return
      if (data.type === 'kdcube-set-view') {
        if (data.view === 'compact' || data.view === 'expanded') engine.setHostView(data.view, { silent: true })
        return
      }
      const conversationId = conversationIdFromSurfaceCommand(data as Record<string, unknown>)
      if (conversationId) {
        const id = conversationId.trim()
        if (id) engine.loadConversation(id)
        return
      }
      if (data.type === 'kdcube-auth-changed') {
        engine.refreshAuth()
        return
      }
      const removed = recognizeContextRemoval(data)
      if (removed.length > 0) {
        engine.removeContext(removed, { silent: true })
        return
      }
      const recognized = recognizeContextMessage(data)
      if (recognized.length > 0) {
        engine.attachContext(recognized as unknown as AttachContextInput[])
        const source = typeof data.source === 'string' ? data.source : ''
        const silent = data.silent === true || source === CHAT_CONTEXT_REFRESH_SOURCE
        if (!silent) {
          window.requestAnimationFrame(() => {
            const textarea = document.querySelector('.k-composer textarea') as HTMLTextAreaElement | null
            textarea?.focus()
          })
        }
      }
    }
    window.addEventListener('message', onHostMessage)
    return () => window.removeEventListener('message', onHostMessage)
  }, [engine])

  // --- dropped conversation pin (a `conv:` ref) loads that conversation ---
  useEffect(() => {
    const idFromConvRef = (ref: string): string => {
      const value = String(ref || '').trim()
      if (!value.startsWith('conv:')) return ''
      const parts = value.slice('conv:'.length).split('/')
      return (parts[parts.length - 1] || '').trim()
    }
    const conversationIdFromTransfer = (dt: DataTransfer | null): string => {
      if (!dt) return ''
      const fromJson = (raw: string): string => {
        if (!raw) return ''
        try {
          const parsed = JSON.parse(raw)
          const items = Array.isArray(parsed?.contexts) ? parsed.contexts : [parsed]
          for (const item of items) {
            if (!item || typeof item !== 'object') continue
            const kind = String(item.kind || '')
            const ref = String(item.ref || item.logical_path || item.id || '')
            if (kind === 'conversation' || ref.startsWith('conv:')) {
              const fromData = item.data && typeof item.data === 'object'
                ? String((item.data as Record<string, unknown>).conversation_id || '')
                : ''
              return fromData || idFromConvRef(ref)
            }
          }
        } catch { /* not JSON */ }
        return ''
      }
      return fromJson(dt.getData('application/json')) || idFromConvRef(dt.getData('text/uri-list'))
    }
    const looksLikeContextDrag = (dt: DataTransfer | null): boolean =>
      !!dt && Array.from(dt.types || []).some((type) => type === 'application/json' || type === 'text/uri-list')
    const onDragOver = (event: DragEvent) => {
      if (!looksLikeContextDrag(event.dataTransfer)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    }
    const onDrop = (event: DragEvent) => {
      const id = conversationIdFromTransfer(event.dataTransfer)
      if (!id) return
      event.preventDefault()
      engine.loadConversation(id)
    }
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('drop', onDrop)
    }
  }, [engine])

  // host re-posts config (with tokens) after login -> re-auth + connect
  useEffect(() => {
    settings.onConfigReceived(() => engine.refreshAuth())
  }, [engine])

  // The package <Chat/> consumes the package engine via its own context; this host
  // only wires the iframe bridge above and renders children. (The in-tree App view
  // model / ChatEngineContext lived here previously and is gone with App.)
  return <>{children}</>
}
