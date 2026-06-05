/**
 * HTTP-only API client for the versatile main chat UI.
 *
 * Conversation list, conversation fetch-by-id, status request,
 * `submitChatMessage` (with optional multipart attachments), resource
 * downloads. All `fetch` calls go through `buildRequestHeaders` from
 * `./transport.ts` so auth + timezone headers are set consistently.
 *
 * Moved verbatim from src/service.ts (Wave 4).
 */

import { settings } from '../settings.ts'
import {
  buildRequestHeaders,
  downloadBlobAsFile,
  requireScope,
  resolveAbsoluteUrl,
} from './transport.ts'
import type {
  ConversationDTO,
  ConversationSummary,
  SubmitChatMessageParams,
  SubmitChatMessageResponse,
  TurnReaction,
} from './types.ts'

interface ConversationListResponse {
  items?: Array<{
    conversation_id: string
    last_activity_at?: string | null
    started_at?: string | null
    title?: string | null
  }>
}

interface SubmitChatMessageApiResponse {
  status?: string
  task_id?: string
  session_id?: string
  conversation_id?: string
  turn_id?: string
  conversation_created?: boolean
  user_type?: string
  is_continuation?: boolean | null
  active_turn_id?: string | null
  target_turn_id?: string | null
  queued_turn_id?: string | null
  event_id?: string | null
  external_event_sequence?: number | null
  live_owner_detected?: boolean | null
  message?: string
}

function newEventId(): string {
  return crypto.randomUUID ? crypto.randomUUID() : `evt_${Date.now()}_${Math.random().toString(16).slice(2)}`
}

export function buildEventSubmission(params: SubmitChatMessageParams, tenant: string, project: string): Record<string, unknown> {
  const reactiveEventType = params.reactiveEventType || 'event.user.prompt'
  const events: Record<string, unknown>[] = []
  const text = String(params.text || '').trim()
  const hasAuthoredEvents = Boolean(params.externalEvents?.length)
  if ((text || reactiveEventType === 'event.user.steer') && !hasAuthoredEvents) {
    const eventSourceId = reactiveEventType === 'event.user.steer'
      ? 'chat.steer'
      : reactiveEventType === 'event.user.followup'
        ? 'chat.followup'
        : 'chat.message'
    events.push({
      event_id: newEventId(),
      type: reactiveEventType,
      event_source_id: eventSourceId,
      reactive: true,
      agent_id: (params.target?.agent_id || params.target?.agent) as string | undefined,
      story_id: params.target?.story_id as string | undefined,
      payload: { mime: 'text/plain', event: { text } },
    })
  }
  params.files.forEach((file, index) => {
    events.push({
      event_id: newEventId(),
      type: 'event.user.attachment.file',
      event_source_id: 'chat.attachment',
      reactive: true,
      agent_id: (params.target?.agent_id || params.target?.agent) as string | undefined,
      story_id: params.target?.story_id as string | undefined,
      payload: {
        mime: file.type || 'application/octet-stream',
        event: {
          filename: file.name,
          size: file.size,
          mime: file.type || 'application/octet-stream',
          file_index: index,
        },
      },
    })
  })
  events.push(...(params.externalEvents || []))
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

interface ResourceByRnResponse {
  metadata?: {
    download_url?: string
  }
}

export async function listBundleConversations(bundleId: string): Promise<ConversationSummary[]> {
  const { tenant, project } = requireScope()
  const params = new URLSearchParams()
  params.set('bundle_id', bundleId)

  const response = await fetch(
    `${settings.getBaseUrl()}/api/cb/conversations/${tenant}/${project}?${params.toString()}`,
    {
      method: 'GET',
      credentials: 'include',
      headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
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

export async function fetchConversationById(conversationId: string): Promise<ConversationDTO> {
  const { tenant, project } = requireScope()
  const response = await fetch(
    `${settings.getBaseUrl()}/api/cb/conversations/${tenant}/${project}/${conversationId}/fetch`,
    {
      method: 'POST',
      credentials: 'include',
      headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ materialize: true }),
    },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to fetch conversation (${response.status}): ${detail}`)
  }

  return response.json()
}

export async function requestConversationStatus(conversationId: string, streamId: string): Promise<void> {
  await fetch(`${settings.getBaseUrl()}/sse/conv_status.get`, {
    method: 'POST',
    credentials: 'include',
    headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ conversation_id: conversationId, stream_id: streamId }),
  })
}

/** Submit, update, or clear the signed-in user's reaction to one assistant
 *  turn. `reaction: null` clears any existing feedback; `text` is an
 *  optional free-form note (only sent with a non-null reaction). Maps to
 *  POST /api/cb/conversations/{tenant}/{project}/{id}/turns/{turn}/feedback. */
export async function submitTurnFeedback(
  conversationId: string,
  turnId: string,
  reaction: TurnReaction | null,
  text?: string,
): Promise<void> {
  const { tenant, project } = requireScope()
  const body: Record<string, unknown> = { reaction }
  if (reaction && text) body.text = text
  const response = await fetch(
    `${settings.getBaseUrl()}/api/cb/conversations/${tenant}/${project}/${conversationId}/turns/${turnId}/feedback`,
    {
      method: 'POST',
      credentials: 'include',
      headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to save feedback (${response.status}): ${detail}`)
  }
}

/** Pull `{ reaction, origin }` out of one raw reaction artifact. The value
 *  is stored in the message payload as `{ reaction: { reaction, origin, … } }`,
 *  but recent()/materialize may surface the payload at slightly different
 *  depths, so probe the plausible shapes defensively. */
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
 *  reaction only (machine-inferred reactions never light the thumbs). At
 *  most one user reaction exists per turn (the server replaces it on write). */
function pickUserReaction(items: Array<Record<string, unknown>>): TurnReaction | null {
  for (const item of items) {
    const { reaction, origin } = extractReaction(item)
    if (origin === 'user' && (reaction === 'ok' || reaction === 'not_ok' || reaction === 'neutral')) {
      return reaction
    }
  }
  return null
}

/** Hydrate the signed-in user's saved reactions for a conversation, keyed
 *  by turn id. Maps to POST .../turns-with-feedbacks. Best-effort: returns
 *  an empty map on any failure so a hydration miss never blocks the load. */
export async function fetchTurnFeedbacks(
  conversationId: string,
): Promise<Record<string, TurnReaction>> {
  const { tenant, project } = requireScope()
  try {
    const response = await fetch(
      `${settings.getBaseUrl()}/api/cb/conversations/${tenant}/${project}/${conversationId}/turns-with-feedbacks`,
      {
        method: 'POST',
        credentials: 'include',
        headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({}),
      },
    )
    if (!response.ok) return {}
    const data = (await response.json()) as {
      turns?: Array<{
        turn_id?: string
        // Raw reaction artifacts; the reaction value lives in the
        // message payload: { reaction: { reaction, origin, text, ts } }.
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

/** Hard-delete a conversation (and related artifacts) for the authenticated
 *  user. Maps to `DELETE /api/cb/conversations/{tenant}/{project}/{id}`.
 *  Irreversible — callers should confirm with the user before invoking. */
export async function deleteConversationById(conversationId: string): Promise<void> {
  const { tenant, project } = requireScope()
  const response = await fetch(
    `${settings.getBaseUrl()}/api/cb/conversations/${tenant}/${project}/${conversationId}`,
    {
      method: 'DELETE',
      credentials: 'include',
      headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
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

export async function submitChatMessage(params: SubmitChatMessageParams): Promise<SubmitChatMessageResponse> {
  const { tenant, project } = requireScope()
  const eventSubmission = buildEventSubmission(params, tenant, project)

  const url = new URL(`${settings.getBaseUrl()}/sse/chat`)
  url.searchParams.set('stream_id', params.streamId)

  let response: Response
  if (params.files.length > 0) {
    const form = new FormData()
    form.set('event_submission', JSON.stringify(eventSubmission))
    params.files.forEach((file) => form.append('files', file, file.name))
    response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: buildRequestHeaders(),
      body: form,
    })
  } else {
    response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(eventSubmission),
    })
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`sse/chat failed (${response.status}) ${detail}`)
  }

  return parseSubmitChatMessageResponse(response, params.conversationId)
}

async function fetchResourceByRN(rn: string): Promise<ResourceByRnResponse> {
  const response = await fetch(`${settings.getBaseUrl()}/api/cb/resources/by-rn`, {
    method: 'POST',
    credentials: 'include',
    headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ rn }),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to resolve resource (${response.status}): ${detail}`)
  }

  return response.json()
}

export async function downloadResourceByRN(rn: string, filename: string): Promise<void> {
  const resource = await fetchResourceByRN(rn)
  const downloadUrl = resource.metadata?.download_url
  if (!downloadUrl) {
    throw new Error('Resource metadata did not include a download URL.')
  }

  const response = await fetch(resolveAbsoluteUrl(downloadUrl), {
    method: 'GET',
    credentials: 'include',
    headers: buildRequestHeaders(),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to download resource (${response.status}): ${detail}`)
  }

  downloadBlobAsFile(await response.blob(), filename)
}

export async function downloadHostedFile(path: string, filename: string): Promise<void> {
  const response = await fetch(resolveAbsoluteUrl(path), {
    method: 'GET',
    credentials: 'include',
    headers: buildRequestHeaders(),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Failed to download attachment (${response.status}): ${detail}`)
  }

  downloadBlobAsFile(await response.blob(), filename)
}
