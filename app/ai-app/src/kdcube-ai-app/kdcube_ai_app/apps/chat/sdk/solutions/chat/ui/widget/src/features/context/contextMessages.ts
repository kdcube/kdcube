export interface RecognizedContext {
  id: string
  kind: string
  label: string
  summary?: string
  ref?: string
  logicalPath?: string
  hostedUri?: string
  mime?: string
  canvasId?: string
  canvasName?: string
  revision?: number
  cardId?: string
  cardType?: string
  selected?: boolean
  eventSourceId?: string
  surface?: string
  data?: Record<string, unknown>
}

export interface ContextMessageTypes {
  attach: string
  focus: string
  remove: string
}

export const KDCUBE_CONTEXT_MIME_TYPE = 'application/vnd.kdcube.context+json'
const GENERIC_CONTEXT_ATTACH = 'kdcube.context.attach'
const GENERIC_CONTEXT_FOCUS = 'kdcube.context.focus'
const GENERIC_CONTEXT_REMOVE = 'kdcube.context.remove'

function compactId(value: unknown, fallback: string): string {
  const raw = String(value || '').trim()
  return raw || fallback
}

function normalizeContext(ctx: Record<string, unknown>, index = 0): RecognizedContext | null {
  const kind = String(ctx.kind || ctx.type || '').trim()
  if (!kind) return null
  const label = String(ctx.label || ctx.title || ctx.name || kind).trim()
  const ref = ctx.ref ?? ctx.logical_path ?? ctx.logicalPath
  const id = compactId(ctx.id || ctx.context_id || ref, `${kind}:${index}`)
  const data = ctx.data && typeof ctx.data === 'object' ? ctx.data as Record<string, unknown> : undefined
  const revisionRaw = ctx.revision
  const revision = typeof revisionRaw === 'number'
    ? revisionRaw
    : typeof revisionRaw === 'string' && revisionRaw.trim()
      ? Number(revisionRaw)
      : undefined
  return {
    id,
    kind,
    label: label || id,
    summary: ctx.summary != null ? String(ctx.summary) : undefined,
    ref: ref != null ? String(ref) : undefined,
    logicalPath: ctx.logicalPath != null
      ? String(ctx.logicalPath)
      : ctx.logical_path != null
        ? String(ctx.logical_path)
        : undefined,
    hostedUri: ctx.hostedUri != null
      ? String(ctx.hostedUri)
      : ctx.hosted_uri != null
        ? String(ctx.hosted_uri)
        : undefined,
    mime: ctx.mime != null ? String(ctx.mime) : undefined,
    canvasId: ctx.canvasId != null
      ? String(ctx.canvasId)
      : ctx.canvas_id != null
        ? String(ctx.canvas_id)
        : undefined,
    canvasName: ctx.canvasName != null
      ? String(ctx.canvasName)
      : ctx.canvas_name != null
        ? String(ctx.canvas_name)
        : undefined,
    revision: Number.isFinite(revision) ? revision : undefined,
    cardId: ctx.cardId != null
      ? String(ctx.cardId)
      : ctx.card_id != null
        ? String(ctx.card_id)
        : undefined,
    cardType: ctx.cardType != null
      ? String(ctx.cardType)
      : ctx.card_type != null
        ? String(ctx.card_type)
        : undefined,
    selected: typeof ctx.selected === 'boolean' ? ctx.selected : undefined,
    eventSourceId: ctx.eventSourceId != null
      ? String(ctx.eventSourceId)
      : ctx.event_source_id != null
        ? String(ctx.event_source_id)
        : undefined,
    surface: ctx.surface != null ? String(ctx.surface) : undefined,
    data,
  }
}

function normalizeContextsFromPayload(data: unknown): RecognizedContext[] {
  if (!data || typeof data !== 'object') return []
  const message = data as Record<string, unknown>
  const rawContexts = Array.isArray(message.contexts)
    ? message.contexts
    : Array.isArray(message.items)
      ? message.items
      : [message.context]
  return rawContexts
    .filter((ctx): ctx is Record<string, unknown> => Boolean(ctx) && typeof ctx === 'object')
    .map((ctx, index) => normalizeContext(ctx, index))
    .filter((ctx): ctx is RecognizedContext => Boolean(ctx))
}

export function recognizeContextPayload(data: unknown): RecognizedContext[] {
  return normalizeContextsFromPayload(data)
}

export function recognizeContextMessageWithTypes(data: unknown, types: ContextMessageTypes): RecognizedContext[] {
  if (!data || typeof data !== 'object') return []
  const message = data as Record<string, unknown>
  const type = String(message.type || '').trim()
  if (
    type !== types.attach &&
    type !== types.focus &&
    type !== GENERIC_CONTEXT_ATTACH &&
    type !== GENERIC_CONTEXT_FOCUS
  ) return []
  return normalizeContextsFromPayload(message)
}

export function recognizeContextRemovalWithTypes(data: unknown, types: ContextMessageTypes): string[] {
  if (!data || typeof data !== 'object') return []
  const message = data as Record<string, unknown>
  const type = String(message.type || '').trim()
  if (type !== types.remove && type !== GENERIC_CONTEXT_REMOVE) return []
  const rawIds = Array.isArray(message.ids) ? message.ids : [message.id]
  return rawIds
    .map((id) => String(id || '').trim())
    .filter(Boolean)
}

export function setContextDragData(dataTransfer: DataTransfer, context: RecognizedContext | Record<string, unknown>): void {
  const label = String(
    (context as Record<string, unknown>).label ||
    (context as Record<string, unknown>).title ||
    (context as Record<string, unknown>).ref ||
    (context as Record<string, unknown>).id ||
    'context',
  )
  dataTransfer.effectAllowed = 'copy'
  // Canonical context-drag shape is the plural `contexts: [...]` envelope so the
  // SAME payload is recognized by both the chat composer (recognizeContextPayload)
  // and the canvas drop (normalizeContextMessage). Emitting singular `context`
  // here let canvas drops fall through to text -> a generic `cnv:` card instead of
  // the native object ref.
  dataTransfer.setData(KDCUBE_CONTEXT_MIME_TYPE, JSON.stringify({ contexts: [context] }))
  dataTransfer.setData('application/json', JSON.stringify({ type: GENERIC_CONTEXT_ATTACH, contexts: [context] }))
  dataTransfer.setData('text/plain', label)
}
