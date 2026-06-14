import type { ExternalEvent } from '../../api/types'

export type EventIdFactory = (prefix: string) => string

export interface AttachedContext {
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

export interface ChatEventSourceDefaults {
  userEventSourceId?: string
  attachmentEventSourceId?: string
  contextEventSourceId?: string
  chatSurface?: string
  canvasStateEventSourceId?: string
  canvasFocusEventSourceId?: string
  canvasSurface?: string
  snapshotEventSourceId?: string
  snapshotSurface?: string
}

export interface BuildContextEventsOptions {
  agentId?: string
  eventId?: EventIdFactory
  defaults?: ChatEventSourceDefaults
}

export interface BuildUserPromptEventOptions {
  agentId?: string
  eventId?: EventIdFactory
  reactiveEventType?: string
  target?: Record<string, unknown>
  defaults?: ChatEventSourceDefaults
  allowEmptyText?: boolean
}

export interface AttachmentEventInput {
  name: string
  size: number
  type?: string
}

export interface BuildAttachmentEventsOptions {
  agentId?: string
  eventId?: EventIdFactory
  target?: Record<string, unknown>
  defaults?: ChatEventSourceDefaults
}

export interface BuildExternalEventBatchOptions extends BuildContextEventsOptions {
  text?: string
  files?: AttachmentEventInput[]
  reactiveEventType?: string
  target?: Record<string, unknown>
}

export function contextRef(ctx: AttachedContext): string | undefined {
  return ctx.ref || ctx.logicalPath
}

export function isCanvasContext(ctx: AttachedContext): boolean {
  return ctx.kind === 'canvas' || ctx.kind === 'event.canvas'
}

export function isWizardContext(ctx: AttachedContext): boolean {
  return ctx.kind === 'wizard' || ctx.kind === 'snapshot' || ctx.kind === 'event.snapshot'
}

function defaultEventId(prefix: string): string {
  return crypto.randomUUID ? crypto.randomUUID() : `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`
}

const DEFAULT_EVENT_SOURCES: Required<ChatEventSourceDefaults> = {
  userEventSourceId: 'chat.main.user',
  attachmentEventSourceId: 'chat.main.attachment',
  contextEventSourceId: 'chat.context.focus',
  chatSurface: 'chat',
  canvasStateEventSourceId: 'chat.canvas.state',
  canvasFocusEventSourceId: 'chat.canvas.focus',
  canvasSurface: 'canvas',
  snapshotEventSourceId: 'chat.snapshot',
  snapshotSurface: 'snapshot',
}

function eventSources(defaults?: ChatEventSourceDefaults): Required<ChatEventSourceDefaults> {
  return { ...DEFAULT_EVENT_SOURCES, ...(defaults || {}) }
}

function storyId(ctx: AttachedContext): string | undefined {
  return typeof ctx.data?.story_id === 'string' ? ctx.data.story_id : undefined
}

function snapshotSourceId(ctx: AttachedContext, defaults: Required<ChatEventSourceDefaults>): string {
  const explicit = ctx.data?.event_source_id
  if (typeof explicit === 'string' && explicit.trim()) return explicit.trim()
  return defaults.snapshotEventSourceId
}

function attachedContextEventSourceId(ctx: AttachedContext, defaults: Required<ChatEventSourceDefaults>): string {
  const explicit = ctx.eventSourceId || ctx.data?.event_source_id || ctx.data?.eventSourceId
  if (typeof explicit === 'string' && explicit.trim()) return explicit.trim()
  return defaults.contextEventSourceId
}

function attachedContextSurface(ctx: AttachedContext, defaults: Required<ChatEventSourceDefaults>): string {
  const explicit = ctx.surface || ctx.data?.surface
  if (typeof explicit === 'string' && explicit.trim()) return explicit.trim()
  return defaults.chatSurface
}

function contextBase(ctx: AttachedContext, options: { agentId: string; eventId: EventIdFactory }): Partial<ExternalEvent> {
  const { agentId, eventId } = options
  return {
    event_id: eventId('evt'),
    reactive: false,
    agent_id: agentId,
    story_id: storyId(ctx),
  }
}

function canvasEvent(ctx: AttachedContext, options: { agentId: string; eventId: EventIdFactory; defaults: Required<ChatEventSourceDefaults> }): ExternalEvent {
  const { agentId, eventId } = options
  const ref = contextRef(ctx)
  const projection = ctx.data?.projection && typeof ctx.data.projection === 'object'
    ? ctx.data.projection
    : undefined
  return {
    ...contextBase(ctx, { agentId, eventId }),
    type: 'event.canvas',
    event_source_id: options.defaults.canvasStateEventSourceId,
    surface: options.defaults.canvasSurface,
    ...(ref ? { hosted_uri: ref } : {}),
    payload: {
      mime: ctx.mime || 'application/vnd.kdcube.canvas+json;version=1',
      ...(ref ? { event_ref: ref } : {}),
      event: {
        context_role: 'canvas',
        id: ctx.id,
        label: ctx.label,
        summary: ctx.summary,
        canvas_id: ctx.canvasId,
        canvas_name: ctx.canvasName,
        revision: ctx.revision,
        ref,
        ...(projection ? { projection } : {}),
      },
    },
  }
}

function focusCardFromLegendRow(row: unknown): Record<string, unknown> | null {
  if (!row || typeof row !== 'object') return null
  const raw = row as Record<string, unknown>
  const cardId = String(raw.card_id || raw.cardId || raw.id || '').trim()
  const ref = String(raw.logical_path || raw.logicalPath || raw.ref || raw.artifact_ref || raw.artifactRef || '').trim()
  if (!cardId && !ref) return null
  return {
    ...(cardId ? { card_id: cardId, id: cardId } : {}),
    kind: String(raw.kind || raw.type || 'object.ref'),
    title: String(raw.title || raw.label || raw.filename || cardId || ref),
    ...(ref ? { logical_path: ref, ref } : {}),
    ...(raw.mime ? { mime: String(raw.mime) } : {}),
    ...(raw.content_preview || raw.preview || raw.summary
      ? { content_preview: String(raw.content_preview || raw.preview || raw.summary) }
      : {}),
    selected: true,
  }
}

function selectedFocusCards(ctx: AttachedContext): Record<string, unknown>[] {
  const data = ctx.data && typeof ctx.data === 'object' ? ctx.data : {}
  const projection = data.projection && typeof data.projection === 'object'
    ? data.projection as Record<string, unknown>
    : undefined
  const selectedIds = new Set(
    (Array.isArray(data.selected_card_ids) ? data.selected_card_ids : [])
      .map((value) => String(value || '').trim())
      .filter(Boolean),
  )
  const legend = Array.isArray(projection?.legend) ? projection.legend : []
  const cards = legend
    .filter((row) => {
      if (!row || typeof row !== 'object') return false
      const raw = row as Record<string, unknown>
      const id = String(raw.card_id || raw.cardId || raw.id || '').trim()
      return Boolean(raw.selected) || (id ? selectedIds.has(id) : false)
    })
    .map(focusCardFromLegendRow)
    .filter((card): card is Record<string, unknown> => Boolean(card))
  if (cards.length || !selectedIds.size) return cards
  return Array.from(selectedIds).map((id) => ({
    card_id: id,
    id,
    kind: 'object.ref',
    title: id,
    selected: true,
  }))
}

function canvasFocusEvent(ctx: AttachedContext, options: { agentId: string; eventId: EventIdFactory; defaults: Required<ChatEventSourceDefaults> }): ExternalEvent | null {
  const cards = selectedFocusCards(ctx)
  if (!cards.length) return null
  const { agentId, eventId } = options
  const ref = contextRef(ctx)
  return {
    ...contextBase(ctx, { agentId, eventId }),
    type: 'event.canvas.focus',
    event_source_id: options.defaults.canvasFocusEventSourceId,
    surface: options.defaults.canvasSurface,
    ...(ref ? { hosted_uri: ref } : {}),
    payload: {
      mime: 'application/vnd.kdcube.canvas.focus+json;version=1',
      ...(ref ? { event_ref: ref } : {}),
      event: {
        context_role: 'canvas_focus',
        canvas_id: ctx.canvasId,
        canvas_name: ctx.canvasName,
        canvas_uri: ctx.canvasName && ctx.revision !== undefined ? `canvas:${ctx.canvasName}@${ctx.revision}` : undefined,
        revision: ctx.revision,
        selection: {
          mode: 'cards',
          reason: 'canvas_selection',
        },
        focused_cards: cards,
      },
    },
  }
}

function snapshotEvent(ctx: AttachedContext, options: { agentId: string; eventId: EventIdFactory; defaults: Required<ChatEventSourceDefaults> }): ExternalEvent {
  const { agentId, eventId } = options
  const ref = contextRef(ctx)
  const contextData = ctx.data && typeof ctx.data === 'object' ? ctx.data : {}
  const contextRole = typeof contextData.context_role === 'string' && contextData.context_role.trim()
    ? contextData.context_role
    : 'snapshot'
  return {
    ...contextBase(ctx, { agentId, eventId }),
    type: 'event.snapshot',
    event_source_id: snapshotSourceId(ctx, options.defaults),
    surface: typeof ctx.data?.surface === 'string' ? ctx.data.surface : options.defaults.snapshotSurface,
    ...(ref ? { hosted_uri: ref } : {}),
    payload: {
      mime: ctx.mime || 'application/json',
      ...(ref ? { event_ref: ref } : {}),
      event: {
        ...contextData,
        context_role: contextRole,
        id: ctx.id,
        label: ctx.label,
        summary: ctx.summary,
        revision: ctx.revision,
        ref,
      },
    },
  }
}

export function buildContextEvents(contexts: AttachedContext[], options: BuildContextEventsOptions = {}): ExternalEvent[] {
  const agentId = options.agentId || 'main'
  const eventId = options.eventId || defaultEventId
  const defaults = eventSources(options.defaults)
  const events: ExternalEvent[] = []
  contexts.filter(isCanvasContext).forEach((ctx) => {
    events.push(canvasEvent(ctx, { agentId, eventId, defaults }))
    const focus = canvasFocusEvent(ctx, { agentId, eventId, defaults })
    if (focus) events.push(focus)
  })
  contexts.filter(isWizardContext).forEach((ctx) => {
    events.push(snapshotEvent(ctx, { agentId, eventId, defaults }))
  })
  contexts
    .filter((ctx) => !isCanvasContext(ctx) && !isWizardContext(ctx))
    .forEach((ctx) => {
      const ref = contextRef(ctx)
      const contextData = ctx.data && typeof ctx.data === 'object' ? ctx.data : {}
      const payloadLabel =
        ctx.kind === 'intent' && typeof contextData.label === 'string' && contextData.label.trim()
          ? contextData.label.trim()
          : ctx.label
      events.push({
        ...contextBase(ctx, { agentId, eventId }),
        type: 'event.external',
        event_source_id: attachedContextEventSourceId(ctx, defaults),
        surface: attachedContextSurface(ctx, defaults),
        ...(ref ? { hosted_uri: ref } : {}),
        payload: {
          mime: ctx.mime || 'application/json',
          ...(ref ? { event_ref: ref } : {}),
          event: {
            context_role: 'context',
            id: ctx.id,
            kind: ctx.kind,
            label: payloadLabel,
            summary: ctx.summary,
            ...(ref ? { object_ref: ref } : {}),
            ref,
            data: contextData,
          },
        },
      })
    })
  return events
}

export function buildUserPromptEvent(text: string, options: BuildUserPromptEventOptions = {}): ExternalEvent | null {
  const body = String(text || '').trim()
  const reactiveEventType = options.reactiveEventType || 'event.user.prompt'
  if (!body && reactiveEventType !== 'event.user.steer' && options.allowEmptyText !== true) return null
  const target = options.target || {}
  const eventSourceId =
    typeof target.event_source_id === 'string'
      ? target.event_source_id
      : eventSources(options.defaults).userEventSourceId
  const agentId = (target.agent_id || target.agent || options.agentId || 'main') as string
  return {
    event_id: (options.eventId || defaultEventId)('evt'),
    type: reactiveEventType,
    event_source_id: eventSourceId,
    reactive: true,
    agent_id: agentId,
    story_id: target.story_id as string | undefined,
    surface: target.surface as string | undefined,
    payload: { mime: 'text/plain', event: { text: body } },
  }
}

export function buildUserAttachmentEvents(
  files: AttachmentEventInput[],
  options: BuildAttachmentEventsOptions = {},
): ExternalEvent[] {
  const target = options.target || {}
  const eventId = options.eventId || defaultEventId
  const defaults = eventSources(options.defaults)
  const agentId = (target.agent_id || target.agent || options.agentId || 'main') as string
  return files.map((file, index) => {
    const mime = file.type || 'application/octet-stream'
    return {
      event_id: eventId('evt'),
      type: 'event.user.attachment.file',
      event_source_id: defaults.attachmentEventSourceId,
      reactive: true,
      agent_id: agentId,
      story_id: target.story_id as string | undefined,
      surface: target.surface as string | undefined,
      payload: {
        mime,
        event: {
          filename: file.name,
          size: file.size,
          mime,
          file_index: index,
        },
      },
    }
  })
}

export function buildExternalEventBatch(contexts: AttachedContext[], options: BuildExternalEventBatchOptions = {}): ExternalEvent[] {
  const events = buildContextEvents(contexts, options)
  const prompt = buildUserPromptEvent(options.text || '', {
    ...options,
    // A context-only chat send is still a user action that must wake ReAct.
    // Context events are non-reactive facts; this empty prompt/followup event
    // is the turn boundary that tells the runtime to process them.
    allowEmptyText: contexts.length > 0,
  })
  if (prompt) events.push(prompt)
  const attachments = buildUserAttachmentEvents(options.files || [], options)
  events.push(...attachments)
  return events
}
