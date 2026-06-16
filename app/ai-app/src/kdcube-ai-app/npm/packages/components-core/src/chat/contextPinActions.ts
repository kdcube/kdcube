/**
 * Context-pin activation — resolve a pinned object's capabilities and run its
 * declared default open effect (open via the host, or download inline).
 *
 * Ported from the widget's `features/chat/contextPinActions.ts`. Two host
 * couplings change: `resolveObjectAction` now takes the `EngineRuntime`, and the
 * `requestHostObjectOpen` postMessage becomes an `object-open` event on the host
 * event bus (the host decides how to open it).
 */
import type { EngineRuntime } from './runtime.ts'
import type { HostEventEmitter } from '../shared/index.ts'
import {
  downloadObjectActionResult,
  ObjectActionRequestError,
  resolveObjectAction,
} from './transport/client.ts'
import type {
  BannerTone,
  ObjectActionCapabilities,
  ObjectActionName,
  ObjectActionResponse,
} from './protocol.ts'
import type { AttachedContext } from './state.ts'
import type { ContextChip } from './contextChips.ts'

export type ActionableContext = AttachedContext | ContextChip

export class ContextPinActionError extends Error {
  readonly tone: BannerTone

  constructor(message: string, tone: BannerTone = 'warning') {
    super(message)
    this.name = 'ContextPinActionError'
    this.tone = tone
  }
}

export function contextPinActionNotice(error: unknown): { text: string; tone: BannerTone } {
  if (error instanceof ContextPinActionError) {
    return { text: error.message, tone: error.tone }
  }
  return {
    text: error instanceof Error ? error.message : String(error),
    tone: 'error',
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function field(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = stringValue(record[key])
    if (value) return value
  }
  return ''
}

function looksLikeRef(value: string): boolean {
  return /^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith('/')
}

export function contextPinObjectRef(context: ActionableContext): string {
  const record = asRecord(context)
  const data = asRecord(record.data)
  const direct = field(record, 'ref', 'logicalPath', 'logical_path', 'hostedUri', 'hosted_uri', 'object_ref', 'event_ref', 'uri', 'canonical_uri')
  if (direct) return direct
  const nested = field(data, 'ref', 'logicalPath', 'logical_path', 'hostedUri', 'hosted_uri', 'object_ref', 'event_ref', 'uri', 'canonical_uri')
  if (nested) return nested
  const id = field(record, 'id')
  return looksLikeRef(id) ? id : ''
}

function contextLabel(context: ActionableContext, objectRef: string): string {
  const record = asRecord(context)
  return field(record, 'label', 'title', 'id') || objectRef || 'context'
}

function contextMime(context: ActionableContext): string {
  const record = asRecord(context)
  const data = asRecord(record.data)
  return field(record, 'mime', 'media_type', 'content_type') || field(data, 'mime', 'media_type', 'content_type')
}

function isAction(value: string): value is ObjectActionName {
  return ['capabilities', 'describe', 'preview', 'open', 'download', 'rehost'].includes(value)
}

function capabilitiesFrom(response: ObjectActionResponse): ObjectActionCapabilities {
  return response.capabilities && typeof response.capabilities === 'object' ? response.capabilities : {}
}

function openEffectActionFrom(response: ObjectActionResponse): ObjectActionName | '' {
  const record = response as Record<string, unknown>
  const extra = asRecord(record.extra)
  const value =
    field(record, 'default_open_effect_action', 'defaultOpenEffectAction') ||
    field(extra, 'default_open_effect_action', 'defaultOpenEffectAction')
  return isAction(value) ? value : ''
}

export function chooseContextPinOpenEffectAction(
  capabilitiesResponse: ObjectActionResponse,
): ObjectActionName | '' {
  const capabilities = capabilitiesFrom(capabilitiesResponse)
  const declaredOpenEffect = openEffectActionFrom(capabilitiesResponse)
  if ((declaredOpenEffect === 'open' || declaredOpenEffect === 'download') && capabilities[declaredOpenEffect] !== false) {
    return declaredOpenEffect
  }
  return ''
}

function sourceForContext(context: ActionableContext, objectRef: string): Record<string, unknown> {
  const record = asRecord(context)
  return {
    id: field(record, 'id') || objectRef,
    title: contextLabel(context, objectRef),
    summary: field(record, 'summary'),
    kind: field(record, 'kind', 'cardType', 'card_type') || 'context',
    ref: objectRef,
    mime: contextMime(context) || 'application/octet-stream',
  }
}

function isResolverUnavailable(error: unknown): boolean {
  if (!(error instanceof ObjectActionRequestError)) return false
  const code = String(error.code || '').toLowerCase()
  return error.action === 'capabilities' && (
    code.includes('resolver_not_registered') ||
    code.includes('not_registered') ||
    error.status === 404
  )
}

/**
 * Resolve a pinned object's capabilities and run its declared default open
 * effect. `open` is bubbled to the host via `emitter.emit('object-open', …)`;
 * `download` is handled inline. The host decides what an open means.
 */
export async function activateContextPin(
  runtime: EngineRuntime,
  emitter: HostEventEmitter,
  context: ActionableContext,
): Promise<void> {
  const objectRef = contextPinObjectRef(context)
  if (!objectRef) {
    throw new ContextPinActionError(`No open/download action is registered for "${contextLabel(context, '')}".`)
  }
  const filename = contextLabel(context, objectRef)
  const mime = contextMime(context) || undefined
  let capabilities: ObjectActionResponse
  try {
    capabilities = await resolveObjectAction(runtime, {
      action: 'capabilities',
      objectRef,
      filename,
      mime,
    })
  } catch (error) {
    if (isResolverUnavailable(error)) {
      throw new ContextPinActionError(`No open/download action is registered for "${filename}".`)
    }
    throw error
  }
  const action = chooseContextPinOpenEffectAction(capabilities)
  if (!action) {
    throw new ContextPinActionError(`No open/download action is available for "${filename}".`)
  }
  const response = await resolveObjectAction(runtime, {
    action,
    objectRef,
    filename,
    mime,
  })
  if (action === 'download') {
    downloadObjectActionResult(response, filename, mime)
    return
  }
  if (action === 'open') {
    emitter.emit('object-open', {
      ref: {
        object_ref: objectRef,
        response: response as Record<string, unknown>,
        source: sourceForContext(context, objectRef),
      },
    })
  }
}
