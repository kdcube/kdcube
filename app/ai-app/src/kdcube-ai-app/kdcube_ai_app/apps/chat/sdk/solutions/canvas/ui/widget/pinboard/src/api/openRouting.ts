/**
 * Cross-surface OPEN routing for pin-card actions.
 *
 * An `open` object action that resolves to another surface (a conversation
 * opens in chat, a memory in the memory editor) is forwarded to the host
 * broker as a `kdcube.surface.command`. The command carries the resolver's
 * `ui_event` verbatim (it holds the surface-specific identity, e.g.
 * `conversation_id`) plus a `command_id`, and the sender waits briefly for
 * the host's `kdcube.surface.command.ack` — the same honest-chain idiom as
 * the chat widget's `connections.hub.open`. An un-acked command surfaces a
 * visible board notice instead of landing nowhere silently.
 */

export const SURFACE_COMMAND_MESSAGE_TYPE = 'kdcube.surface.command'
export const SURFACE_COMMAND_ACK_MESSAGE_TYPE = 'kdcube.surface.command.ack'
export const OPEN_COMMAND_ACK_TIMEOUT_MS = 900

export interface OpenSurfaceCommandInput {
  targetSurface: string
  uiEvent: Record<string, unknown>
  cardRef?: string
  fallbackObjectRef?: string
  commandId: string
}

export function newOpenCommandId(): string {
  return `pinboard_open_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

/** The routed-open command envelope posted to the host broker. */
export function buildOpenSurfaceCommand(input: OpenSurfaceCommandInput): Record<string, unknown> {
  const uiEvent = input.uiEvent && typeof input.uiEvent === 'object' ? input.uiEvent : {}
  return {
    type: SURFACE_COMMAND_MESSAGE_TYPE,
    target_surface: input.targetSurface,
    action: String((uiEvent as Record<string, unknown>).action || 'open'),
    ui_event: uiEvent,
    object_ref: String(
      (uiEvent as Record<string, unknown>).object_ref || input.fallbackObjectRef || '',
    ).trim(),
    card_ref: input.cardRef,
    command_id: input.commandId,
  }
}

/**
 * Wait for the host's ack of a routed command. Resolves true when the host
 * confirms routing (`ok` not false), false on a negative ack or timeout.
 */
export function awaitSurfaceCommandAck(
  commandId: string,
  timeoutMs: number = OPEN_COMMAND_ACK_TIMEOUT_MS,
): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const finish = (acked: boolean) => {
      if (settled) return
      settled = true
      window.removeEventListener('message', onMessage)
      window.clearTimeout(timer)
      resolve(acked)
    }
    function onMessage(event: MessageEvent) {
      const data = event.data as Record<string, unknown> | null
      if (!data || typeof data !== 'object') return
      if (data.type !== SURFACE_COMMAND_ACK_MESSAGE_TYPE) return
      if (String(data.command_id || '') !== commandId) return
      finish(data.ok !== false)
    }
    window.addEventListener('message', onMessage)
    const timer = window.setTimeout(() => finish(false), timeoutMs)
  })
}
