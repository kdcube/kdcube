/**
 * `capabilities.open` — the scene surface-command contract for the capability
 * picker, mirroring `connections.hub.open`.
 *
 * EMIT (the chat composer's expand affordance, and the consent-banner
 * spotlight when it prefers the readable presentation): post a
 * `kdcube.surface.command` to the parent frame targeting
 * `sdk.agent.capabilities` and await the host's `{command_id, ok}` ack. An
 * acked command means a scene window opened (resizable/dockable like every
 * widget); a timeout or standalone context keeps the in-chat modal.
 *
 * RECEIVE (the served `capabilities` widget): parse the routed command,
 * apply `{agent_id?, conversation_id?, spotlight_tools?, section?}` at
 * runtime, and ack for host diagnostics. A chat-originated open carries the
 * active conversation id; an independently mounted widget has no id and edits
 * the future-conversation baseline.
 */

export const CAPABILITIES_SURFACE = 'sdk.agent.capabilities'
/** `connections.hub.open` target surfaces (the contract this one mirrors). */
export const CONNECTION_HUB_CONNECTIONS_SURFACE = 'connection_hub.connections'
export const CONNECTION_HUB_SETTINGS_SURFACE = 'connection_hub.settings'
export const SURFACE_COMMAND_MESSAGE_TYPE = 'kdcube.surface.command'
export const SURFACE_COMMAND_ACK_MESSAGE_TYPE = 'kdcube.surface.command.ack'
const CAPABILITIES_ACK_TIMEOUT_MS = 600

/** Runtime payload of one `capabilities.open` (the command's `ui_event`). */
export interface CapabilitiesOpenPayload {
  /** The bundle agent whose inventory the picker should manage. */
  agent_id?: string
  /** Exact conversation to edit; absent means the user baseline. */
  conversation_id?: string
  /** Entries to highlight + scroll to (`alias.tool` or a namespace token). */
  spotlight_tools?: string[]
  /** Section to bring into view: model | skills | tools | mcp | services. */
  section?: string
}

export interface CapabilitiesOpenCommand {
  targetSurface: string
  commandId: string
  payload: CapabilitiesOpenPayload
}

type WindowLike = {
  parent: WindowLike | null
  postMessage?: (message: unknown, targetOrigin: string) => void
  addEventListener: (type: string, listener: (event: MessageEvent) => void) => void
  removeEventListener: (type: string, listener: (event: MessageEvent) => void) => void
  setTimeout: (handler: () => void, ms: number) => number
  clearTimeout: (id: number) => void
}

function defaultWindow(): WindowLike | null {
  return typeof window === 'undefined' ? null : (window as unknown as WindowLike)
}

/** Ask the HOST to open the capability picker as a scene window.
 *
 * Resolves true only on an explicit `{command_id, ok: true}` ack — the
 * caller keeps its in-chat presentation on timeout, a negative ack, or a
 * standalone (non-embedded) context. Never throws.
 */
export function openCapabilitiesOnHost(
  payload: CapabilitiesOpenPayload = {},
  options: { source?: string; widget?: string; timeoutMs?: number; win?: WindowLike | null } = {},
): Promise<boolean> {
  const ui_event: Record<string, unknown> = {}
  const agent = String(payload.agent_id || '').trim()
  if (agent) ui_event.agent_id = agent
  const conversation = String(payload.conversation_id || '').trim()
  if (conversation) ui_event.conversation_id = conversation
  const spotlight = (payload.spotlight_tools ?? []).map((item) => String(item || '').trim()).filter(Boolean)
  if (spotlight.length) ui_event.spotlight_tools = spotlight
  const section = String(payload.section || '').trim()
  if (section) ui_event.section = section
  return emitSurfaceOpenAndAwaitAck(CAPABILITIES_SURFACE, ui_event, 'caps', options)
}

/** Ask the HOST to open the Connection Hub as a scene window (the
 *  `connections.hub.open` contract). A consent payload targets the hub's
 *  provider-connections card and rides `ui_event` verbatim (tab + the deep
 *  link's query params); without one the hub opens on its settings surface.
 *  Same ack semantics as `openCapabilitiesOnHost` — the caller falls back to
 *  its deep link on false. Never throws. */
export function openConnectionsOnHost(
  consent?: { tab?: string; params?: Record<string, string> } | null,
  options: { source?: string; widget?: string; timeoutMs?: number; win?: WindowLike | null } = {},
): Promise<boolean> {
  const surface = consent ? CONNECTION_HUB_CONNECTIONS_SURFACE : CONNECTION_HUB_SETTINGS_SURFACE
  const ui_event: Record<string, unknown> = consent
    ? { tab: consent.tab || 'provider_connections', ...(consent.params || {}) }
    : {}
  return emitSurfaceOpenAndAwaitAck(surface, ui_event, 'connhub', options)
}

/** Ask the HOST to summon an arbitrary declared scene surface (a requirement
 *  affordance's on-scene target, e.g. `task_tracker.issue_list`). Same ack
 *  semantics as `openCapabilitiesOnHost` — resolves true only on an explicit
 *  positive ack, so the caller falls back to its absolute-URL new tab on
 *  false. Never throws. */
export function openSurfaceOnHost(
  targetSurface: string,
  ui_event: Record<string, unknown> = {},
  options: { source?: string; widget?: string; timeoutMs?: number; win?: WindowLike | null } = {},
): Promise<boolean> {
  const surface = String(targetSurface || '').trim()
  if (!surface) return Promise.resolve(false)
  return emitSurfaceOpenAndAwaitAck(surface, ui_event, 'surface', options)
}

/** Shared emitter: post one `open` surface command to the parent frame and
 *  resolve true only on an explicit `{command_id, ok: true}` ack. */
function emitSurfaceOpenAndAwaitAck(
  targetSurface: string,
  ui_event: Record<string, unknown>,
  idPrefix: string,
  options: { source?: string; widget?: string; timeoutMs?: number; win?: WindowLike | null } = {},
): Promise<boolean> {
  const win = options.win !== undefined ? options.win : defaultWindow()
  return new Promise((resolve) => {
    if (!win || !win.parent || win.parent === win || !win.parent.postMessage) {
      resolve(false)
      return
    }
    const commandId = `${idPrefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
    let settled = false
    const finish = (acked: boolean) => {
      if (settled) return
      settled = true
      win.removeEventListener('message', onMessage)
      win.clearTimeout(timer)
      resolve(acked)
    }
    function onMessage(event: MessageEvent) {
      const data = event.data as Record<string, unknown> | null
      if (!data || typeof data !== 'object') return
      if (data.type !== SURFACE_COMMAND_ACK_MESSAGE_TYPE) return
      if (String(data.command_id || '') !== commandId) return
      finish(data.ok !== false)
    }
    win.addEventListener('message', onMessage)
    const timer = win.setTimeout(() => finish(false), options.timeoutMs ?? CAPABILITIES_ACK_TIMEOUT_MS)
    try {
      const command: Record<string, unknown> = {
        type: SURFACE_COMMAND_MESSAGE_TYPE,
        target_surface: targetSurface,
        action: 'open',
        command_id: commandId,
        source: options.source || 'chat',
      }
      if (options.widget) command.widget = options.widget
      if (Object.keys(ui_event).length) command.ui_event = ui_event
      win.parent.postMessage(command, '*')
    } catch {
      finish(false)
    }
  })
}

/** Parse a routed `capabilities.open` surface command (widget side). */
export function parseCapabilitiesOpen(data: unknown): CapabilitiesOpenCommand | null {
  if (!data || typeof data !== 'object') return null
  const raw = data as Record<string, unknown>
  if (raw.type !== SURFACE_COMMAND_MESSAGE_TYPE) return null
  const target = typeof raw.target_surface === 'string' ? raw.target_surface.trim().toLowerCase() : ''
  if (target !== CAPABILITIES_SURFACE) return null
  const action = typeof raw.action === 'string' ? raw.action.trim().toLowerCase() : ''
  if (action && action !== 'open') return null
  const source = (raw.ui_event && typeof raw.ui_event === 'object' ? raw.ui_event : {}) as Record<string, unknown>
  const payload: CapabilitiesOpenPayload = {}
  const agent = typeof source.agent_id === 'string' ? source.agent_id.trim() : ''
  if (agent) payload.agent_id = agent
  const conversation = typeof source.conversation_id === 'string' ? source.conversation_id.trim() : ''
  if (conversation) payload.conversation_id = conversation
  if (Array.isArray(source.spotlight_tools)) {
    const spotlight = source.spotlight_tools.map((item) => String(item || '').trim()).filter(Boolean)
    if (spotlight.length) payload.spotlight_tools = spotlight
  }
  const section = typeof source.section === 'string' ? source.section.trim() : ''
  if (section) payload.section = section
  return {
    targetSurface: target,
    commandId: typeof raw.command_id === 'string' ? raw.command_id.trim() : '',
    payload,
  }
}

/** Widget-side diagnostics ack (the scene host acks the emitter itself). */
export function ackCapabilitiesOpen(
  command: CapabilitiesOpenCommand,
  reason: string,
  win: WindowLike | null = defaultWindow(),
): void {
  try {
    if (!win || !win.parent || win.parent === win || !win.parent.postMessage) return
    const ack: Record<string, unknown> = {
      type: SURFACE_COMMAND_ACK_MESSAGE_TYPE,
      target_surface: command.targetSurface,
      action: 'open',
      reason,
      ts: new Date().toISOString(),
    }
    if (command.commandId) {
      ack.command_id = command.commandId
      ack.ok = true
    }
    win.parent.postMessage(ack, '*')
  } catch {
    /* host diagnostics are best-effort only */
  }
}
