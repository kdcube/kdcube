/**
 * HTTP client for the chat engine — conversation list/fetch/status, feedback,
 * delete, message submit (with attachments), context preview, object actions,
 * downloads.
 *
 * Ported from the widget's `api/client.ts`. The `settings` singleton is replaced
 * by an explicit `EngineRuntime` first argument; behaviour is otherwise verbatim.
 */
import type { EngineRuntime } from '../runtime.ts'
import type {
  AgentCachePolicy,
  AgentCapabilitiesInventory,
  AgentModelPick,
  AgentSelectionApplyMode,
  AgentSelectionDisabled,
  AgentSelectionPatch,
  AgentSelectionPending,
} from '../capabilities.ts'
import { buildRequestHeaders, downloadBlobAsFile, requireScope } from './http.ts'
import type {
  ConversationDTO,
  ConversationListResponse,
  ConversationSummary,
  ObjectActionResponse,
  ReactContextPreviewParams,
  ReactContextPreviewResponse,
  ResolveObjectActionParams,
  SubmitChatMessageApiResponse,
  SubmitChatMessageParams,
  SubmitChatMessageResponse,
  TurnReaction,
} from '../protocol.ts'

export class ObjectActionRequestError extends Error {
  readonly action: string
  readonly objectRef: string
  readonly code?: string
  readonly status?: number

  constructor({
    action,
    objectRef,
    detail,
    code,
    status,
  }: {
    action: string
    objectRef: string
    detail: string
    code?: string
    status?: number
  }) {
    super(`Object action ${action} failed for ${objectRef}: ${detail}`)
    this.name = 'ObjectActionRequestError'
    this.action = action
    this.objectRef = objectRef
    this.code = code
    this.status = status
  }
}

export function buildEventSubmission(params: SubmitChatMessageParams, tenant: string, project: string): Record<string, unknown> {
  const events = params.externalEvents || []
  if (!events.length) {
    throw new Error('submitChatMessage requires a prebuilt external_events[] batch')
  }
  return {
    external_events: events,
    chat_history: params.chatHistory,
    project,
    tenant,
    bundle_id: params.bundleId,
    ...(params.turnId ? { turn_id: params.turnId } : {}),
    ...(params.conversationId ? { conversation_id: params.conversationId } : {}),
    ...(params.activeTurnId ? { active_turn_id: params.activeTurnId } : {}),
    ...(params.targetTurnId ? { target_turn_id: params.targetTurnId } : {}),
    ...(params.target ? { target: params.target } : {}),
    ...(params.payload ? { payload: params.payload } : {}),
  }
}

function operationsUrl(runtime: EngineRuntime, alias: string, bundleId: string, tenant: string, project: string): string {
  return (
    `${runtime.baseUrl}/api/integrations/bundles/` +
    `${encodeURIComponent(tenant)}/${encodeURIComponent(project)}/` +
    `${encodeURIComponent(bundleId)}/operations/${alias}`
  )
}

export async function listBundleConversations(runtime: EngineRuntime, bundleId: string): Promise<ConversationSummary[]> {
  const { tenant, project } = requireScope(runtime)
  const params = new URLSearchParams()
  params.set('bundle_id', bundleId)

  const response = await fetch(
    `${runtime.baseUrl}/api/cb/conversations/${tenant}/${project}?${params.toString()}`,
    {
      method: 'GET',
      credentials: runtime.credentials,
      headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
    },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to load conversations (${response.status}): ${detail}`)
  }

  const data = (await response.json()) as ConversationListResponse
  return (data.items || []).map((item) => ({
    id: item.conversation_id,
    title: item.title || null,
    startedAt: item.started_at ? Date.parse(item.started_at) : null,
    lastActivityAt: item.last_activity_at ? Date.parse(item.last_activity_at) : null,
  }))
}

export async function fetchConversationById(runtime: EngineRuntime, conversationId: string): Promise<ConversationDTO> {
  const { tenant, project } = requireScope(runtime)
  const response = await fetch(
    `${runtime.baseUrl}/api/cb/conversations/${tenant}/${project}/${conversationId}/fetch`,
    {
      method: 'POST',
      credentials: runtime.credentials,
      headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ materialize: true }),
    },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to fetch conversation (${response.status}): ${detail}`)
  }

  return response.json()
}

export async function requestConversationStatus(runtime: EngineRuntime, conversationId: string, streamId: string): Promise<void> {
  await fetch(`${runtime.baseUrl}/sse/conv_status.get`, {
    method: 'POST',
    credentials: runtime.credentials,
    headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ conversation_id: conversationId, stream_id: streamId }),
  })
}

/** Submit, update, or clear the signed-in user's reaction to one assistant turn.
 *  `reaction: null` clears existing feedback; `text` is an optional note. */
export async function submitTurnFeedback(
  runtime: EngineRuntime,
  conversationId: string,
  turnId: string,
  reaction: TurnReaction | null,
  text?: string,
): Promise<void> {
  const { tenant, project } = requireScope(runtime)
  const body: Record<string, unknown> = { reaction }
  if (reaction && text) body.text = text
  const response = await fetch(
    `${runtime.baseUrl}/api/cb/conversations/${tenant}/${project}/${conversationId}/turns/${turnId}/feedback`,
    {
      method: 'POST',
      credentials: runtime.credentials,
      headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to save feedback (${response.status}): ${detail}`)
  }
}

/** Pull `{ reaction, origin }` out of one raw reaction artifact, probing the
 *  plausible payload depths defensively. */
function extractReaction(item: Record<string, unknown>): { reaction?: string; origin?: string } {
  const get = (obj: unknown, key: string): unknown =>
    obj && typeof obj === 'object' ? (obj as Record<string, unknown>)[key] : undefined
  const meta = get(item, 'meta')
  const candidates = [
    get(get(item, 'payload'), 'reaction'),
    get(get(get(item, 'data'), 'payload'), 'reaction'),
    get(item, 'payload'),
    item,
  ]
  for (const candidate of candidates) {
    const reaction = get(candidate, 'reaction')
    if (typeof reaction === 'string') {
      const origin = get(candidate, 'origin') ?? get(meta, 'origin')
      return { reaction, origin: typeof origin === 'string' ? origin : undefined }
    }
  }
  return {}
}

/** The signed-in user's reaction for a turn — the explicit `origin: 'user'`
 *  reaction only. At most one user reaction exists per turn. */
function pickUserReaction(items: Array<Record<string, unknown>>): TurnReaction | null {
  for (const item of items) {
    const { reaction, origin } = extractReaction(item)
    if (origin === 'user' && (reaction === 'ok' || reaction === 'not_ok' || reaction === 'neutral')) {
      return reaction
    }
  }
  return null
}

/** Hydrate the signed-in user's saved reactions for a conversation, keyed by
 *  turn id. Best-effort: returns an empty map on any failure. */
export async function fetchTurnFeedbacks(
  runtime: EngineRuntime,
  conversationId: string,
): Promise<Record<string, TurnReaction>> {
  const { tenant, project } = requireScope(runtime)
  try {
    const response = await fetch(
      `${runtime.baseUrl}/api/cb/conversations/${tenant}/${project}/${conversationId}/turns-with-feedbacks`,
      {
        method: 'POST',
        credentials: runtime.credentials,
        headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
        body: JSON.stringify({}),
      },
    )
    if (!response.ok) return {}
    const data = (await response.json()) as {
      turns?: Array<{
        turn_id?: string
        reactions?: Array<Record<string, unknown>>
      }>
    }
    const out: Record<string, TurnReaction> = {}
    for (const turn of data.turns || []) {
      const turnId = turn.turn_id
      if (!turnId) continue
      const value = pickUserReaction(turn.reactions || [])
      if (value) out[turnId] = value
    }
    return out
  } catch {
    return {}
  }
}

/** Hard-delete a conversation (and related artifacts). Irreversible — callers
 *  should confirm with the user before invoking. */
export async function deleteConversationById(runtime: EngineRuntime, conversationId: string): Promise<void> {
  const { tenant, project } = requireScope(runtime)
  const response = await fetch(
    `${runtime.baseUrl}/api/cb/conversations/${tenant}/${project}/${conversationId}`,
    {
      method: 'DELETE',
      credentials: runtime.credentials,
      headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
    },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to delete conversation (${response.status}): ${detail}`)
  }
}

async function parseSubmitChatMessageResponse(
  response: Response,
  fallbackConversationId?: string | null,
): Promise<SubmitChatMessageResponse> {
  const raw = (await response.json().catch(() => null)) as SubmitChatMessageApiResponse | null
  const conversationId = raw?.conversation_id || fallbackConversationId || ''
  if (!conversationId) {
    throw new Error('sse/chat response did not include a conversation_id')
  }

  return {
    status: raw?.status,
    taskId: raw?.task_id,
    sessionId: raw?.session_id,
    conversationId,
    turnId: raw?.turn_id,
    conversationCreated: Boolean(raw?.conversation_created),
    userType: raw?.user_type,
    isContinuation: Boolean(raw?.is_continuation),
    activeTurnId: raw?.active_turn_id,
    targetTurnId: raw?.target_turn_id,
    queuedTurnId: raw?.queued_turn_id,
    eventId: raw?.event_id,
    externalEventSequence: raw?.external_event_sequence,
    liveOwnerDetected: raw?.live_owner_detected,
    message: raw?.message,
  }
}

export async function submitChatMessage(runtime: EngineRuntime, params: SubmitChatMessageParams): Promise<SubmitChatMessageResponse> {
  const { tenant, project } = requireScope(runtime)
  const eventSubmission = buildEventSubmission(params, tenant, project)

  const url = new URL(`${runtime.baseUrl}/sse/chat`)
  url.searchParams.set('stream_id', params.streamId)

  let response: Response
  if (params.files.length > 0) {
    const form = new FormData()
    form.set('event_submission', JSON.stringify(eventSubmission))
    params.files.forEach((file) => form.append('files', file, file.name))
    response = await fetch(url, {
      method: 'POST',
      credentials: runtime.credentials,
      headers: await buildRequestHeaders(runtime),
      body: form,
    })
  } else {
    response = await fetch(url, {
      method: 'POST',
      credentials: runtime.credentials,
      headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(eventSubmission),
    })
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`sse/chat failed (${response.status}) ${detail}`)
  }

  return parseSubmitChatMessageResponse(response, params.conversationId)
}

export async function previewReactContext(runtime: EngineRuntime, params: ReactContextPreviewParams): Promise<ReactContextPreviewResponse> {
  const { tenant, project } = requireScope(runtime)
  if (!params.externalEvents.length) {
    return {
      ok: false,
      error: 'No external events to preview.',
      status: 400,
    }
  }
  const response = await fetch(operationsUrl(runtime, 'react_context_preview', params.bundleId, tenant, project), {
    method: 'POST',
    credentials: runtime.credentials,
    headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json', Accept: 'application/json' }),
    body: JSON.stringify({
      data: {
        external_events: params.externalEvents,
        ...(params.conversationId ? { conversation_id: params.conversationId } : {}),
        ...(params.turnId ? { turn_id: params.turnId } : {}),
        ...(params.target ? { target: params.target } : {}),
      },
    }),
  })
  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object'
      ? String((payload as Record<string, unknown>).detail || (payload as Record<string, unknown>).error || response.statusText)
      : response.statusText
    throw new Error(`react_context_preview failed (${response.status}): ${detail}`)
  }
  const body = payload && typeof payload === 'object' && 'react_context_preview' in payload
    ? (payload as Record<string, unknown>).react_context_preview
    : payload
  return (body && typeof body === 'object' ? body : { ok: false, error: 'Invalid preview response.' }) as ReactContextPreviewResponse
}

function unwrapOperationBody(payload: unknown, alias: string): unknown {
  return payload && typeof payload === 'object' && alias in (payload as Record<string, unknown>)
    ? (payload as Record<string, unknown>)[alias]
    : payload
}

export interface AgentCapabilitiesResponse {
  ok: boolean
  agent: string
  capabilities: AgentCapabilitiesInventory
  selection: {
    schema_version?: number
    disabled?: AgentSelectionDisabled
    model?: AgentModelPick | null
    cache_policy?: Record<string, string>
    pending?: AgentSelectionPending | null
  }
  cache_policy?: AgentCachePolicy
  error?: string
  message?: string
}

export interface AgentSelectionUpdateResponse {
  ok: boolean
  agent: string
  selection: {
    schema_version?: number
    disabled?: AgentSelectionDisabled
    model?: AgentModelPick | null
    cache_policy?: Record<string, string>
    pending?: AgentSelectionPending | null
    updated_at?: string
  }
  error?: string
  message?: string
}

export interface AgentSelectionWriteOptions {
  /** Cold-cache choice for this write. Default 'now'; deferred modes park the
   *  change as a pending delta the runtime promotes on its trigger. */
  apply?: AgentSelectionApplyMode
  /** Anchors the next-conversation trigger. */
  conversationId?: string | null
  /** Persist the user's standing per-class policy alongside. */
  cachePolicy?: Record<string, string>
}

/** The agent's configured inventory + the caller's saved selection (deny-list). */
export async function fetchAgentCapabilities(
  runtime: EngineRuntime,
  agentId: string,
): Promise<AgentCapabilitiesResponse> {
  const { tenant, project } = requireScope(runtime)
  const response = await fetch(operationsUrl(runtime, 'agent_capabilities', runtime.bundleId, tenant, project), {
    method: 'POST',
    credentials: runtime.credentials,
    headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json', Accept: 'application/json' }),
    body: JSON.stringify({ data: { agent: agentId } }),
  })
  const payload = await response.json().catch(() => null)
  const body = unwrapOperationBody(payload, 'agent_capabilities') as AgentCapabilitiesResponse | null
  if (!response.ok || !body || body.ok === false) {
    const detail = body && typeof body === 'object'
      ? String(body.message || body.error || response.statusText)
      : response.statusText
    throw new Error(`agent_capabilities failed (${response.status}): ${detail}`)
  }
  return body
}

/** Merge-write partial selection toggles (deny-list; clamped server-side).
 *  A `model` key in the patch rides the body as its own field (a PICK, not a
 *  denial): `{provider, model}` sets, `null` clears; omitted keeps. */
export async function submitAgentSelectionUpdate(
  runtime: EngineRuntime,
  agentId: string,
  patch: AgentSelectionPatch,
  options: AgentSelectionWriteOptions = {},
): Promise<AgentSelectionUpdateResponse> {
  const { tenant, project } = requireScope(runtime)
  const { model, ...disabled } = patch
  const apply = options.apply && options.apply !== 'now' ? options.apply : undefined
  const response = await fetch(operationsUrl(runtime, 'agent_selection_update', runtime.bundleId, tenant, project), {
    method: 'POST',
    credentials: runtime.credentials,
    headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json', Accept: 'application/json' }),
    body: JSON.stringify({
      data: {
        agent: agentId,
        disabled,
        ...(model !== undefined ? { model } : {}),
        ...(apply ? { apply, conversation_id: options.conversationId || '' } : {}),
        ...(options.cachePolicy ? { cache_policy: options.cachePolicy } : {}),
      },
    }),
  })
  const payload = await response.json().catch(() => null)
  const body = unwrapOperationBody(payload, 'agent_selection_update') as AgentSelectionUpdateResponse | null
  if (!response.ok || !body || body.ok === false) {
    const detail = body && typeof body === 'object'
      ? String(body.message || body.error || response.statusText)
      : response.statusText
    throw new Error(`agent_selection_update failed (${response.status}): ${detail}`)
  }
  return body
}

function base64ToBlob(value: string, mime: string): Blob {
  const binary = window.atob(value)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return new Blob([bytes], { type: mime || 'application/octet-stream' })
}

function downloadUrlAsFile(runtime: EngineRuntime | undefined, downloadUrl: string, filename: string): void {
  const href = runtime ? resolveDownloadUrl(runtime, downloadUrl) : downloadUrl
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

function resolveDownloadUrl(runtime: EngineRuntime, downloadUrl: string): string {
  if (/^https?:\/\//i.test(downloadUrl)) return downloadUrl
  return new URL(downloadUrl, runtime.baseUrl).toString()
}

function sameOriginAsRuntime(runtime: EngineRuntime, href: string): boolean {
  try {
    return new URL(href).origin === new URL(runtime.baseUrl).origin
  } catch {
    return false
  }
}

async function fetchDownloadUrlAsBlob(runtime: EngineRuntime, downloadUrl: string): Promise<Blob> {
  const href = resolveDownloadUrl(runtime, downloadUrl)
  const sameOrigin = sameOriginAsRuntime(runtime, href)
  const response = await fetch(href, {
    credentials: sameOrigin ? runtime.credentials : 'omit',
    headers: sameOrigin ? await buildRequestHeaders(runtime) : undefined,
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to fetch downloadable content (${response.status}): ${detail}`)
  }
  return response.blob()
}

export async function resolveObjectAction(runtime: EngineRuntime, params: ResolveObjectActionParams): Promise<ObjectActionResponse> {
  const { tenant, project } = requireScope(runtime)
  const response = await fetch(operationsUrl(runtime, 'canvas_object_action', runtime.bundleId, tenant, project), {
    method: 'POST',
    credentials: runtime.credentials,
    headers: await buildRequestHeaders(runtime, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      data: {
        ...(params.payload || {}),
        action: params.action,
        object_ref: params.objectRef,
        mime: params.mime || undefined,
        filename: params.filename || undefined,
      },
    }),
  })
  const payload = await response.json().catch(() => null) as Record<string, unknown> | null
  const body = payload && typeof payload === 'object' && 'canvas_object_action' in payload
    ? payload.canvas_object_action as ObjectActionResponse
    : payload
  if (!response.ok || !body || body.ok === false) {
    const code = body && typeof body === 'object'
      ? String(body.error || body.code || '')
      : ''
    const detail = body && typeof body === 'object'
      ? String(body.message || body.error || response.statusText)
      : response.statusText
    const status = body && typeof body.status === 'number' ? body.status : response.status
    throw new ObjectActionRequestError({
      action: params.action,
      objectRef: params.objectRef,
      detail,
      code: code || undefined,
      status,
    })
  }
  return body as ObjectActionResponse
}

export function downloadObjectActionResult(
  body: ObjectActionResponse,
  fallbackFilename: string,
  fallbackMime?: string | null,
  runtime?: EngineRuntime,
): void {
  const downloadUrl = typeof body.download_url === 'string' ? body.download_url.trim() : ''
  const resolvedFilename = typeof body.filename === 'string' && body.filename ? body.filename : fallbackFilename
  if (downloadUrl) {
    downloadUrlAsFile(runtime, downloadUrl, resolvedFilename)
    return
  }
  const contentBase64 = typeof body.content_base64 === 'string' ? body.content_base64 : ''
  if (!contentBase64) {
    throw new Error('Resolver did not return downloadable content.')
  }
  const resolvedMime = typeof body.mime === 'string' && body.mime ? body.mime : fallbackMime || 'application/octet-stream'
  downloadBlobAsFile(base64ToBlob(contentBase64, resolvedMime), resolvedFilename)
}

export async function objectActionResultBlob(
  body: ObjectActionResponse,
  fallbackMime?: string | null,
  runtime?: EngineRuntime,
): Promise<Blob> {
  const downloadUrl = typeof body.download_url === 'string' ? body.download_url.trim() : ''
  if (downloadUrl && runtime) {
    return fetchDownloadUrlAsBlob(runtime, downloadUrl)
  }
  const contentBase64 = typeof body.content_base64 === 'string' ? body.content_base64 : ''
  if (!contentBase64) {
    throw new Error('Resolver did not return downloadable content.')
  }
  const resolvedMime = typeof body.mime === 'string' && body.mime ? body.mime : fallbackMime || 'application/octet-stream'
  return base64ToBlob(contentBase64, resolvedMime)
}

export async function downloadObjectRef(runtime: EngineRuntime, objectRef: string, filename: string, mime?: string | null): Promise<void> {
  const body = await resolveObjectAction(runtime, {
    action: 'download',
    objectRef,
    filename,
    mime,
  })
  try {
    downloadObjectActionResult(body, filename, mime, runtime)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(`Could not download ${objectRef}: ${message}`)
  }
}

export async function fetchObjectRefBlob(runtime: EngineRuntime, objectRef: string, filename?: string | null, mime?: string | null): Promise<Blob> {
  const body = await resolveObjectAction(runtime, {
    action: 'download',
    objectRef,
    filename,
    mime,
  })
  try {
    return await objectActionResultBlob(body, mime, runtime)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(`Could not read ${objectRef}: ${message}`)
  }
}
