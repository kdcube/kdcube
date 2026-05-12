import { createLocalId, getClientTimezone, makeAuthHeaders, settings } from './settings'

export type BannerTone = 'info' | 'warning' | 'error'
export type StepStatus = 'started' | 'running' | 'completed' | 'error' | 'skipped'

export interface ServiceInfo {
  request_id: string
  tenant?: string | null
  project?: string | null
  user?: string | null
}

export interface ConversationInfo {
  session_id: string
  conversation_id: string
  turn_id: string
}

export interface BaseEnvelope {
  type: string
  timestamp: string
  service: ServiceInfo
  conversation: ConversationInfo
  event: {
    agent?: string | null
    step: string
    status: StepStatus
    title?: string | null
    markdown?: string
  }
  data?: Record<string, unknown>
}

export interface ChatStartEnvelope extends BaseEnvelope {
  type: 'chat.start'
}

export interface ChatStepEnvelope extends BaseEnvelope {
  type: 'chat.step'
}

export interface ChatDeltaEnvelope extends BaseEnvelope {
  type: 'chat.delta'
  delta: {
    text: string
    marker: 'thinking' | 'answer' | 'canvas' | 'timeline_text' | 'subsystem' | string
    index: number
    completed?: boolean
  }
  extra: Record<string, unknown>
}

export interface ChatCompleteEnvelope extends BaseEnvelope {
  type: 'chat.complete'
  data: {
    final_answer?: string
    followups?: string[]
    error_message?: string
    [key: string]: unknown
  }
}

export interface ChatErrorEnvelope extends BaseEnvelope {
  type: 'chat.error'
  data: {
    error?: string
    [key: string]: unknown
  }
}

export interface ConvStatusEnvelope {
  type: 'conv.status'
  timestamp: string
  conversation: ConversationInfo
  data: {
    state: 'idle' | 'in_progress' | 'error'
    current_turn_id?: string | null
  }
}

export interface RateLimitPayload {
  retry_after_sec?: number | null
  reset_text?: string | null
  user_message?: string | null
  notification_type?: BannerTone | null
}

export interface ChatServiceEnvelope {
  type: string
  data?: {
    rate_limit?: RateLimitPayload
    user_message?: string
    notification_type?: BannerTone
    reason?: string
    has_personal_budget?: boolean
    usd_short?: number
    [key: string]: unknown
  }
}

export interface ConversationSummary {
  id: string
  title?: string | null
  startedAt?: number | null
  lastActivityAt?: number | null
}

interface ConversationListResponse {
  items?: Array<{
    conversation_id: string
    last_activity_at?: string | null
    started_at?: string | null
    title?: string | null
  }>
}

export interface ConversationArtifactDTO {
  type: string
  ts?: string
  data?: {
    text?: string
    payload?: Record<string, unknown>
    meta?: Record<string, unknown>
  }
}

export interface ConversationTurnDTO {
  turn_id: string
  artifacts: ConversationArtifactDTO[]
}

export interface ConversationDTO {
  conversation_id: string
  conversation_title?: string | null
  bundle_id?: string | null
  turns: ConversationTurnDTO[]
}

export interface ChatHistoryItem {
  role: 'user'
  content: string
  timestamp: string
  id: number
}

export interface OpenChatStreamOptions {
  sessionId?: string | null
  timeoutMs?: number
  onChatStart?: (payload: ChatStartEnvelope) => void
  onChatStep?: (payload: ChatStepEnvelope) => void
  onChatDelta?: (payload: ChatDeltaEnvelope) => void
  onChatComplete?: (payload: ChatCompleteEnvelope) => void
  onChatError?: (payload: ChatErrorEnvelope) => void
  onConversationStatus?: (payload: ConvStatusEnvelope) => void
  onChatService?: (payload: ChatServiceEnvelope) => void
  onDisconnect?: () => void
}

export interface OpenChatStreamResult {
  eventSource: EventSource
  sessionId: string
  streamId: string
}

export interface SubmitChatMessageParams {
  streamId: string
  bundleId: string
  conversationId?: string | null
  turnId: string
  text: string
  files: File[]
  chatHistory: ChatHistoryItem[]
}

interface SubmitChatMessageApiResponse {
  status?: string
  task_id?: string
  session_id?: string
  conversation_id?: string
  conversation_created?: boolean
  user_type?: string
  message_kind?: string | null
  message?: string
}

export interface SubmitChatMessageResponse {
  status?: string
  taskId?: string
  sessionId?: string
  conversationId: string
  conversationCreated: boolean
  userType?: string
  messageKind?: string | null
  message?: string
}

interface ResourceByRnResponse {
  metadata?: {
    download_url?: string
  }
}

function buildRequestHeaders(base?: HeadersInit): Headers {
  const headers = makeAuthHeaders(base)
  const tz = getClientTimezone()
  if (tz.tz) headers.set('X-User-Timezone', tz.tz)
  headers.set('X-User-UTC-Offset', String(tz.utcOffsetMin))
  return headers
}

function resolveAbsoluteUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  const base = settings.getBaseUrl()
  return `${base}${path.startsWith('/') ? path : `/${path}`}`
}

export function downloadBlobAsFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function requireScope(): { tenant: string; project: string } {
  const tenant = settings.getTenant()
  const project = settings.getProject()
  if (!tenant || !project) {
    throw new Error('Tenant/project is not configured for this bundle UI.')
  }
  return { tenant, project }
}

async function fetchProfileSessionId(sessionId?: string | null): Promise<string> {
  if (sessionId) return sessionId

  const response = await fetch(`${settings.getBaseUrl()}/profile`, {
    method: 'GET',
    credentials: 'include',
    headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`Unable to fetch profile (${response.status}): ${detail}`)
  }

  const data = (await response.json()) as { session_id?: string | null }
  if (!data.session_id) {
    throw new Error('Profile did not include a session id.')
  }
  return data.session_id
}

function addJsonListener<T>(
  eventSource: EventSource,
  eventName: string,
  handler?: (payload: T) => void,
): void {
  if (!handler) return

  eventSource.addEventListener(eventName, (event: MessageEvent) => {
    try {
      handler(JSON.parse(event.data) as T)
    } catch (error) {
      console.error('Malformed SSE event', eventName, error)
    }
  })
}

export async function openChatStream(options: OpenChatStreamOptions): Promise<OpenChatStreamResult> {
  const sessionId = await fetchProfileSessionId(options.sessionId)
  const streamId = createLocalId('stream')
  const timeoutMs = options.timeoutMs ?? 8000

  let eventSource: EventSource | null = null

  await new Promise<void>((resolve, reject) => {
    const url = new URL(`${settings.getBaseUrl()}/sse/stream`)
    url.searchParams.set('user_session_id', sessionId)
    url.searchParams.set('stream_id', streamId)
    if (settings.getTenant()) url.searchParams.set('tenant', settings.getTenant())
    if (settings.getProject()) url.searchParams.set('project', settings.getProject())
    if (settings.getAccessToken()) url.searchParams.set('bearer_token', settings.getAccessToken()!)
    if (settings.getIdToken()) url.searchParams.set('id_token', settings.getIdToken()!)

    eventSource = new EventSource(url.toString(), { withCredentials: true })

    addJsonListener(eventSource, 'chat_start', options.onChatStart)
    addJsonListener(eventSource, 'chat_step', options.onChatStep)
    addJsonListener(eventSource, 'chat_delta', options.onChatDelta)
    addJsonListener(eventSource, 'chat_complete', options.onChatComplete)
    addJsonListener(eventSource, 'chat_error', options.onChatError)
    addJsonListener(eventSource, 'conv_status', options.onConversationStatus)
    addJsonListener(eventSource, 'chat_service', options.onChatService)

    let opened = false
    const timeout = window.setTimeout(() => {
      if (!opened) {
        eventSource?.close()
        reject(new Error('Timed out connecting to the event stream.'))
      }
    }, timeoutMs)

    eventSource.addEventListener('open', () => {
      opened = true
      window.clearTimeout(timeout)
      resolve()
    })

    eventSource.addEventListener('error', () => {
      if (!opened) {
        window.clearTimeout(timeout)
        eventSource?.close()
        reject(new Error('Unable to open the event stream.'))
        return
      }
      options.onDisconnect?.()
    })
  })

  return {
    eventSource: eventSource!,
    sessionId,
    streamId,
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
    conversationCreated: Boolean(raw?.conversation_created),
    userType: raw?.user_type,
    messageKind: raw?.message_kind,
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
    turn_id: params.turnId,
    bundle_id: params.bundleId,
  }
  if (params.conversationId) {
    message.conversation_id = params.conversationId
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
