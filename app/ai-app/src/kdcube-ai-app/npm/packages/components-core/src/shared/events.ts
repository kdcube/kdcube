/**
 * Host event bus — the decoupling seam that lets ANY host (React, Angular,
 * plain JS, an iframe scene) react to server- and component-originated control
 * events without the engine knowing what the host is.
 *
 * The engine NEVER reaches for `window.parent.postMessage`, a router, or a
 * login modal directly. It emits typed events here; the host subscribes via
 * `engine.on(...)` and decides what to do. The iframe/scene widget maps these
 * to `postMessage`; a website maps them to its own handlers.
 *
 * This replaces direct host-coupled behavior in the widget. The iframe adapter
 * maps host events to the current scene contracts, including
 * `kdcube.surface.command` for cross-surface object actions.
 */

/** A reference to an openable object surfaced by the engine (context chip,
 * resolver target, pinned conversation, …). Shape is intentionally open — the
 * host decides how to resolve it. */
export interface ObjectRef {
  /** Stable identifier of the referenced object. */
  id?: string
  /** Logical surface the host should route to (e.g. a canvas/chat surface id). */
  surface?: string
  /** Free-form resolver payload carried verbatim from the server. */
  [key: string]: unknown
}

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed' | 'error'

export type NoticeTone = 'info' | 'success' | 'warning' | 'error'

/**
 * The events a component engine can bubble to its host. Keep this map the single
 * source of truth — every new host-actionable signal gets a key here so all
 * adapters stay type-checked.
 */
export interface HostEventMap {
  /** Server rejected the request as unauthenticated/forbidden. Host shows login. */
  'unauthorized': { status?: number; reason?: string }
  /** A referenced object should be opened. Host orchestrates the open. */
  'object-open': { ref: ObjectRef }
  /** The component requests a host-level view change (compact/expanded/…). */
  'view-change': { view: string }
  /** The user asked to pin the active conversation to a board/canvas. */
  'pin-conversation': { conversationId: string; title?: string; ref?: ObjectRef; context?: ObjectRef; contexts?: ObjectRef[] }
  /** A backend canvas patch arrived on the chat stream; forward it to a board. */
  'canvas-patch': { event: Record<string, unknown> }
  /** Attached context chip(s) were removed; the host may sync the source surface. */
  'context-removed': { ids: string[] }
  /** A user-facing notice from the server (rate limit, funding, economics, tips). */
  'service-notice': { text: string; tone: NoticeTone; kind?: string }
  /** The user asked to manage connected accounts (Connection Hub). The host
   *  opens its connections surface (e.g. the connection-hub bundle's
   *  `connections_settings` widget). Component UI shows the entry point only
   *  when a handler is registered (`emitter.has('open-connections')`). */
  'open-connections': { source?: string }
  /** Transport/connection lifecycle changed. Informational. */
  'connection': { status: ConnectionStatus; detail?: string }
  /** The engine finished booting and is ready to use. */
  'ready': Record<string, never>
  /** A non-fatal or fatal engine error the host may want to surface/log. */
  'error': { error: unknown; fatal?: boolean; context?: string }
}

export type HostEventName = keyof HostEventMap

export type HostEventHandler<E extends HostEventName> = (payload: HostEventMap[E]) => void

export interface HostEventEmitter {
  on<E extends HostEventName>(event: E, handler: HostEventHandler<E>): () => void
  off<E extends HostEventName>(event: E, handler: HostEventHandler<E>): void
  emit<E extends HostEventName>(event: E, payload: HostEventMap[E]): void
  /** True when at least one handler is registered — UI uses this to hide
   *  entry points the host chose not to wire (e.g. the connections row). */
  has(event: HostEventName): boolean
}

/** Minimal typed emitter with no external dependency. */
export function createHostEventEmitter(): HostEventEmitter {
  const handlers = new Map<HostEventName, Set<(payload: unknown) => void>>()

  return {
    on(event, handler) {
      let set = handlers.get(event)
      if (!set) {
        set = new Set()
        handlers.set(event, set)
      }
      set.add(handler as (payload: unknown) => void)
      return () => this.off(event, handler)
    },
    off(event, handler) {
      handlers.get(event)?.delete(handler as (payload: unknown) => void)
    },
    emit(event, payload) {
      const set = handlers.get(event)
      if (!set) return
      for (const handler of [...set]) {
        try {
          handler(payload)
        } catch {
          /* a misbehaving host handler must not break the engine */
        }
      }
    },
    has(event) {
      return (handlers.get(event)?.size ?? 0) > 0
    },
  }
}
