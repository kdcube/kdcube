/**
 * The KDCube context-pin contract — the single, enforced shape for "a draggable
 * reference to an object" shared by every producer and consumer (chat, canvas,
 * memory, tasks, and any future bundle).
 *
 * WHY THIS EXISTS
 * Historically each system rolled its own `dataTransfer.setData(...)` with its
 * own envelope shape (`{context}` / `{contexts}` / bare / `{type,contexts}`) and
 * its own uri field (`ref` / `object_ref` / `logical_path` / `hosted_uri`). Every
 * producer×consumer pair whose shapes/fields didn't line up silently fell back
 * to plain text and minted a generic object instead of the native ref. This
 * module replaces that zoo with one contract:
 *
 *   - ONE envelope:  { type: 'kdcube.context.attach', source?, contexts: ContextItem[] }
 *   - ONE uri field: ContextItem.ref   (required by the type — producers cannot omit it)
 *
 * HOW THE CONTRACT IS ENFORCED (not just documented)
 *   - Producers call `buildContextDrag()`. `ContextItem.ref` is a required field,
 *     so a producer literally cannot compile without putting the uri in `.ref`,
 *     and the function always writes the canonical envelope. Map any backend
 *     alias (`object_ref`, …) to `.ref` ONCE, here at the producer boundary.
 *   - Consumers call `parseContextDrop()` — the single validator/normalizer per
 *     drop boundary (canvas drop, chat composer). It stays lenient about legacy
 *     envelopes/aliases ONLY to absorb not-yet-migrated producers during the
 *     transition; tighten it once every producer uses `buildContextDrag`.
 */

/** A draggable reference to an object. `ref` is the one canonical uri. */
export interface ContextItem {
  id: string
  /** Semantic kind, e.g. 'object.ref' | 'memory' | 'file' | 'conversation' | 'task.issue'. */
  kind: string
  label: string
  /** THE canonical object uri — `task:issue:…` | `mem:…` | `fi:conv_…` | `cnv:…` | `conv:…`. Required. */
  ref: string
  summary?: string
  mime?: string
  event_source_id?: string
  surface?: string
  data?: Record<string, unknown>
}

export interface ContextDragEnvelope {
  type: string
  source?: string
  contexts: ContextItem[]
}

/** The canonical drag MIME + envelope `type`. */
export const CONTEXT_DRAG_MIME = 'application/vnd.kdcube.context+json'
export const CONTEXT_ATTACH_TYPE = 'kdcube.context.attach'

/** Uri aliases the consumer normalizer collapses into `.ref` (legacy producers). */
const REF_ALIASES = [
  'ref', 'object_ref', 'objectRef', 'logical_path', 'logicalPath',
  'hosted_uri', 'hostedUri', 'event_ref', 'eventRef', 'uri', 'canonical_uri',
]

function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function pickRef(raw: Record<string, unknown>): string {
  for (const key of REF_ALIASES) {
    const value = str(raw[key])
    if (value) return value
  }
  const data = raw.data && typeof raw.data === 'object' ? raw.data as Record<string, unknown> : null
  if (data) {
    for (const key of REF_ALIASES) {
      const value = str(data[key])
      if (value) return value
    }
  }
  return ''
}

/**
 * Producer entry point. Writes the canonical context-pin envelope to a
 * DataTransfer (both `application/json` and the dedicated MIME), plus a plain
 * label and a `text/uri-list` for non-aware drop targets. Every producer should
 * call this instead of hand-rolling `setData`.
 */
export function buildContextDrag(
  dataTransfer: DataTransfer,
  items: ContextItem | ContextItem[],
  options: { source?: string; type?: string } = {},
): void {
  const list = (Array.isArray(items) ? items : [items]).filter((item) => item && str(item.ref))
  const envelope: ContextDragEnvelope = {
    type: options.type || CONTEXT_ATTACH_TYPE,
    ...(options.source ? { source: options.source } : {}),
    contexts: list,
  }
  const json = JSON.stringify(envelope)
  dataTransfer.effectAllowed = 'copy'
  dataTransfer.setData('application/json', json)
  dataTransfer.setData(CONTEXT_DRAG_MIME, json)
  const first = list[0]
  if (first) {
    dataTransfer.setData('text/plain', first.label || first.ref || first.id)
    if (first.ref) dataTransfer.setData('text/uri-list', first.ref)
  }
}

function normalizeItem(raw: unknown, index: number): ContextItem | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const kind = str(r.kind) || str(r.type) || 'object.ref'
  const ref = pickRef(r)
  const label = str(r.label) || str(r.title) || str(r.name) || ref || kind
  const id = str(r.id) || str(r.context_id) || ref || `${kind}:${index}`
  if (!ref && !id) return null
  return {
    id,
    kind,
    label: label || id,
    ref,
    summary: str(r.summary) || undefined,
    mime: str(r.mime) || str(r.mime_type) || undefined,
    event_source_id: str(r.event_source_id) || str(r.eventSourceId) || undefined,
    surface: str(r.surface) || undefined,
    data: r.data && typeof r.data === 'object' ? r.data as Record<string, unknown> : undefined,
  }
}

/**
 * Consumer entry point. Accepts a parsed object or a raw JSON string and returns
 * normalized `ContextItem[]`, with every uri alias mapped to `.ref`. Tolerant of
 * the four legacy envelopes during migration: `{contexts:[…]}`, `{items:[…]}`,
 * `{context}` (singular), and a bare item. Once all producers use
 * `buildContextDrag`, the only shape on the wire is `{type, contexts:[…]}`.
 */
export function parseContextDrop(raw: unknown): ContextItem[] {
  let parsed: unknown = raw
  if (typeof raw === 'string') {
    try {
      parsed = JSON.parse(raw)
    } catch {
      return []
    }
  }
  if (!parsed || typeof parsed !== 'object') return []
  const message = parsed as Record<string, unknown>
  const rawList: unknown[] =
    Array.isArray(message.contexts) ? message.contexts
      : Array.isArray(message.items) ? message.items
        : message.context ? [message.context]
          : (message.kind || message.ref || message.object_ref || message.id) ? [message]
            : []
  return rawList
    .map((item, index) => normalizeItem(item, index))
    .filter((item): item is ContextItem => Boolean(item))
}
