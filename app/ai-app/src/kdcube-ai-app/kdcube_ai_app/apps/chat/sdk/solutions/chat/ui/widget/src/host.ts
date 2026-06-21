/**
 * Host-window coordination for the expand/collapse affordance, shared
 * idea with the stats `usage` and news `news_preview` widgets.
 *
 * When this chat UI is embedded as an iframe tile on the public landing
 * page, the expand control asks the host to promote the same iframe to a
 * fullscreen overlay (no reload, no re-fetch — the app keeps its state).
 * The widget never opens a fullscreen layer itself, since an iframe
 * cannot draw outside its own rectangle.
 *
 * Contract:
 *   widget -> host: { type: 'kdcube-widget-view', widget: '<chat-widget-id>',
 *                     view: 'compact' | 'expanded' }
 *   host -> widget: { type: 'kdcube-set-view', view: 'compact' | 'expanded' }
 *
 * Iframe sizing protocol:
 *   ?chat_embed_mode=host
 *
 * A host-composed scene sets this query parameter when it owns the iframe
 * rectangle. In that mode the widget fills the iframe and must not enter the
 * same-origin dev preview tile, even when the parent frame is readable.
 *
 * Auth handoff (public embedding): the chat renders for anonymous
 * visitors but sending requires a signed-in user. When an anonymous
 * visitor tries to send, the widget asks the host to show its own login
 * surface:
 *   widget -> host: { type: 'kdcube-auth-required', widget: '<chat-widget-id>' }
 * The host owns the login UI; the visitor may dismiss it and keep
 * reading. On success the host re-posts the runtime config with tokens
 * (CONFIG_RESPONSE, identity configured by the mounted bundle), which the
 * widget picks up to open the stream — no reload.
 */

import {
  CHAT_CONTEXT_ATTACH_MESSAGE,
  CHAT_CONTEXT_FOCUS_MESSAGE,
  CHAT_CONTEXT_REMOVE_MESSAGE,
  CHAT_WIDGET_ID,
} from './settings.ts'
import {
  recognizeContextMessageWithTypes,
  recognizeContextRemovalWithTypes,
  type RecognizedContext,
} from './features/context/contextMessages.ts'

export type TaskTrackerHostView = 'compact' | 'expanded'
export type { RecognizedContext }

export function isHostEmbedMode(): boolean {
  try {
    if (typeof window === 'undefined') return false
    const params = new URLSearchParams(window.location.search || '')
    return params.get('chat_embed_mode') === 'host'
  } catch {
    return false
  }
}

export function requestHostView(view: TaskTrackerHostView): void {
  try {
    if (typeof window === 'undefined' || window.parent === window) return
    window.parent.postMessage({ type: 'kdcube-widget-view', widget: CHAT_WIDGET_ID, view }, '*')
  } catch {
    // Non-fatal: the iframe simply stays at its embedded size.
  }
}

export function notifyHostWidgetFocus(): void {
  try {
    if (typeof window === 'undefined' || window.parent === window) return
    window.parent.postMessage({ type: 'kdcube-widget-focus', widget: CHAT_WIDGET_ID }, '*')
  } catch {
    // Non-fatal: focus promotion is only a host-scene affordance.
  }
}

export function requestAuthRequired(): void {
  try {
    if (typeof window === 'undefined' || window.parent === window) return
    /* Same-origin landing (Caddy/ngrok single origin): call the host's login
     * directly so the sign-in popup opens within the current user gesture.
     * postMessage is async, which loses the gesture and makes the browser
     * block the popup (forcing a full-page redirect). Reading the parent's
     * KDAuth throws on a cross-origin host, where we fall back to postMessage. */
    try {
      const host = (window.parent as unknown as { KDAuth?: { login?: () => void } }).KDAuth
      if (host && typeof host.login === 'function') {
        host.login()
        return
      }
    } catch {
      /* cross-origin parent — fall through to the postMessage contract */
    }
    window.parent.postMessage({ type: 'kdcube-auth-required', widget: CHAT_WIDGET_ID }, '*')
  } catch {
    // Non-fatal: standalone (non-embedded) usage has no host to prompt.
  }
}

export interface HostObjectOpenPayload {
  response: Record<string, unknown>
  source: Record<string, unknown>
}

export function requestHostObjectOpen(payload: HostObjectOpenPayload): boolean {
  try {
    if (typeof window === 'undefined' || window.parent === window) return false
    const response = payload.response || {}
    const uiEvent = (response.ui_event && typeof response.ui_event === 'object' ? response.ui_event : {}) as Record<string, unknown>
    const source = payload.source || {}
    const targetSurface = String(uiEvent.target_surface || response.target_surface || '').trim()
    const objectRef = String(
      uiEvent.object_ref ||
      response.object_ref ||
      response.ref ||
      source.object_ref ||
      source.ref ||
      '',
    ).trim()
    if (!targetSurface) return false
    window.parent.postMessage({
      ...uiEvent,
      type: 'kdcube.surface.command',
      target_surface: targetSurface,
      action: 'open',
      object_ref: objectRef || undefined,
      widget: CHAT_WIDGET_ID,
      response: payload.response,
      source: payload.source,
    }, '*')
    return true
  } catch {
    return false
  }
}

/**
 * True in any KDCube-hosted/dev context — standalone (top-level), or a
 * same-origin KDCube frame such as the control plane (whether the bundle
 * main view is opened top-level or iframed). Only the real public landing
 * embed is excluded: it is a *cross-origin* iframe (reading the parent
 * location throws) and drives the overlay itself.
 *
 * Used to (a) expose the dev compact/full preview toggle and (b) box the
 * compact view into a tile so the "little widget" can be assessed before the
 * landing host is wired up. On the landing embed this returns false, so the
 * compact view simply fills the host-sized iframe.
 */
export function isKdcubePreviewContext(): boolean {
  try {
    if (typeof window === 'undefined') return false
    if (isHostEmbedMode()) return false
    /* Served via a public embed route (the landing page) — never a dev
     * preview, even when the landing is same-origin (Caddy/ngrok single
     * origin). Without this, the same-origin landing would read as a "preview"
     * and box the chat into a 560px dev tile inside its host-sized iframe,
     * leaving the composer below the fold. The compact view must simply fill
     * the host frame here. */
    const path = window.location.pathname || ''
    if (path.includes('/public/static') || path.includes('/public/widgets')) return false
    if (window.parent === window) return true // top-level (standalone / control-plane page)
    void window.parent.location.href // same-origin parent (control-plane iframe) is readable
    return true
  } catch {
    return false // cross-origin embed
  }
}

/**
 * Host-dropped context handoff (parent-owned drag + postMessage).
 * Native HTML5 drop events do not cross iframe boundaries reliably, so the host
 * catches drops over the chat iframe and hands recognized context objects
 * over by postMessage instead:
 *
 *   host -> widget: {
 *     type: '<context-attach-message>',
 *     context: {
 *       id, kind, label, summary, ref, logical_path, event_source_id
 *     }
 *   }
 *
 * Chat does not special-case memory, task, canvas, file, or other subsystem
 * event names. Context producers must emit the generic context envelope, and
 * canvas cards attach as the proxied objects they represent. Canvas selection
 * focus, when present, comes from the attached canvas context itself.
 */
export function recognizeContextMessage(data: unknown): RecognizedContext[] {
  return recognizeContextMessageWithTypes(data, {
    attach: CHAT_CONTEXT_ATTACH_MESSAGE,
    focus: CHAT_CONTEXT_FOCUS_MESSAGE,
    remove: CHAT_CONTEXT_REMOVE_MESSAGE,
  })
}

export function recognizeContextRemoval(data: unknown): string[] {
  return recognizeContextRemovalWithTypes(data, {
    attach: CHAT_CONTEXT_ATTACH_MESSAGE,
    focus: CHAT_CONTEXT_FOCUS_MESSAGE,
    remove: CHAT_CONTEXT_REMOVE_MESSAGE,
  })
}
