/** Parent-broker postMessage protocol for chat-originated canvas ingress.
 *
 * Native drag-and-drop does not cross iframe boundaries reliably, so the
 * chat iframe forwards drag intents as structured messages to the parent
 * main UI. The main UI normalizes the payload through
 * `features/canvas/ingress.ts` and writes the resulting card via
 * `canvas_patch`.
 *
 * Two ingress payloads pass through this bridge:
 *   - `chat.artifact`: the user dragged an agent-produced object row from
 *     chat. Durable `fi:` refs become file cards; other namespace refs become
 *     `object.ref` cards. In both cases the ref flows through verbatim; no
 *     rehosting.
 *   - `chat.assistant.text`: the user dragged assistant response text from
 *     chat. The bundle rehosts the text as a versioned `cnv:` `agent.text`
 *     object.
 *
 * The other three ingress paths (local file, selected text, search-result
 * row) originate inside the main UI itself and don't go through this
 * bridge.
 *
 * The bridge is intentionally tiny and stateless so chat and canvas can
 * remain separate SDK-extractable components. The main UI is the only
 * place that decides which board the card lands on.
 */

export const INGRESS_MESSAGE_TYPE = 'kdcube-canvas-ingress'

export type CanvasIngressKind = 'chat.artifact' | 'chat.assistant.text'

/** Chat artifact/object drop. The chat iframe already knows the canonical
 *  resolver ref of the row (its own client built the listing); the main UI
 *  never resolves it. */
export interface CanvasIngressArtifactPayload {
  kind: 'chat.artifact'
  /** Canonical resolver ref. Cross-conversation file refs
   *  (`fi:conv_<id>.turn_<id>...`) and namespace refs such as
   *  `mem:record:<id>` flow through verbatim. */
  ref: string
  mime: string
  filename?: string
  /** Optional UI-only preview text for the card title before the ref
   *  resolves. */
  preview?: string
  /** Echo of the chat conversation/turn so the main UI can attribute the
   *  card if it wants. Optional. */
  source?: { conversation_id?: string; turn_id?: string }
}

/** Chat assistant text drop. The text is the user's selection from the
 *  rendered assistant message in chat. */
export interface CanvasIngressAssistantTextPayload {
  kind: 'chat.assistant.text'
  text: string
  /** Optional preview/title hint; if absent the normalizer derives one
   *  from the leading characters of `text`. */
  title?: string
  source?: { conversation_id?: string; turn_id?: string }
}

export type CanvasIngressPayload =
  | CanvasIngressArtifactPayload
  | CanvasIngressAssistantTextPayload

export interface CanvasIngressMessage {
  type: typeof INGRESS_MESSAGE_TYPE
  payload: CanvasIngressPayload
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function trimOrUndefined(value: unknown): string | undefined {
  const str = asString(value).trim()
  return str || undefined
}

function parseSource(value: unknown): { conversation_id?: string; turn_id?: string } | undefined {
  if (!value || typeof value !== 'object') return undefined
  const record = value as Record<string, unknown>
  const conversation_id = trimOrUndefined(record.conversation_id)
  const turn_id = trimOrUndefined(record.turn_id)
  if (!conversation_id && !turn_id) return undefined
  return { conversation_id, turn_id }
}

function isDurableCanvasFiRef(ref: string): boolean {
  return /^fi:conv_[^.]+\.(turn_[^.]+)\./.test(String(ref || '').trim())
}

function isValidCanvasArtifactRef(ref: string): boolean {
  const value = String(ref || '').trim()
  if (!value) return false
  return !value.startsWith('fi:') || isDurableCanvasFiRef(value)
}

/** Normalize an inbound postMessage envelope. Returns `null` when the data
 *  is not a recognized ingress message; defensive on every shape so a
 *  malformed payload never throws into the main UI's event loop. */
export function parseIngressMessage(data: unknown): CanvasIngressMessage | null {
  if (!data || typeof data !== 'object') return null
  const record = data as Record<string, unknown>
  if (record.type !== INGRESS_MESSAGE_TYPE) return null
  const payloadRaw = record.payload
  if (!payloadRaw || typeof payloadRaw !== 'object') return null
  const payload = payloadRaw as Record<string, unknown>
  switch (payload.kind) {
    case 'chat.artifact': {
      const ref = asString(payload.ref).trim()
      const mime = asString(payload.mime).trim()
      const source = parseSource(payload.source)
      if (!ref || !mime || !isValidCanvasArtifactRef(ref)) return null
      const result: CanvasIngressArtifactPayload = {
        kind: 'chat.artifact',
        ref,
        mime,
      }
      const filename = trimOrUndefined(payload.filename)
      if (filename) result.filename = filename
      const preview = trimOrUndefined(payload.preview)
      if (preview) result.preview = preview
      if (source) result.source = source
      return { type: INGRESS_MESSAGE_TYPE, payload: result }
    }
    case 'chat.assistant.text': {
      const text = asString(payload.text)
      if (!text.trim()) return null
      const result: CanvasIngressAssistantTextPayload = {
        kind: 'chat.assistant.text',
        text,
      }
      const title = trimOrUndefined(payload.title)
      if (title) result.title = title
      const source = parseSource(payload.source)
      if (source) result.source = source
      return { type: INGRESS_MESSAGE_TYPE, payload: result }
    }
    default:
      return null
  }
}

/** Build the envelope for chat side to post. Kept here so the message type
 *  string only has one home in the codebase. */
export function buildIngressMessage(payload: CanvasIngressPayload): CanvasIngressMessage {
  return { type: INGRESS_MESSAGE_TYPE, payload }
}
