import { buildContextDrag, CONTEXT_ATTACH_TYPE, CONTEXT_DRAG_MIME, type ContextItem } from '@kdcube/components-core'

export interface RecognizedContext {
  id: string
  kind: string
  label: string
  summary?: string
  ref?: string
  object_ref?: string
  namespace?: string
  object_kind?: string
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

export const KDCUBE_CONTEXT_MIME_TYPE = CONTEXT_DRAG_MIME
const GENERIC_CONTEXT_ATTACH = CONTEXT_ATTACH_TYPE
const GENERIC_CONTEXT_FOCUS = 'kdcube.context.focus'
const GENERIC_CONTEXT_REMOVE = 'kdcube.context.remove'
const REF_KEYS = [
  'ref',
  'object_ref',
  'objectRef',
  'logical_path',
  'logicalPath',
  'hosted_uri',
  'hostedUri',
  'event_ref',
  'eventRef',
  'uri',
  'canonical_uri',
]

function compactId(value: unknown, fallback: string): string {
  const raw = String(value || '').trim()
  return raw || fallback
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function contextObjectRef(context: Record<string, unknown>): string {
  for (const key of REF_KEYS) {
    const value = stringValue(context[key])
    if (value) return value
  }
  const data = context.data && typeof context.data === 'object' ? context.data as Record<string, unknown> : null
  if (data) {
    for (const key of REF_KEYS) {
      const value = stringValue(data[key])
      if (value) return value
    }
  }
  return ''
}

function postParentDragMessage(message: Record<string, unknown>): void {
  if (typeof window === 'undefined' || !window.parent || window.parent === window) return
  window.parent.postMessage(message, '*')
}

function dragEndPoint(event: DragEvent): Record<string, number> {
  return {
    client_x: event.clientX,
    client_y: event.clientY,
    screen_x: event.screenX,
    screen_y: event.screenY,
  }
}

function toContextItem(context: Record<string, unknown>, ref: string, label: string): ContextItem {
  const kind = String(context.kind || context.type || 'object.ref').trim() || 'object.ref'
  const id = compactId(context.id || context.context_id || ref, `${kind}:${ref}`)
  const data = context.data && typeof context.data === 'object'
    ? context.data as Record<string, unknown>
    : undefined
  return {
    id,
    kind,
    label,
    ref,
    summary: context.summary != null ? String(context.summary) : undefined,
    mime: context.mime != null ? String(context.mime) : undefined,
    event_source_id: context.event_source_id != null
      ? String(context.event_source_id)
      : context.eventSourceId != null
        ? String(context.eventSourceId)
        : undefined,
    surface: context.surface != null ? String(context.surface) : undefined,
    data,
  }
}

function normalizeContext(ctx: Record<string, unknown>, index = 0): RecognizedContext | null {
  const kind = String(ctx.kind || ctx.type || '').trim()
  if (!kind) return null
  const label = String(ctx.label || ctx.title || ctx.name || kind).trim()
  const ref = ctx.ref ?? ctx.object_ref ?? ctx.objectRef ?? ctx.logical_path ?? ctx.logicalPath
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
    object_ref: ctx.object_ref != null
      ? String(ctx.object_ref)
      : ctx.objectRef != null
        ? String(ctx.objectRef)
        : undefined,
    namespace: ctx.namespace != null ? String(ctx.namespace) : undefined,
    object_kind: ctx.object_kind != null
      ? String(ctx.object_kind)
      : ctx.objectKind != null
        ? String(ctx.objectKind)
        : undefined,
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
  const rawContext = context as Record<string, unknown>
  const label = String(
    rawContext.label ||
    rawContext.title ||
    rawContext.ref ||
    rawContext.id ||
    'context',
  )
  const ref = contextObjectRef(rawContext)
  dataTransfer.effectAllowed = 'copy'
  if (ref) buildContextDrag(dataTransfer, toContextItem(rawContext, ref, label), { source: 'chat-widget' })
  else dataTransfer.setData('text/plain', label)
  postParentDragMessage({
    type: 'kdcube-context-drag-start',
    source: 'chat-widget',
    context,
  })
  window.addEventListener('dragend', (event) => {
    postParentDragMessage({ type: 'kdcube-context-drag-end', source: 'chat-widget', ...dragEndPoint(event) })
  }, { once: true })
}

// --- convenience for the composer drop handler (ported from the widget's host.ts
//     recognizeContextMessage, with the default message-type constants inlined) ---
export const CHAT_CONTEXT_ATTACH_MESSAGE = 'kdcube.context.attach'
const CHAT_CONTEXT_FOCUS_MESSAGE = 'kdcube.context.focus'
const CHAT_CONTEXT_REMOVE_MESSAGE = 'kdcube.context.remove'

export function recognizeContextMessage(data: unknown): RecognizedContext[] {
  return recognizeContextMessageWithTypes(data, {
    attach: CHAT_CONTEXT_ATTACH_MESSAGE,
    focus: CHAT_CONTEXT_FOCUS_MESSAGE,
    remove: CHAT_CONTEXT_REMOVE_MESSAGE,
  })
}
