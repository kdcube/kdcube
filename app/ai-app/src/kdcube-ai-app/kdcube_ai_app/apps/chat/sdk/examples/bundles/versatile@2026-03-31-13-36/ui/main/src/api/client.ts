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
  message_kind?: string | null
  active_turn_id?: string | null
  target_turn_id?: string | null
  queued_turn_id?: string | null
  event_id?: string | null
  external_event_sequence?: number | null
  live_owner_detected?: boolean | null
  message?: string
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
    messageKind: raw?.message_kind,
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

  const message: Record<string, unknown> = {
    message: params.text,
    chat_history: params.chatHistory,
    project,
    tenant,
    bundle_id: params.bundleId,
  }
  if (params.turnId) {
    message.turn_id = params.turnId
  }
  if (params.conversationId) {
    message.conversation_id = params.conversationId
  }
  if (params.messageKind) {
    message.message_kind = params.messageKind
  }
  if (params.continuationKind) {
    message.continuation_kind = params.continuationKind
  }
  if (params.activeTurnId) {
    message.active_turn_id = params.activeTurnId
  }
  if (params.targetTurnId) {
    message.target_turn_id = params.targetTurnId
  }
  if (params.followup) {
    message.followup = true
  }
  if (params.steer) {
    message.steer = true
  }

  const payload = {
    message,
    attachment_meta: params.files.map((file) => ({ filename: file.name })),
  }

  const url = new URL(`${settings.getBaseUrl()}/sse/chat`)
  url.searchParams.set('stream_id', params.streamId)

  let response: Response
  if (params.files.length > 0) {
    const form = new FormData()
    form.set('message', JSON.stringify(payload))
    form.set('attachment_meta', JSON.stringify(params.files.map((file) => ({ filename: file.name }))))
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
      body: JSON.stringify(payload),
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
