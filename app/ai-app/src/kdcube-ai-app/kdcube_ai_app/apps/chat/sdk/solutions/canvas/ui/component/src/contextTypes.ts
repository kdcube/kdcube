export interface CanvasContextItem {
  id: string
  kind: string
  label: string
  summary?: string
  ref?: string
  logical_path?: string
  mime?: string
  canvas_id?: string
  canvas_name?: string
  revision?: number
  card_id?: string
  card_type?: string
  selected?: boolean
  event_source_id?: string
  surface?: string
  data?: Record<string, unknown>
}

export interface CanvasContextMessage {
  type?: string
  source?: string
  contexts: CanvasContextItem[]
}

export type StoryDefinitionPayload = Record<string, unknown>

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function stringValue(value: unknown): string | undefined {
  if (value == null) return undefined
  const text = String(value).trim()
  return text || undefined
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

export function normalizeContext(value: unknown): CanvasContextItem | null {
  const raw = asRecord(value)
  if (!raw) return null
  const id = stringValue(raw.id)
  const kind = stringValue(raw.kind)
  const label = stringValue(raw.label ?? raw.title)
  if (!id || !kind || !label) return null
  return {
    id,
    kind,
    label,
    summary: stringValue(raw.summary),
    ref: stringValue(raw.ref),
    logical_path: stringValue(raw.logical_path ?? raw.logicalPath),
    mime: stringValue(raw.mime),
    canvas_id: stringValue(raw.canvas_id ?? raw.canvasId),
    canvas_name: stringValue(raw.canvas_name ?? raw.canvasName),
    revision: numberValue(raw.revision),
    card_id: stringValue(raw.card_id ?? raw.cardId),
    card_type: stringValue(raw.card_type ?? raw.cardType),
    selected: Boolean(raw.selected),
    event_source_id: stringValue(raw.event_source_id ?? raw.eventSourceId),
    surface: stringValue(raw.surface),
    data: asRecord(raw.data) ?? undefined,
  }
}

export function normalizeContextMessage(value: unknown): CanvasContextMessage | null {
  const raw = asRecord(value)
  if (!raw) return null
  const contextsRaw = Array.isArray(raw.contexts) ? raw.contexts : []
  const contexts = contextsRaw.map(normalizeContext).filter((item): item is CanvasContextItem => Boolean(item))
  if (!contexts.length) return null
  return {
    type: stringValue(raw.type),
    source: stringValue(raw.source),
    contexts,
  }
}
