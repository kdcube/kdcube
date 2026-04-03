import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import {
  BUILT_BUNDLE_ID,
  createLocalId,
  getClientTimezone,
  makeAuthHeaders,
  settings,
} from './settings'

type ConnectionState = 'booting' | 'connecting' | 'connected' | 'disconnected'
type BannerTone = 'info' | 'warning' | 'error'
type TurnState = 'pending' | 'running' | 'completed' | 'error'
type StepStatus = 'started' | 'running' | 'completed' | 'error' | 'skipped'
type TurnTab = 'overview' | 'timeline' | 'steps' | 'downloads'

interface ServiceInfo {
  request_id: string
  tenant?: string | null
  project?: string | null
  user?: string | null
}

interface ConversationInfo {
  session_id: string
  conversation_id: string
  turn_id: string
}

interface BaseEnvelope {
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

interface ChatStartEnvelope extends BaseEnvelope {
  type: 'chat.start'
}

interface ChatStepEnvelope extends BaseEnvelope {
  type: 'chat.step'
}

interface ChatDeltaEnvelope extends BaseEnvelope {
  type: 'chat.delta'
  delta: {
    text: string
    marker: 'thinking' | 'answer' | 'canvas' | 'timeline_text' | 'subsystem' | string
    index: number
    completed?: boolean
  }
  extra: Record<string, unknown>
}

interface ChatCompleteEnvelope extends BaseEnvelope {
  type: 'chat.complete'
  data: {
    final_answer?: string
    followups?: string[]
    error_message?: string
    [key: string]: unknown
  }
}

interface ChatErrorEnvelope extends BaseEnvelope {
  type: 'chat.error'
  data: {
    error?: string
    [key: string]: unknown
  }
}

interface ConvStatusEnvelope {
  type: 'conv.status'
  timestamp: string
  conversation: ConversationInfo
  data: {
    state: 'idle' | 'in_progress' | 'error'
    current_turn_id?: string | null
  }
}

interface RateLimitPayload {
  retry_after_sec?: number | null
  reset_text?: string | null
  user_message?: string | null
  notification_type?: BannerTone | null
}

interface ChatServiceEnvelope {
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

interface ConversationSummary {
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

interface ConversationArtifactDTO {
  type: string
  ts?: string
  data?: {
    text?: string
    payload?: Record<string, unknown>
    meta?: Record<string, unknown>
  }
}

interface ConversationTurnDTO {
  turn_id: string
  artifacts: ConversationArtifactDTO[]
}

interface ConversationDTO {
  conversation_id: string
  conversation_title?: string | null
  bundle_id?: string | null
  turns: ConversationTurnDTO[]
}

interface Banner {
  id: string
  tone: BannerTone
  text: string
}

interface TurnStep {
  step: string
  title?: string | null
  status: StepStatus
  timestamp: number
  error?: string
  markdown?: string
  agent?: string | null
  data?: Record<string, unknown>
}

interface LinkArtifact {
  kind: 'citation'
  timestamp: number
  url: string
  title?: string | null
  body?: string | null
  favicon?: string | null
}

interface FileArtifact {
  kind: 'file'
  timestamp: number
  filename: string
  rn: string
  mime?: string | null
  description?: string | null
}

interface TimelineArtifact {
  kind: 'timeline'
  timestamp: number
  name: string
  markdown: string
}

interface CanvasArtifact {
  kind: 'canvas'
  timestamp: number
  name: string
  title?: string | null
  format?: string | null
  content: string
}

interface WebSearchItem {
  url: string
  title?: string | null
  body?: string | null
  favicon?: string | null
  provider?: string
  weightedScore?: number
}

interface WebSearchArtifact {
  kind: 'web_search'
  timestamp: number
  searchId: string
  name: string
  title?: string | null
  objective?: string
  queries: string[]
  items: WebSearchItem[]
  reportContent?: string | null
}

interface WebFetchItem {
  url: string
  status?: 'success' | 'timeout' | 'paywall' | 'error'
  mime?: string
  favicon?: string
  content_length?: number
  published_time_iso?: string
  modified_time_iso?: string
}

interface WebFetchArtifact {
  kind: 'web_fetch'
  timestamp: number
  executionId: string
  name: string
  title?: string | null
  items: WebFetchItem[]
}

interface CodeExecContractItem {
  filename: string
  description?: string | null
  mime?: string | null
}

interface CodeExecStatus {
  status?: 'gen' | 'exec' | 'done' | 'error'
  error?: Record<string, string>
}

interface CodeExecArtifact {
  kind: 'code_exec'
  timestamp: number
  executionId: string
  name?: string
  title?: string | null
  objective?: string
  language?: string
  program?: string
  contract?: CodeExecContractItem[]
  status?: CodeExecStatus
}

interface ServiceErrorArtifact {
  kind: 'service_error'
  timestamp: number
  message: string
}

interface TurnAttachment {
  id: string
  name: string
  size?: number | null
  mime?: string | null
  rn?: string | null
  hostedUri?: string | null
  description?: string | null
  file?: File
}

type TimelineEntryKind = 'lifecycle' | 'answer' | 'thinking' | 'timeline' | 'canvas' | 'subsystem' | 'error'
type TimelineEntryFormat = 'markdown' | 'text' | 'json' | 'code'

interface TimelineEntry {
  id: string
  timestamp: number
  kind: TimelineEntryKind
  title: string
  body?: string
  format?: TimelineEntryFormat
  agent?: string | null
  status?: string | null
}

type Artifact =
  | LinkArtifact
  | FileArtifact
  | TimelineArtifact
  | CanvasArtifact
  | WebSearchArtifact
  | WebFetchArtifact
  | CodeExecArtifact
  | ServiceErrorArtifact

interface ChatTurn {
  id: string
  state: TurnState
  createdAt: number
  userMessage: string
  userAttachments: TurnAttachment[]
  answer: string
  error?: string | null
  steps: Record<string, TurnStep>
  artifacts: Artifact[]
  timeline: TimelineEntry[]
  followups: string[]
}

interface ChatState {
  connection: ConnectionState
  sessionId: string | null
  conversationId: string | null
  conversationTitle: string | null
  composerText: string
  composerFiles: File[]
  turns: ChatTurn[]
  banners: Banner[]
  inputLocked: boolean
  inputLockMessage: string | null
  conversations: ConversationSummary[]
  conversationsLoading: boolean
  conversationsError: string | null
  conversationLoadingId: string | null
}

const initialState: ChatState = {
  connection: 'booting',
  sessionId: null,
  conversationId: null,
  conversationTitle: null,
  composerText: '',
  composerFiles: [],
  turns: [],
  banners: [],
  inputLocked: false,
  inputLockMessage: null,
  conversations: [],
  conversationsLoading: false,
  conversationsError: null,
  conversationLoadingId: null,
}

const markdownPlugins = [remarkGfm, remarkBreaks]

function timestampValue(value?: string): number {
  const parsed = value ? Date.parse(value) : NaN
  return Number.isFinite(parsed) ? parsed : Date.now()
}

function formatTime(value: number): string {
  return new Date(value).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatConversationTime(value?: number | null): string {
  if (!value || !Number.isFinite(value)) return 'No activity yet'
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size >= 10 || index === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`
}

function toneClass(tone: BannerTone): string {
  switch (tone) {
    case 'error':
      return 'border-[rgba(165,63,50,0.22)] bg-[rgba(165,63,50,0.08)] text-[var(--danger)]'
    case 'warning':
      return 'border-[rgba(164,103,33,0.22)] bg-[rgba(164,103,33,0.08)] text-[var(--warning)]'
    default:
      return 'border-[rgba(29,109,115,0.2)] bg-[rgba(29,109,115,0.09)] text-[var(--accent)]'
  }
}

function stepTone(status: StepStatus): string {
  switch (status) {
    case 'completed':
      return 'bg-[rgba(35,114,79,0.12)] text-[var(--success)]'
    case 'error':
      return 'bg-[rgba(165,63,50,0.12)] text-[var(--danger)]'
    case 'skipped':
      return 'bg-[rgba(94,107,120,0.12)] text-[var(--muted)]'
    default:
      return 'bg-[rgba(29,109,115,0.12)] text-[var(--accent)]'
  }
}

function closeStreamingMarkdown(text: string): string {
  const tripleBackticks = text.match(/```/g)?.length || 0
  const tripleTildes = text.match(/~~~/g)?.length || 0
  let next = text
  if (tripleBackticks % 2 === 1) next += '\n```'
  if (tripleTildes % 2 === 1) next += '\n~~~'
  return next
}

function safeJsonParse<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

function messageForError(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

function addBanner(state: ChatState, tone: BannerTone, text: string): ChatState {
  const trimmed = text.trim()
  if (!trimmed) return state
  if (state.banners.some((banner) => banner.text === trimmed && banner.tone === tone)) {
    return state
  }
  const banners = [{ id: createLocalId('banner'), tone, text: trimmed }, ...state.banners].slice(0, 4)
  return { ...state, banners }
}

function updateTurn(
  state: ChatState,
  turnId: string,
  updater: (turn: ChatTurn) => ChatTurn,
): ChatState {
  const index = state.turns.findIndex((turn) => turn.id === turnId)
  if (index < 0) return state
  const turns = state.turns.slice()
  turns[index] = updater(turns[index])
  return { ...state, turns }
}

function upsertArtifact<T extends Artifact>(
  artifacts: Artifact[],
  matcher: (artifact: Artifact) => boolean,
  next: T,
): Artifact[] {
  const index = artifacts.findIndex(matcher)
  if (index < 0) return [...artifacts, next]
  const copy = artifacts.slice()
  copy[index] = next
  return copy
}

function upsertTimelineEntry(
  entries: TimelineEntry[],
  matcher: (entry: TimelineEntry) => boolean,
  next: TimelineEntry,
): TimelineEntry[] {
  const index = entries.findIndex(matcher)
  if (index < 0) return [...entries, next]
  const copy = entries.slice()
  copy[index] = next
  return copy
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
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

function downloadBlobAsFile(blob: Blob, filename: string): void {
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

async function fetchResourceByRN(rn: string): Promise<{ metadata?: { download_url?: string } }> {
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

async function downloadResourceByRN(rn: string, filename: string): Promise<void> {
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

function buildChatHistory(turns: ChatTurn[]): Array<{ role: 'user'; content: string; timestamp: string; id: number }> {
  return turns.reduce<Array<{ role: 'user'; content: string; timestamp: string; id: number }>>((items, turn) => {
    if (!turn.userMessage.trim()) return items
    items.push({
      role: 'user',
      content: turn.userMessage,
      timestamp: new Date(turn.createdAt).toISOString(),
      id: turn.createdAt,
    })
    return items
  }, [])
}

function fallbackRateLimitMessage(rateLimit: RateLimitPayload | undefined, data: Record<string, unknown>): string {
  const retryAfterSec = rateLimit?.retry_after_sec ?? null
  const reason = typeof data.reason === 'string' ? data.reason : undefined
  if (retryAfterSec && retryAfterSec > 0) {
    const resetText =
      rateLimit?.reset_text ||
      (() => {
        const resetAt = new Date(Date.now() + retryAfterSec * 1000)
        const now = new Date()
        const timeStr = resetAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        const tomorrow = new Date(now)
        tomorrow.setDate(tomorrow.getDate() + 1)
        if (resetAt.toDateString() === now.toDateString()) return `today at ${timeStr}`
        if (resetAt.toDateString() === tomorrow.toDateString()) return `tomorrow at ${timeStr}`
        return `on ${resetAt.toLocaleDateString([], { month: 'long', day: 'numeric' })} at ${timeStr}`
      })()
    return `You've reached your usage limit. Your quota resets ${resetText}.`
  }
  if (reason === 'concurrency' || reason?.includes('concurrent')) {
    return 'Too many requests are running at once. Wait for one to complete and try again.'
  }
  if (reason === 'quota_lock_timeout') {
    return 'Too many requests are being processed right now. Please try again in a moment.'
  }
  if (reason?.includes('token')) {
    return "You've reached your token limit. Try again later or upgrade your plan."
  }
  if (reason?.includes('request')) {
    return "You've reached your request limit. Try again later or upgrade your plan."
  }
  return "You've reached your usage limit. Please try again later."
}

function timelineTitleForMarker(marker: string, fallbackName?: string | null): string {
  switch (marker) {
    case 'answer':
      return 'Assistant answer'
    case 'thinking':
      return 'Reasoning'
    case 'timeline_text':
      return fallbackName || 'Timeline update'
    case 'canvas':
      return fallbackName || 'Canvas update'
    default:
      return fallbackName || 'Stream update'
  }
}

function timelineTitleForSubsystem(subtype: string, fallbackName?: string | null): string {
  switch (subtype) {
    case 'web_search.filtered_results':
      return fallbackName || 'Web search results'
    case 'web_search.html_view':
      return fallbackName || 'Web search report'
    case 'web_fetch.results':
      return fallbackName || 'Web fetch results'
    case 'code_exec.code':
      return fallbackName || 'Exec code'
    case 'code_exec.program.name':
      return fallbackName || 'Exec program name'
    case 'code_exec.objective':
      return fallbackName || 'Exec objective'
    case 'code_exec.contract':
      return fallbackName || 'Exec contract'
    case 'code_exec.status':
      return fallbackName || 'Exec status'
    default:
      return fallbackName || subtype || 'Subsystem update'
  }
}

function extractPayload(record: ConversationArtifactDTO['data']): Record<string, unknown> {
  if (record?.payload && typeof record.payload === 'object') return record.payload
  if (record && typeof record === 'object') return record as Record<string, unknown>
  return {}
}

function normalizeTurnAttachment(
  payload: Record<string, unknown>,
  fallbackId: string,
  file?: File,
): TurnAttachment {
  const meta = payload.meta && typeof payload.meta === 'object' ? (payload.meta as Record<string, unknown>) : {}
  const name =
    (typeof payload.filename === 'string' && payload.filename) ||
    (typeof payload.name === 'string' && payload.name) ||
    (typeof meta.filename === 'string' && meta.filename) ||
    (typeof meta.name === 'string' && meta.name) ||
    file?.name ||
    'attachment'

  return {
    id: fallbackId,
    name,
    size:
      typeof payload.size === 'number'
        ? payload.size
        : typeof payload.size_bytes === 'number'
          ? payload.size_bytes
          : typeof meta.size === 'number'
            ? meta.size
            : typeof meta.size_bytes === 'number'
              ? meta.size_bytes
              : file?.size,
    mime:
      (typeof payload.mime === 'string' && payload.mime) ||
      (typeof payload.mime_type === 'string' && payload.mime_type) ||
      (typeof meta.mime === 'string' && meta.mime) ||
      file?.type ||
      null,
    rn:
      (typeof payload.rn === 'string' && payload.rn) ||
      (typeof meta.rn === 'string' && meta.rn) ||
      null,
    hostedUri:
      (typeof payload.hosted_uri === 'string' && payload.hosted_uri) ||
      (typeof payload.path === 'string' && payload.path) ||
      (typeof payload.source_path === 'string' && payload.source_path) ||
      null,
    description:
      (typeof payload.summary === 'string' && payload.summary) ||
      (typeof payload.description === 'string' && payload.description) ||
      null,
    file,
  }
}

function createEmptyTurn(turnId: string, createdAt: number, message = ''): ChatTurn {
  return {
    id: turnId,
    state: 'completed',
    createdAt,
    userMessage: message,
    userAttachments: [],
    answer: '',
    error: null,
    steps: {},
    artifacts: [],
    timeline: [],
    followups: [],
  }
}

function hydrateHistoricalConversation(conversation: ConversationDTO): ChatTurn[] {
  return (conversation.turns || []).map((turnDto, turnIndex) => {
    let turn = createEmptyTurn(turnDto.turn_id, Date.now())

    for (const artifact of turnDto.artifacts || []) {
      const ts = timestampValue(artifact.ts)
      const payload = extractPayload(artifact.data)

      switch (artifact.type) {
        case 'chat:user': {
          const text =
            (typeof artifact.data?.text === 'string' && artifact.data.text) ||
            (typeof payload.text === 'string' && payload.text) ||
            ''
          turn = {
            ...turn,
            createdAt: ts,
            userMessage: text,
          }
          break
        }
        case 'artifact:user.attachment': {
          turn = {
            ...turn,
            createdAt: Math.min(turn.createdAt, ts),
            userAttachments: [
              ...turn.userAttachments,
              normalizeTurnAttachment(payload, `stored:${turnDto.turn_id}:${turn.userAttachments.length}`),
            ],
          }
          break
        }
        case 'chat:assistant': {
          const text =
            (typeof artifact.data?.text === 'string' && artifact.data.text) ||
            (typeof payload.text === 'string' && payload.text) ||
            ''
          turn = {
            ...turn,
            answer: text,
            timeline: [
              ...turn.timeline,
              {
                id: `history:answer:${turnDto.turn_id}`,
                timestamp: ts,
                kind: 'answer',
                title: 'Assistant answer',
                body: text,
                format: 'markdown',
                status: 'completed',
              },
            ],
          }
          break
        }
        case 'artifact:assistant.file': {
          const normalized = normalizeTurnAttachment(payload, `assistant-file:${turnDto.turn_id}:${turn.artifacts.length}`)
          const fileArtifact: FileArtifact = {
            kind: 'file',
            timestamp: ts,
            filename: normalized.name,
            rn: normalized.rn || normalized.hostedUri || normalized.id,
            mime: normalized.mime,
            description: normalized.description,
          }
          turn = {
            ...turn,
            artifacts: upsertArtifact(
              turn.artifacts,
              (item) => item.kind === 'file' && item.rn === fileArtifact.rn,
              fileArtifact,
            ),
          }
          break
        }
        case 'artifact:conv.user_shortcuts': {
          const items = Array.isArray(payload.items)
            ? payload.items.filter((item): item is string => typeof item === 'string')
            : []
          turn = {
            ...turn,
            followups: items,
          }
          break
        }
        case 'artifact:solver.program.citables': {
          const items = Array.isArray(payload.items) ? payload.items : []
          let artifacts = turn.artifacts.slice()
          for (const item of items) {
            if (!item || typeof item !== 'object') continue
            const row = item as Record<string, unknown>
            const url = typeof row.url === 'string' ? row.url : ''
            if (!url) continue
            artifacts = upsertArtifact(artifacts, (artifactItem) => artifactItem.kind === 'citation' && artifactItem.url === url, {
              kind: 'citation',
              timestamp: ts,
              url,
              title: typeof row.title === 'string' ? row.title : null,
              body: typeof row.text === 'string' ? row.text : null,
              favicon: typeof row.favicon === 'string' ? row.favicon : null,
            })
          }
          turn = {
            ...turn,
            artifacts,
          }
          break
        }
        case 'artifact:conv.timeline_text.stream': {
          const items = Array.isArray(payload.items) ? payload.items : []
          let artifacts = turn.artifacts.slice()
          let timeline = turn.timeline.slice()
          for (const item of items) {
            if (!item || typeof item !== 'object') continue
            const row = item as Record<string, unknown>
            const name = typeof row.artifact_name === 'string' ? row.artifact_name : 'timeline'
            const text = typeof row.text === 'string' ? row.text : ''
            const itemTs = typeof row.ts_first === 'number' ? row.ts_first : ts
            const nextArtifact: TimelineArtifact = {
              kind: 'timeline',
              timestamp: itemTs,
              name,
              markdown: text,
            }
            artifacts = upsertArtifact(artifacts, (artifactItem) => artifactItem.kind === 'timeline' && artifactItem.name === name, nextArtifact)
            timeline = upsertTimelineEntry(timeline, (entry) => entry.id === `timeline:${name}`, {
              id: `timeline:${name}`,
              timestamp: itemTs,
              kind: 'timeline',
              title: name,
              body: text,
              format: 'markdown',
              status: 'completed',
            })
          }
          turn = {
            ...turn,
            artifacts,
            timeline,
          }
          break
        }
        case 'artifact:conv.thinking.stream': {
          const items = Array.isArray(payload.items) ? payload.items : []
          let timeline = turn.timeline.slice()
          for (const item of items) {
            if (!item || typeof item !== 'object') continue
            const row = item as Record<string, unknown>
            const agent = typeof row.agent === 'string' ? row.agent : 'assistant'
            const text = typeof row.text === 'string' ? row.text : ''
            const itemTs = typeof row.ts_first === 'number' ? row.ts_first : ts
            timeline = upsertTimelineEntry(timeline, (entry) => entry.id === `thinking:${agent}`, {
              id: `thinking:${agent}`,
              timestamp: itemTs,
              kind: 'thinking',
              title: `Reasoning • ${agent}`,
              body: text,
              format: 'markdown',
              agent,
              status: 'completed',
            })
          }
          turn = {
            ...turn,
            timeline,
          }
          break
        }
        case 'artifact:conv.artifacts.stream': {
          const items = Array.isArray(payload.items) ? payload.items : []
          let tempState: ChatState = {
            ...initialState,
            conversationId: conversation.conversation_id,
            conversationTitle: conversation.conversation_title || null,
            turns: [{ ...turn }],
          }
          items.forEach((item, index) => {
            if (!item || typeof item !== 'object') return
            const row = item as Record<string, unknown>
            const syntheticEnv: ChatDeltaEnvelope = {
              type: 'chat.delta',
              timestamp: typeof row.ts_first === 'number' ? new Date(row.ts_first).toISOString() : (artifact.ts || new Date(ts).toISOString()),
              service: { request_id: `history:${conversation.conversation_id}:${turnDto.turn_id}` },
              conversation: {
                session_id: '',
                conversation_id: conversation.conversation_id,
                turn_id: turnDto.turn_id,
              },
              event: {
                step: 'historical',
                status: 'completed',
                title: typeof row.title === 'string' ? row.title : null,
                agent: typeof row.agent === 'string' ? row.agent : null,
              },
              data: {},
              delta: {
                text: typeof row.text === 'string' ? row.text : '',
                marker: typeof row.marker === 'string' ? row.marker : 'subsystem',
                index,
                completed: true,
              },
              extra: {
                ...(row.extra && typeof row.extra === 'object' ? (row.extra as Record<string, unknown>) : {}),
                artifact_name: typeof row.artifact_name === 'string' ? row.artifact_name : undefined,
                title: typeof row.title === 'string' ? row.title : undefined,
                format: typeof row.format === 'string' ? row.format : undefined,
              },
            }
            tempState = applyChatDelta(tempState, syntheticEnv)
          })
          turn = tempState.turns[0] || turn
          break
        }
        default:
          break
      }
    }

    const sortedTimeline = turn.timeline.slice().sort((left, right) => left.timestamp - right.timestamp)
    return {
      ...turn,
      createdAt: Number.isFinite(turn.createdAt) ? turn.createdAt : Date.now() + turnIndex,
      state: 'completed',
      timeline: sortedTimeline,
    }
  }).sort((left, right) => left.createdAt - right.createdAt)
}

function applyChatStart(state: ChatState, env: ChatStartEnvelope): ChatState {
  return updateTurn(state, env.conversation.turn_id, (turn) => ({
    ...turn,
    state: 'running',
    timeline: [
      ...turn.timeline,
      {
        id: `lifecycle:start:${env.service.request_id}:${env.conversation.turn_id}`,
        timestamp: timestampValue(env.timestamp),
        kind: 'lifecycle',
        title: 'Turn started',
        body: typeof env.data?.message === 'string' ? env.data.message : undefined,
        format: 'text',
        status: 'started',
        agent: env.event.agent,
      },
    ],
  }))
}

function applyChatComplete(state: ChatState, env: ChatCompleteEnvelope): ChatState {
  return updateTurn(state, env.conversation.turn_id, (turn) => ({
    ...turn,
    state: env.data?.error_message ? 'error' : 'completed',
    answer: (env.data?.final_answer as string | undefined) || turn.answer,
    error: (env.data?.error_message as string | undefined) || turn.error,
    followups: Array.isArray(env.data?.followups) ? (env.data?.followups as string[]) : turn.followups,
    timeline: [
      ...turn.timeline,
      {
        id: `lifecycle:complete:${env.service.request_id}:${env.conversation.turn_id}`,
        timestamp: timestampValue(env.timestamp),
        kind: 'lifecycle',
        title: env.data?.error_message ? 'Turn completed with error' : 'Turn completed',
        body: typeof env.data?.selected_model === 'string' ? `Model: ${env.data.selected_model}` : undefined,
        format: 'text',
        status: env.data?.error_message ? 'error' : 'completed',
        agent: env.event.agent,
      },
    ],
  }))
}

function applyChatError(state: ChatState, env: ChatErrorEnvelope): ChatState {
  const message = env.data?.error || 'Request failed.'
  return updateTurn(state, env.conversation.turn_id, (turn) => ({
    ...turn,
    state: 'error',
    error: message,
    artifacts: [
      ...turn.artifacts,
      {
        kind: 'service_error',
        timestamp: timestampValue(env.timestamp),
        message,
      },
    ],
    timeline: [
      ...turn.timeline,
      {
        id: `error:${env.service.request_id}:${env.conversation.turn_id}:${turn.timeline.length}`,
        timestamp: timestampValue(env.timestamp),
        kind: 'error',
        title: 'Error',
        body: message,
        format: 'text',
        status: 'error',
        agent: env.event.agent,
      },
    ],
  }))
}

function applyConvStatus(state: ChatState, env: ConvStatusEnvelope): ChatState {
  if (!state.conversationId || env.conversation.conversation_id !== state.conversationId) {
    return state
  }
  if (env.data.state === 'in_progress') return state
  const turns = state.turns.map((turn) => {
    if (turn.state === 'pending' || turn.state === 'running') {
      return {
        ...turn,
        state: env.data.state === 'error' ? 'error' : 'completed',
        error:
          env.data.state === 'error' && turn.error == null
            ? 'Conversation ended with an error.'
            : turn.error,
      }
    }
    return turn
  })
  return { ...state, turns }
}

function applyChatStep(state: ChatState, env: ChatStepEnvelope): ChatState {
  return updateTurn(state, env.conversation.turn_id, (turn) => {
    const timestamp = timestampValue(env.timestamp)
    const nextStep: TurnStep = {
      step: env.event.step,
      title: env.event.title,
      status: env.event.status,
      timestamp: turn.steps[env.event.step]?.timestamp ?? timestamp,
      error: typeof env.data?.error === 'string' ? env.data.error : undefined,
      markdown: env.event.markdown,
      agent: env.event.agent,
      data: env.data,
    }

    let artifacts = turn.artifacts.slice()

    if (env.event.status === 'completed') {
      if (env.event.step === 'citations' && Array.isArray(env.data?.items)) {
        for (const item of env.data.items as Array<Record<string, unknown>>) {
          const url = typeof item.url === 'string' ? item.url : ''
          if (!url) continue
          artifacts = upsertArtifact(artifacts, (artifact) => artifact.kind === 'citation' && artifact.url === url, {
            kind: 'citation',
            timestamp,
            url,
            title: typeof item.title === 'string' ? item.title : null,
            body: typeof item.body === 'string' ? item.body : null,
            favicon: typeof item.favicon === 'string' ? item.favicon : null,
          })
        }
      }

      if (env.event.step === 'files' && Array.isArray(env.data?.items)) {
        for (const item of env.data.items as Array<Record<string, unknown>>) {
          const rn = typeof item.rn === 'string' ? item.rn : ''
          const filename = typeof item.filename === 'string' ? item.filename : ''
          if (!rn || !filename) continue
          artifacts = upsertArtifact(artifacts, (artifact) => artifact.kind === 'file' && artifact.rn === rn, {
            kind: 'file',
            timestamp,
            rn,
            filename,
            mime: typeof item.mime === 'string' ? item.mime : null,
            description: typeof item.description === 'string' ? item.description : null,
          })
        }
      }
    }

    return {
      ...turn,
      steps: {
        ...turn.steps,
        [env.event.step]: nextStep,
      },
      artifacts,
    }
  })
}

function applyChatDelta(state: ChatState, env: ChatDeltaEnvelope): ChatState {
  const turnId = env.conversation.turn_id
  const timestamp = timestampValue(env.timestamp)
  const marker = env.delta?.marker || 'answer'
  const textDelta = env.delta?.text || ''
  const index = env.delta?.index || 0

  return updateTurn(state, turnId, (turn) => {
    let nextTurn: ChatTurn = { ...turn }
    let artifacts = turn.artifacts.slice()
    let timeline = turn.timeline.slice()

    switch (marker) {
      case 'answer':
        nextTurn.answer = `${turn.answer}${textDelta}`
        nextTurn.state = 'running'
        timeline = upsertTimelineEntry(timeline, (entry) => entry.id === 'answer:assistant', {
          id: 'answer:assistant',
          timestamp: timeline.find((entry) => entry.id === 'answer:assistant')?.timestamp ?? timestamp,
          kind: 'answer',
          title: timelineTitleForMarker(marker),
          body: `${timeline.find((entry) => entry.id === 'answer:assistant')?.body || ''}${textDelta}`,
          format: 'markdown',
          agent: env.event.agent,
          status: env.event.status,
        })
        break
      case 'thinking': {
        const entryId = `thinking:${env.event.agent || 'assistant'}`
        const current = timeline.find((entry) => entry.id === entryId)
        timeline = upsertTimelineEntry(timeline, (entry) => entry.id === entryId, {
          id: entryId,
          timestamp: current?.timestamp ?? timestamp,
          kind: 'thinking',
          title: timelineTitleForMarker(marker, env.event.agent ? `Reasoning • ${env.event.agent}` : undefined),
          body: `${current?.body || ''}${textDelta}`,
          format: 'markdown',
          agent: env.event.agent,
          status: env.event.status,
        })
        break
      }
      case 'timeline_text': {
        const name = String(env.extra?.artifact_name || 'timeline')
        const current =
          index === 0
            ? null
            : (artifacts.find(
                (artifact) => artifact.kind === 'timeline' && artifact.name === name,
              ) as TimelineArtifact | undefined)
        const nextArtifact: TimelineArtifact = {
          kind: 'timeline',
          timestamp: current?.timestamp ?? timestamp,
          name,
          markdown: `${current?.markdown || ''}${textDelta}`,
        }
        artifacts = upsertArtifact(
          artifacts.filter((artifact) => !(index === 0 && artifact.kind === 'timeline' && artifact.name === name)),
          (artifact) => artifact.kind === 'timeline' && artifact.name === name,
          nextArtifact,
        )
        timeline = upsertTimelineEntry(
          timeline.filter((entry) => !(index === 0 && entry.id === `timeline:${name}`)),
          (entry) => entry.id === `timeline:${name}`,
          {
            id: `timeline:${name}`,
            timestamp: current?.timestamp ?? timestamp,
            kind: 'timeline',
            title: timelineTitleForMarker(marker, name),
            body: nextArtifact.markdown,
            format: 'markdown',
            agent: env.event.agent,
            status: env.event.status,
          },
        )
        break
      }
      case 'canvas': {
        const name = String(env.extra?.artifact_name || 'canvas')
        const format = typeof env.extra?.format === 'string' ? env.extra.format : null
        const title = typeof env.extra?.title === 'string' ? env.extra.title : null
        const current =
          index === 0
            ? null
            : (artifacts.find(
                (artifact) => artifact.kind === 'canvas' && artifact.name === name,
              ) as CanvasArtifact | undefined)
        const nextArtifact: CanvasArtifact = {
          kind: 'canvas',
          timestamp: current?.timestamp ?? timestamp,
          name,
          title,
          format,
          content: `${current?.content || ''}${textDelta}`,
        }
        artifacts = upsertArtifact(
          artifacts.filter((artifact) => !(index === 0 && artifact.kind === 'canvas' && artifact.name === name)),
          (artifact) => artifact.kind === 'canvas' && artifact.name === name,
          nextArtifact,
        )
        timeline = upsertTimelineEntry(
          timeline.filter((entry) => !(index === 0 && entry.id === `canvas:${name}`)),
          (entry) => entry.id === `canvas:${name}`,
          {
            id: `canvas:${name}`,
            timestamp: current?.timestamp ?? timestamp,
            kind: 'canvas',
            title: timelineTitleForMarker(marker, title || name),
            body: nextArtifact.content,
            format: format === 'markdown' ? 'markdown' : 'text',
            agent: env.event.agent,
            status: env.event.status,
          },
        )
        break
      }
      case 'subsystem': {
        const subtype = String(env.extra?.sub_type || '')
        const artifactName = String(env.extra?.artifact_name || 'Subsystem')
        const title = typeof env.extra?.title === 'string' ? env.extra.title : null
        const timelineEntryId = `subsystem:${subtype}:${String(env.extra?.execution_id || env.extra?.search_id || artifactName)}`
        let timelineBody = textDelta
        let timelineFormat: TimelineEntryFormat = 'text'
        if (subtype === 'web_search.filtered_results' || subtype === 'web_search.html_view') {
          const searchId = String(env.extra?.search_id || artifactName)
          const current = artifacts.find(
            (artifact) => artifact.kind === 'web_search' && artifact.searchId === searchId,
          ) as WebSearchArtifact | undefined
          const base: WebSearchArtifact = current || {
            kind: 'web_search',
            timestamp,
            searchId,
            name: artifactName,
            title,
            queries: [],
            items: [],
            objective: undefined,
            reportContent: null,
          }
          let nextArtifact = { ...base, title: title || base.title, name: artifactName || base.name }
          if (subtype === 'web_search.filtered_results') {
            const parsed = safeJsonParse<Record<string, unknown>>(textDelta, {})
            nextArtifact = {
              ...nextArtifact,
              objective: typeof parsed.objective === 'string' ? parsed.objective : nextArtifact.objective,
              queries: Array.isArray(parsed.queries)
                ? parsed.queries.filter((item): item is string => typeof item === 'string')
                : nextArtifact.queries,
              items: Array.isArray(parsed.results)
                ? (parsed.results as WebSearchItem[])
                : nextArtifact.items,
            }
            timelineBody = prettyJson(parsed)
            timelineFormat = 'json'
          } else {
            nextArtifact = {
              ...nextArtifact,
              reportContent: `${nextArtifact.reportContent || ''}${textDelta}`,
            }
            timelineBody = nextArtifact.reportContent || textDelta
            timelineFormat = 'markdown'
          }
          artifacts = upsertArtifact(artifacts, (artifact) => artifact.kind === 'web_search' && artifact.searchId === searchId, nextArtifact)
        } else if (subtype === 'web_fetch.results') {
          const executionId = String(env.extra?.execution_id || artifactName)
          const parsed = safeJsonParse<Record<string, unknown>>(textDelta, {})
          const nextArtifact: WebFetchArtifact = {
            kind: 'web_fetch',
            timestamp,
            executionId,
            name: artifactName,
            title,
            items: Array.isArray(parsed.urls) ? (parsed.urls as WebFetchItem[]) : [],
          }
          artifacts = upsertArtifact(artifacts, (artifact) => artifact.kind === 'web_fetch' && artifact.executionId === executionId, nextArtifact)
          timelineBody = prettyJson(parsed)
          timelineFormat = 'json'
        } else if (subtype.startsWith('code_exec.')) {
          const executionId = String(env.extra?.execution_id || artifactName)
          const current = artifacts.find(
            (artifact) => artifact.kind === 'code_exec' && artifact.executionId === executionId,
          ) as CodeExecArtifact | undefined
          const base: CodeExecArtifact = current || {
            kind: 'code_exec',
            timestamp,
            executionId,
            title,
          }
          let nextArtifact: CodeExecArtifact = {
            ...base,
            name: artifactName || base.name,
            title: title || base.title,
          }
          if (subtype === 'code_exec.code') {
            nextArtifact = {
              ...nextArtifact,
              language: typeof env.extra?.language === 'string' ? env.extra.language : nextArtifact.language,
              program: `${index === 0 ? '' : nextArtifact.program || ''}${textDelta}`,
            }
            timelineBody = nextArtifact.program || textDelta
            timelineFormat = 'code'
          } else if (subtype === 'code_exec.program.name') {
            nextArtifact = { ...nextArtifact, name: textDelta || nextArtifact.name }
            timelineBody = nextArtifact.name || textDelta
          } else if (subtype === 'code_exec.objective') {
            nextArtifact = { ...nextArtifact, objective: textDelta || nextArtifact.objective }
            timelineBody = nextArtifact.objective || textDelta
          } else if (subtype === 'code_exec.contract') {
            const parsed = safeJsonParse<Record<string, unknown>>(textDelta, {})
            nextArtifact = {
              ...nextArtifact,
              contract: Array.isArray(parsed.contract) ? (parsed.contract as CodeExecContractItem[]) : nextArtifact.contract,
            }
            timelineBody = prettyJson(parsed)
            timelineFormat = 'json'
          } else if (subtype === 'code_exec.status') {
            const parsed = safeJsonParse<Record<string, unknown>>(textDelta, {})
            nextArtifact = {
              ...nextArtifact,
              status: (parsed.status as CodeExecStatus | undefined) || nextArtifact.status,
            }
            timelineBody = prettyJson(parsed)
            timelineFormat = 'json'
          }
          artifacts = upsertArtifact(artifacts, (artifact) => artifact.kind === 'code_exec' && artifact.executionId === executionId, nextArtifact)
        }
        timeline = upsertTimelineEntry(
          timeline.filter((entry) => !(index === 0 && entry.id === timelineEntryId)),
          (entry) => entry.id === timelineEntryId,
          {
            id: timelineEntryId,
            timestamp: timeline.find((entry) => entry.id === timelineEntryId)?.timestamp ?? timestamp,
            kind: 'subsystem',
            title: timelineTitleForSubsystem(subtype, title || artifactName),
            body: timelineFormat === 'markdown'
              ? `${index === 0 ? '' : timeline.find((entry) => entry.id === timelineEntryId)?.body || ''}${timelineBody}`
              : timelineBody,
            format: timelineFormat,
            agent: env.event.agent,
            status: env.event.status,
          },
        )
        break
      }
      default:
        timeline = upsertTimelineEntry(timeline, (entry) => entry.id === `delta:${marker}`, {
          id: `delta:${marker}`,
          timestamp: timeline.find((entry) => entry.id === `delta:${marker}`)?.timestamp ?? timestamp,
          kind: 'timeline',
          title: timelineTitleForMarker(marker, marker),
          body: `${timeline.find((entry) => entry.id === `delta:${marker}`)?.body || ''}${textDelta}`,
          format: 'text',
          agent: env.event.agent,
          status: env.event.status,
        })
        break
    }

    if (env.event?.step === 'followups' && env.event?.status === 'completed' && Array.isArray(env.data?.items)) {
      nextTurn.followups = env.data.items.filter((item): item is string => typeof item === 'string')
    }

    return {
      ...nextTurn,
      artifacts,
      timeline,
    }
  })
}

function MarkdownBlock({ content, compact = false }: { content: string; compact?: boolean }) {
  const normalized = useMemo(() => closeStreamingMarkdown(content), [content])

  return (
    <ReactMarkdown
      remarkPlugins={markdownPlugins}
      components={{
        a: ({ children, href }) => (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-[var(--accent)] underline underline-offset-2"
          >
            {children}
          </a>
        ),
        p: ({ children }) => (
          <p className={compact ? 'my-1 leading-6' : 'my-3 leading-7'}>{children}</p>
        ),
        ul: ({ children }) => <ul className={compact ? 'my-1 list-disc pl-5' : 'my-3 list-disc pl-6'}>{children}</ul>,
        ol: ({ children }) => <ol className={compact ? 'my-1 list-decimal pl-5' : 'my-3 list-decimal pl-6'}>{children}</ol>,
        li: ({ children }) => <li className="my-1">{children}</li>,
        blockquote: ({ children }) => (
          <blockquote className="my-3 border-l-4 border-[rgba(29,109,115,0.22)] pl-4 text-[var(--muted)]">
            {children}
          </blockquote>
        ),
        pre: ({ children }) => (
          <pre className="my-3 overflow-x-auto rounded-2xl bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
            {children}
          </pre>
        ),
        code: ({ inline, children }) =>
          inline ? (
            <code className="rounded bg-[rgba(17,32,43,0.08)] px-1.5 py-0.5 text-[0.92em]">
              {children}
            </code>
          ) : (
            <code>{children}</code>
          ),
        table: ({ children }) => (
          <div className="my-3 overflow-x-auto rounded-2xl border border-[var(--line)]">
            <table className="min-w-full border-collapse text-sm">{children}</table>
          </div>
        ),
        th: ({ children }) => <th className="border-b border-[var(--line)] px-3 py-2 text-left">{children}</th>,
        td: ({ children }) => <td className="border-b border-[var(--line)] px-3 py-2 align-top">{children}</td>,
      }}
      className="markdown-body text-[15px]"
    >
      {normalized}
    </ReactMarkdown>
  )
}

function BannerStrip({
  banners,
  onDismiss,
}: {
  banners: Banner[]
  onDismiss: (id: string) => void
}) {
  if (banners.length === 0) return null
  return (
    <div className="space-y-2">
      {banners.map((banner) => (
        <div
          key={banner.id}
          className={`flex items-start gap-3 rounded-2xl border px-4 py-3 text-sm ${toneClass(banner.tone)}`}
        >
          <div className="min-w-0 flex-1">{banner.text}</div>
          <button
            type="button"
            className="rounded-full px-2 py-1 text-xs transition hover:bg-black/5"
            onClick={() => onDismiss(banner.id)}
          >
            Dismiss
          </button>
        </div>
      ))}
    </div>
  )
}

function ConversationsSidebar({
  conversations,
  query,
  activeConversationId,
  disabled,
  loading,
  error,
  loadingConversationId,
  onQueryChange,
  onRefresh,
  onSelect,
  onStartNew,
}: {
  conversations: ConversationSummary[]
  query: string
  activeConversationId: string | null
  disabled: boolean
  loading: boolean
  error: string | null
  loadingConversationId: string | null
  onQueryChange: (value: string) => void
  onRefresh: () => void
  onSelect: (conversationId: string) => void
  onStartNew: () => void
}) {
  return (
    <aside className="glass-panel h-fit rounded-[32px] px-4 py-4 lg:sticky lg:top-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">
            Chats
          </div>
          <div className="pt-1 text-lg font-semibold text-[var(--ink)]">
            Bundle conversations
          </div>
        </div>
        <button
          type="button"
          onClick={onStartNew}
          disabled={disabled}
          className="rounded-full border border-[var(--line)] bg-white/70 px-3 py-1.5 text-sm font-medium transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          New
        </button>
      </div>

      <div className="flex gap-2 pt-4">
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search chats"
          disabled={disabled}
          className="min-w-0 flex-1 rounded-full border border-[var(--line)] bg-white/75 px-4 py-2 text-sm outline-none transition placeholder:text-[var(--muted)] focus:border-[rgba(29,109,115,0.3)]"
        />
        <button
          type="button"
          onClick={onRefresh}
          className="rounded-full border border-[var(--line)] bg-white/70 px-3 py-2 text-sm font-medium transition hover:bg-white"
        >
          Refresh
        </button>
      </div>

      {error ? (
        <div className="mt-3 rounded-2xl border border-[rgba(165,63,50,0.18)] bg-[rgba(165,63,50,0.08)] px-3 py-2 text-sm text-[var(--danger)]">
          {error}
        </div>
      ) : null}

      {loading && conversations.length === 0 ? (
        <p className="pt-4 text-sm text-[var(--muted)]">Loading conversations…</p>
      ) : null}

      {!loading && conversations.length === 0 ? (
        <p className="pt-4 text-sm leading-6 text-[var(--muted)]">
          {query.trim()
            ? 'No chats match the current search.'
            : 'No saved chats for this bundle yet. Start a new one and it will appear here.'}
        </p>
      ) : null}

      {conversations.length > 0 ? (
        <div className="space-y-2 pt-4">
          {conversations.map((conversation) => {
            const isActive = conversation.id === activeConversationId
            const isLoading = loadingConversationId === conversation.id
            return (
              <button
                key={conversation.id}
                type="button"
                onClick={() => onSelect(conversation.id)}
                disabled={disabled || isLoading}
                className={`block w-full rounded-[24px] border px-4 py-3 text-left transition ${
                  isActive
                    ? 'border-[rgba(29,109,115,0.22)] bg-[rgba(29,109,115,0.1)]'
                    : 'border-[var(--line)] bg-white/65 hover:border-[rgba(29,109,115,0.16)] hover:bg-white'
                } disabled:cursor-wait disabled:opacity-70`}
              >
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-[var(--ink)]">
                      {conversation.title || 'Untitled conversation'}
                    </div>
                    <div className="pt-1 text-xs text-[var(--muted)]">
                      {formatConversationTime(conversation.lastActivityAt || conversation.startedAt)}
                    </div>
                    <div className="truncate pt-1 text-[11px] uppercase tracking-[0.08em] text-[var(--muted)]">
                      {conversation.id}
                    </div>
                  </div>
                  {isActive ? (
                    <span className="rounded-full bg-[rgba(29,109,115,0.14)] px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--accent)]">
                      Current
                    </span>
                  ) : null}
                </div>
                {isLoading ? (
                  <div className="pt-2 text-xs font-medium text-[var(--accent)]">Loading chat…</div>
                ) : null}
              </button>
            )
          })}
        </div>
      ) : null}
    </aside>
  )
}

function SuggestedQuestions({
  items,
  disabled,
  onSelect,
}: {
  items: string[]
  disabled: boolean
  onSelect: (text: string) => void
}) {
  if (items.length === 0) return null
  return (
    <div className="flex flex-wrap gap-2 pt-3">
      {items.map((item) => (
        <button
          key={item}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(item)}
          className="rounded-full border border-[rgba(29,109,115,0.16)] bg-[rgba(29,109,115,0.09)] px-3 py-1.5 text-sm text-[var(--accent)] transition hover:bg-[rgba(29,109,115,0.14)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {item}
        </button>
      ))}
    </div>
  )
}

function StepList({ steps }: { steps: TurnStep[] }) {
  if (steps.length === 0) return null
  return (
    <div className="space-y-2 pt-3">
      {steps.map((step) => (
        <div key={step.step} className="rounded-2xl border border-[var(--line)] bg-white/55 px-3 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{step.title || step.step}</span>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] ${stepTone(step.status)}`}>
              {step.status}
            </span>
            {step.agent ? <span className="text-xs text-[var(--muted)]">{step.agent}</span> : null}
          </div>
          {step.markdown ? <div className="pt-2"><MarkdownBlock content={step.markdown} compact /></div> : null}
          {!step.markdown && typeof step.data?.message === 'string' ? (
            <p className="pt-2 text-sm text-[var(--muted)]">{step.data.message}</p>
          ) : null}
          {step.error ? <p className="pt-2 text-sm text-[var(--danger)]">{step.error}</p> : null}
        </div>
      ))}
    </div>
  )
}

function TimelineFeed({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return <p className="pt-3 text-sm text-[var(--muted)]">No timeline events yet.</p>
  }

  const sortedEntries = entries.slice().sort((left, right) => left.timestamp - right.timestamp)

  const badgeClass = (kind: TimelineEntryKind): string => {
    switch (kind) {
      case 'answer':
        return 'bg-[rgba(29,109,115,0.12)] text-[var(--accent)]'
      case 'thinking':
        return 'bg-[rgba(190,136,72,0.14)] text-[#87541a]'
      case 'subsystem':
        return 'bg-[rgba(22,35,47,0.1)] text-[var(--ink)]'
      case 'error':
        return 'bg-[rgba(165,63,50,0.12)] text-[var(--danger)]'
      case 'lifecycle':
        return 'bg-[rgba(35,114,79,0.12)] text-[var(--success)]'
      default:
        return 'bg-[rgba(94,107,120,0.12)] text-[var(--muted)]'
    }
  }

  return (
    <div className="space-y-3 pt-3">
      {sortedEntries.map((entry) => (
        <div key={entry.id} className="rounded-[24px] border border-[var(--line)] bg-white/60 px-4 py-4">
          <div className="flex flex-wrap items-center gap-2 pb-2">
            <span className={`rounded-full px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] ${badgeClass(entry.kind)}`}>
              {entry.kind}
            </span>
            <span className="font-medium">{entry.title}</span>
            {entry.agent ? <span className="text-xs text-[var(--muted)]">{entry.agent}</span> : null}
            {entry.status ? <span className="text-xs text-[var(--muted)]">{entry.status}</span> : null}
            <span className="ml-auto text-xs text-[var(--muted)]">{formatTime(entry.timestamp)}</span>
          </div>
          {entry.body ? (
            entry.format === 'markdown' ? (
              <MarkdownBlock content={entry.body} compact />
            ) : entry.format === 'json' || entry.format === 'code' ? (
              <pre className="overflow-x-auto rounded-2xl bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
                {entry.body}
              </pre>
            ) : (
              <p className="whitespace-pre-wrap text-sm leading-6 text-[var(--ink)]">{entry.body}</p>
            )
          ) : (
            <p className="text-sm text-[var(--muted)]">No body payload.</p>
          )}
        </div>
      ))}
    </div>
  )
}

function DownloadsPanel({
  attachments,
  files,
  onError,
}: {
  attachments: TurnAttachment[]
  files: FileArtifact[]
  onError: (text: string) => void
}) {
  const [downloadingId, setDownloadingId] = useState<string | null>(null)

  if (attachments.length === 0 && files.length === 0) {
    return <p className="pt-3 text-sm text-[var(--muted)]">No downloadable files for this turn yet.</p>
  }

  const handleAttachmentDownload = async (attachment: TurnAttachment, index: number) => {
    try {
      setDownloadingId(`attachment:${index}`)
      if (attachment.file) {
        downloadBlobAsFile(attachment.file, attachment.name)
        return
      }
      if (attachment.rn) {
        await downloadResourceByRN(attachment.rn, attachment.name)
        return
      }
      if (attachment.hostedUri) {
        const response = await fetch(resolveAbsoluteUrl(attachment.hostedUri), {
          method: 'GET',
          credentials: 'include',
          headers: buildRequestHeaders(),
        })
        if (!response.ok) {
          const detail = await response.text().catch(() => response.statusText)
          throw new Error(`Failed to download attachment (${response.status}): ${detail}`)
        }
        downloadBlobAsFile(await response.blob(), attachment.name)
        return
      }
      throw new Error('Attachment download metadata is missing.')
    } catch (error) {
      onError(messageForError(error))
    } finally {
      setDownloadingId(null)
    }
  }

  const handleFileDownload = async (file: FileArtifact) => {
    try {
      setDownloadingId(`file:${file.rn}`)
      await downloadResourceByRN(file.rn, file.filename)
    } catch (error) {
      onError(messageForError(error))
    } finally {
      setDownloadingId(null)
    }
  }

  return (
    <div className="space-y-4 pt-3">
      {attachments.length > 0 ? (
        <div>
          <div className="pb-2 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
            Sent attachments
          </div>
          <div className="space-y-2">
            {attachments.map((attachment, index) => (
              <button
                key={attachment.id}
                type="button"
                onClick={() => void handleAttachmentDownload(attachment, index)}
                className="flex w-full items-center justify-between rounded-2xl border border-[var(--line)] bg-white/60 px-4 py-3 text-left transition hover:bg-white"
              >
                <div>
                  <div className="font-medium">{attachment.name}</div>
                  <div className="text-sm text-[var(--muted)]">
                    {typeof attachment.size === 'number' ? formatBytes(attachment.size) : attachment.mime || attachment.rn || 'Stored attachment'}
                  </div>
                </div>
                <span className="text-sm text-[var(--accent)]">
                  {downloadingId === `attachment:${index}` ? 'Preparing…' : 'Download'}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {files.length > 0 ? (
        <div>
          <div className="pb-2 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
            Assistant files
          </div>
          <div className="space-y-2">
            {files.map((file) => (
              <button
                key={file.rn}
                type="button"
                onClick={() => void handleFileDownload(file)}
                className="flex w-full items-center justify-between rounded-2xl border border-[var(--line)] bg-white/60 px-4 py-3 text-left transition hover:bg-white"
              >
                <div>
                  <div className="font-medium">{file.filename}</div>
                  <div className="text-sm text-[var(--muted)]">{file.description || file.mime || file.rn}</div>
                </div>
                <span className="text-sm text-[var(--accent)]">
                  {downloadingId === `file:${file.rn}` ? 'Downloading…' : 'Download'}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function ArtifactFeed({ artifacts }: { artifacts: Artifact[] }) {
  if (artifacts.length === 0) return null

  const sortedArtifacts = artifacts.slice().sort((left, right) => left.timestamp - right.timestamp)

  return (
    <div className="space-y-3 pt-3">
      {sortedArtifacts.map((artifact) => {
        if (artifact.kind === 'timeline') {
          return (
            <div key={`${artifact.kind}-${artifact.name}`} className="rounded-[24px] border border-[rgba(29,109,115,0.14)] bg-[rgba(29,109,115,0.07)] px-4 py-4">
              <div className="pb-2 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--accent)]">
                Timeline
              </div>
              <MarkdownBlock content={artifact.markdown} />
            </div>
          )
        }

        if (artifact.kind === 'canvas') {
          return (
            <details key={`${artifact.kind}-${artifact.name}`} className="rounded-[24px] border border-[var(--line)] bg-white/60 px-4 py-4">
              <summary className="cursor-pointer list-none font-medium">
                {artifact.title || artifact.name}
                <span className="pl-2 text-xs uppercase tracking-[0.12em] text-[var(--muted)]">
                  {artifact.format || 'text'}
                </span>
              </summary>
              <div className="pt-3">
                {artifact.format === 'markdown' ? (
                  <MarkdownBlock content={artifact.content} />
                ) : (
                  <pre className="overflow-x-auto rounded-2xl bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
                    {artifact.content}
                  </pre>
                )}
              </div>
            </details>
          )
        }

        if (artifact.kind === 'citation') {
          return (
            <a
              key={`${artifact.kind}-${artifact.url}`}
              href={artifact.url}
              target="_blank"
              rel="noreferrer"
              className="block rounded-[24px] border border-[var(--line)] bg-white/65 px-4 py-4 transition hover:-translate-y-0.5 hover:border-[rgba(29,109,115,0.2)]"
            >
              <div className="pb-1 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
                Link
              </div>
              <div className="font-medium">{artifact.title || artifact.url}</div>
              {artifact.body ? <p className="pt-1 text-sm text-[var(--muted)]">{artifact.body}</p> : null}
            </a>
          )
        }

        if (artifact.kind === 'file') {
          return (
            <div key={`${artifact.kind}-${artifact.rn}`} className="rounded-[24px] border border-[var(--line)] bg-white/65 px-4 py-4">
              <div className="pb-1 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
                File
              </div>
              <div className="font-medium">{artifact.filename}</div>
              <div className="pt-1 text-sm text-[var(--muted)]">
                {artifact.description || artifact.mime || artifact.rn}
              </div>
            </div>
          )
        }

        if (artifact.kind === 'web_search') {
          return (
            <div key={`${artifact.kind}-${artifact.searchId}`} className="rounded-[24px] border border-[var(--line)] bg-white/65 px-4 py-4">
              <div className="flex flex-wrap items-center gap-2 pb-2">
                <span className="rounded-full bg-[rgba(29,109,115,0.12)] px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--accent)]">
                  Web Search
                </span>
                <span className="font-medium">{artifact.title || artifact.name}</span>
              </div>
              {artifact.objective ? <p className="pb-2 text-sm text-[var(--muted)]">{artifact.objective}</p> : null}
              {artifact.queries.length > 0 ? (
                <div className="pb-3 text-sm text-[var(--muted)]">
                  Queries: {artifact.queries.join(' • ')}
                </div>
              ) : null}
              <div className="space-y-2">
                {artifact.items.slice(0, 4).map((item) => (
                  <a
                    key={item.url}
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="block rounded-2xl border border-[var(--line)] bg-[rgba(255,255,255,0.7)] px-3 py-3 transition hover:border-[rgba(29,109,115,0.2)]"
                  >
                    <div className="font-medium">{item.title || item.url}</div>
                    {item.body ? <p className="pt-1 text-sm text-[var(--muted)]">{item.body}</p> : null}
                  </a>
                ))}
              </div>
              {artifact.reportContent ? (
                <details className="pt-3">
                  <summary className="cursor-pointer text-sm font-medium text-[var(--accent)]">
                    Show report
                  </summary>
                  <div className="pt-3">
                    <MarkdownBlock content={artifact.reportContent} compact />
                  </div>
                </details>
              ) : null}
            </div>
          )
        }

        if (artifact.kind === 'web_fetch') {
          return (
            <div key={`${artifact.kind}-${artifact.executionId}`} className="rounded-[24px] border border-[var(--line)] bg-white/65 px-4 py-4">
              <div className="flex flex-wrap items-center gap-2 pb-2">
                <span className="rounded-full bg-[rgba(190,136,72,0.14)] px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#87541a]">
                  Web Fetch
                </span>
                <span className="font-medium">{artifact.title || artifact.name}</span>
              </div>
              <div className="space-y-2">
                {artifact.items.slice(0, 4).map((item) => (
                  <div key={item.url} className="rounded-2xl border border-[var(--line)] bg-[rgba(255,255,255,0.7)] px-3 py-3">
                    <a href={item.url} target="_blank" rel="noreferrer" className="font-medium underline underline-offset-2">
                      {item.url}
                    </a>
                    <div className="pt-1 text-sm text-[var(--muted)]">
                      {(item.status || 'unknown').toUpperCase()}
                      {item.mime ? ` • ${item.mime}` : ''}
                      {typeof item.content_length === 'number' ? ` • ${formatBytes(item.content_length)}` : ''}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )
        }

        if (artifact.kind === 'code_exec') {
          const statusLabel =
            artifact.status?.status === 'error'
              ? 'Error'
              : artifact.status?.status === 'exec'
                ? 'Executing'
                : artifact.status?.status === 'gen'
                  ? 'Generating'
                  : artifact.status?.status === 'done'
                    ? 'Done'
                    : 'Ready'

          return (
            <details key={`${artifact.kind}-${artifact.executionId}`} className="rounded-[24px] border border-[var(--line)] bg-white/65 px-4 py-4">
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2">
                <span className="rounded-full bg-[rgba(22,35,47,0.1)] px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--ink)]">
                  Exec
                </span>
                <span className="font-medium">{artifact.name || 'Program'}</span>
                <span className="text-sm text-[var(--muted)]">{statusLabel}</span>
              </summary>
              {artifact.objective ? <p className="pt-3 text-sm text-[var(--muted)]">{artifact.objective}</p> : null}
              {artifact.contract && artifact.contract.length > 0 ? (
                <div className="space-y-1 pt-3 text-sm">
                  {artifact.contract.map((item) => (
                    <div key={item.filename} className="rounded-xl bg-[rgba(17,32,43,0.05)] px-3 py-2">
                      <span className="font-medium">{item.filename}</span>
                      {item.description ? <span className="text-[var(--muted)]"> • {item.description}</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {artifact.program ? (
                <div className="pt-3">
                  <pre className="overflow-x-auto rounded-2xl bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
                    {artifact.program}
                  </pre>
                </div>
              ) : null}
              {artifact.status?.status === 'error' && artifact.status.error ? (
                <div className="pt-3 text-sm text-[var(--danger)]">
                  {Object.values(artifact.status.error).join(' ')}
                </div>
              ) : null}
            </details>
          )
        }

        return (
          <div key={`${artifact.kind}-${artifact.timestamp}`} className="rounded-[24px] border border-[rgba(165,63,50,0.14)] bg-[rgba(165,63,50,0.08)] px-4 py-4 text-[var(--danger)]">
            {artifact.message}
          </div>
        )
      })}
    </div>
  )
}

function TurnView({
  turn,
  sendingDisabled,
  onFollowup,
  onDownloadError,
}: {
  turn: ChatTurn
  sendingDisabled: boolean
  onFollowup: (text: string) => void
  onDownloadError: (text: string) => void
}) {
  const [activeTab, setActiveTab] = useState<TurnTab>('overview')
  const steps = useMemo(
    () => Object.values(turn.steps).sort((left, right) => left.timestamp - right.timestamp),
    [turn.steps],
  )
  const assistantFiles = useMemo(
    () => turn.artifacts.filter((artifact): artifact is FileArtifact => artifact.kind === 'file'),
    [turn.artifacts],
  )
  const overviewArtifacts = useMemo(
    () => turn.artifacts.filter((artifact) => artifact.kind !== 'file' && artifact.kind !== 'timeline'),
    [turn.artifacts],
  )

  return (
    <article className="glass-panel rounded-[32px] px-5 py-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
            User • {formatTime(turn.createdAt)}
          </div>
          <p className="pt-2 whitespace-pre-wrap text-[15px] leading-7">{turn.userMessage || 'Sent attachments only'}</p>
          {turn.userAttachments.length > 0 ? (
            <div className="flex flex-wrap gap-2 pt-3">
              {turn.userAttachments.map((attachment) => (
                <span
                  key={attachment.id}
                  className="rounded-full border border-[rgba(24,42,58,0.12)] bg-[rgba(24,42,58,0.05)] px-3 py-1 text-xs text-[var(--muted)]"
                >
                  {attachment.name}
                  {typeof attachment.size === 'number' ? ` • ${formatBytes(attachment.size)}` : ''}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] ${
            turn.state === 'error'
              ? 'bg-[rgba(165,63,50,0.12)] text-[var(--danger)]'
              : turn.state === 'completed'
                ? 'bg-[rgba(35,114,79,0.12)] text-[var(--success)]'
                : 'bg-[rgba(29,109,115,0.12)] text-[var(--accent)]'
          }`}
        >
          {turn.state}
        </span>
      </div>

      <div className="mt-5 rounded-[28px] border border-[var(--line)] bg-[var(--paper-strong)] px-4 py-4">
        <div className="pb-4 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
          Assistant
        </div>

        <div className="flex flex-wrap gap-2 border-b border-[var(--line)] pb-3">
          {([
            ['overview', 'Overview'],
            ['timeline', `Timeline${turn.timeline.length ? ` (${turn.timeline.length})` : ''}`],
            ['steps', `Steps${steps.length ? ` (${steps.length})` : ''}`],
            ['downloads', `Downloads${turn.userAttachments.length + assistantFiles.length ? ` (${turn.userAttachments.length + assistantFiles.length})` : ''}`],
          ] as Array<[TurnTab, string]>).map(([tab, label]) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`rounded-full px-3 py-1.5 text-sm transition ${
                activeTab === tab
                  ? 'bg-[var(--ink)] text-white'
                  : 'border border-[var(--line)] bg-white/70 text-[var(--ink)] hover:bg-white'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {activeTab === 'overview' ? (
          <>
            {turn.answer ? (
              <MarkdownBlock content={turn.answer} />
            ) : turn.state === 'error' ? (
              <p className="pt-3 text-sm text-[var(--danger)]">{turn.error || 'Request failed.'}</p>
            ) : (
              <p className="pt-3 text-sm text-[var(--muted)]">Streaming response…</p>
            )}
            <ArtifactFeed artifacts={overviewArtifacts} />
            <SuggestedQuestions items={turn.followups} disabled={sendingDisabled} onSelect={onFollowup} />
          </>
        ) : null}

        {activeTab === 'timeline' ? <TimelineFeed entries={turn.timeline} /> : null}
        {activeTab === 'steps' ? <StepList steps={steps} /> : null}
        {activeTab === 'downloads' ? (
          <DownloadsPanel attachments={turn.userAttachments} files={assistantFiles} onError={onDownloadError} />
        ) : null}
      </div>
    </article>
  )
}

function Composer({
  text,
  files,
  disabled,
  lockedMessage,
  onTextChange,
  onFilesAdd,
  onFileRemove,
  onSubmit,
}: {
  text: string
  files: File[]
  disabled: boolean
  lockedMessage: string | null
  onTextChange: (value: string) => void
  onFilesAdd: (files: FileList | null) => void
  onFileRemove: (index: number) => void
  onSubmit: () => void
}) {
  return (
    <div className="glass-panel sticky bottom-4 rounded-[28px] px-4 py-4">
      {lockedMessage ? (
        <div className="mb-3 rounded-2xl border border-[rgba(164,103,33,0.18)] bg-[rgba(164,103,33,0.08)] px-3 py-2 text-sm text-[var(--warning)]">
          {lockedMessage}
        </div>
      ) : null}

      {files.length > 0 ? (
        <div className="mb-3 flex flex-wrap gap-2">
          {files.map((file, index) => (
            <span
              key={`${file.name}-${file.size}-${index}`}
              className="inline-flex items-center gap-2 rounded-full border border-[rgba(24,42,58,0.12)] bg-white/65 px-3 py-1 text-xs"
            >
              <span>{file.name}</span>
              <span className="text-[var(--muted)]">{formatBytes(file.size)}</span>
              <button type="button" onClick={() => onFileRemove(index)} className="text-[var(--muted)] hover:text-[var(--ink)]">
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <label className="mb-3 inline-flex cursor-pointer items-center rounded-full border border-[rgba(24,42,58,0.12)] bg-white/70 px-3 py-1.5 text-sm font-medium text-[var(--ink)] transition hover:bg-white">
        Attach files
        <input type="file" multiple className="hidden" disabled={disabled} onChange={(event) => onFilesAdd(event.target.files)} />
      </label>

      <div className="grid gap-3 md:grid-cols-[1fr_auto]">
        <textarea
          value={text}
          disabled={disabled}
          onChange={(event) => onTextChange(event.target.value)}
          placeholder="Ask the versatile bundle anything. This sample main view supports attachments, SSE streaming, rate-limit banners, followups, and tool widgets."
          rows={4}
          className="min-h-[120px] rounded-[24px] border border-[var(--line)] bg-white/75 px-4 py-3 text-[15px] leading-7 shadow-[inset_0_1px_1px_rgba(0,0,0,0.02)] outline-none transition placeholder:text-[var(--muted)] focus:border-[rgba(29,109,115,0.32)] disabled:cursor-not-allowed disabled:opacity-60"
        />
        <button
          type="button"
          disabled={disabled || (!text.trim() && files.length === 0)}
          onClick={onSubmit}
          className="h-fit rounded-[22px] bg-[var(--ink)] px-5 py-3 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:bg-[#0d1922] disabled:cursor-not-allowed disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </div>
  )
}

export default function App() {
  const [state, setState] = useState<ChatState>(initialState)
  const [ready, setReady] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const [conversationQuery, setConversationQuery] = useState('')

  const stateRef = useRef(state)
  const eventSourceRef = useRef<EventSource | null>(null)
  const connectPromiseRef = useRef<Promise<void> | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const streamIdRef = useRef<string | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [state.turns, state.banners, ready])

  const hasPendingTurn = state.turns.some((turn) => turn.state === 'pending' || turn.state === 'running')
  const bundleId = settings.getBundleId() || BUILT_BUNDLE_ID
  const filteredConversations = useMemo(() => {
    const query = conversationQuery.trim().toLowerCase()
    const items = state.conversations.slice().sort((left, right) => (right.lastActivityAt || 0) - (left.lastActivityAt || 0))
    if (!query) return items
    return items.filter((item) => {
      const haystack = `${item.title || ''} ${item.id}`.toLowerCase()
      return haystack.includes(query)
    })
  }, [conversationQuery, state.conversations])

  const fetchProfile = async (): Promise<string> => {
    if (sessionIdRef.current) return sessionIdRef.current
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
    sessionIdRef.current = data.session_id
    setState((previous) => ({ ...previous, sessionId: data.session_id || null }))
    return data.session_id
  }

  const refreshConversationList = async () => {
    const tenant = settings.getTenant()
    const project = settings.getProject()
    if (!tenant || !project || !bundleId) return

    setState((previous) => ({
      ...previous,
      conversationsLoading: true,
      conversationsError: null,
    }))

    try {
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
      const conversations = (data.items || []).map((item) => ({
        id: item.conversation_id,
        title: item.title || null,
        startedAt: item.started_at ? Date.parse(item.started_at) : null,
        lastActivityAt: item.last_activity_at ? Date.parse(item.last_activity_at) : null,
      }))
      setState((previous) => ({
        ...previous,
        conversations,
        conversationsLoading: false,
        conversationsError: null,
      }))
    } catch (error) {
      const message = messageForError(error)
      setState((previous) => ({
        ...previous,
        conversationsLoading: false,
        conversationsError: message,
      }))
    }
  }

  const requestConversationStatus = async (conversationId: string) => {
    const streamId = streamIdRef.current
    if (!streamId) return
    try {
      await fetch(`${settings.getBaseUrl()}/sse/conv_status.get`, {
        method: 'POST',
        credentials: 'include',
        headers: buildRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ conversation_id: conversationId, stream_id: streamId }),
      })
    } catch (error) {
      console.warn('Unable to request conversation status', error)
    }
  }

  const loadConversation = async (conversationId: string) => {
    const tenant = settings.getTenant()
    const project = settings.getProject()
    if (!tenant || !project) return

    setState((previous) => ({
      ...previous,
      conversationLoadingId: conversationId,
      inputLocked: false,
      inputLockMessage: null,
    }))

    try {
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

      const conversation = (await response.json()) as ConversationDTO
      const turns = hydrateHistoricalConversation(conversation)

      setState((previous) => ({
        ...previous,
        conversationId: conversation.conversation_id,
        conversationTitle: conversation.conversation_title || null,
        turns,
        composerText: '',
        composerFiles: [],
        conversationLoadingId: null,
      }))

      if (stateRef.current.connection === 'connected') {
        void requestConversationStatus(conversation.conversation_id)
      }
    } catch (error) {
      const message = messageForError(error)
      setState((previous) => ({
        ...previous,
        conversationLoadingId: null,
      }))
      setBootError(message)
    }
  }

  const startNewChat = () => {
    setState((previous) => ({
      ...previous,
      conversationId: null,
      conversationTitle: null,
      turns: [],
      composerText: '',
      composerFiles: [],
      inputLocked: false,
      inputLockMessage: null,
      conversationLoadingId: null,
    }))
  }

  const resetTransport = () => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    streamIdRef.current = null
    connectPromiseRef.current = null
  }

  const bindEventSource = (eventSource: EventSource) => {
    const bind = <T,>(eventName: string, handler: (payload: T) => void) => {
      eventSource.addEventListener(eventName, (event: MessageEvent) => {
        try {
          handler(JSON.parse(event.data) as T)
        } catch (error) {
          console.error('Malformed SSE event', eventName, error)
        }
      })
    }

    bind<ChatStartEnvelope>('chat_start', (env) => {
      setState((previous) => applyChatStart(previous, env))
    })

    bind<ChatStepEnvelope>('chat_step', (env) => {
      setState((previous) => applyChatStep(previous, env))
    })

    bind<ChatDeltaEnvelope>('chat_delta', (env) => {
      setState((previous) => applyChatDelta(previous, env))
    })

    bind<ChatCompleteEnvelope>('chat_complete', (env) => {
      setState((previous) => applyChatComplete(previous, env))
      void refreshConversationList()
    })

    bind<ChatErrorEnvelope>('chat_error', (env) => {
      setState((previous) => applyChatError(previous, env))
    })

    bind<ConvStatusEnvelope>('conv_status', (env) => {
      setState((previous) => applyConvStatus(previous, env))
    })

    bind<ChatServiceEnvelope>('chat_service', (env) => {
      const data = (env.data || {}) as Record<string, unknown>
      const rateLimit = (env.data?.rate_limit || null) as RateLimitPayload | null

      let tone: BannerTone = (rateLimit?.notification_type || data.notification_type || 'warning') as BannerTone
      let message = ''

      switch (env.type) {
        case 'rate_limit.warning':
        case 'rate_limit.denied':
        case 'rate_limit.post_run_exceeded':
          message = rateLimit?.user_message || fallbackRateLimitMessage(rateLimit || undefined, data)
          break
        case 'rate_limit.no_funding':
          tone = (data.notification_type as BannerTone | undefined) || 'error'
          message = (data.user_message as string | undefined) || 'This service is not available for your account type.'
          break
        case 'rate_limit.subscription_exhausted':
          tone = (data.notification_type as BannerTone | undefined) || 'error'
          message =
            (data.user_message as string | undefined) ||
            'Your subscription balance is exhausted. Top up your balance to continue.'
          break
        case 'rate_limit.project_exhausted': {
          tone = 'error'
          const hasPersonalBudget = Boolean(data.has_personal_budget)
          const usdShort = typeof data.usd_short === 'number' ? data.usd_short : null
          if (hasPersonalBudget && usdShort && usdShort > 0) {
            message = `Project budget exhausted. You need $${usdShort.toFixed(2)} more in personal credits to run this request.`
          } else if (!hasPersonalBudget) {
            message = 'Project budget exhausted. Please contact your administrator to add funds.'
          } else {
            message = 'Project budget exhausted. Unable to process this request.'
          }
          break
        }
        case 'rate_limit.attachment_failure':
          tone = (data.notification_type as BannerTone | undefined) || 'error'
          message = (data.user_message as string | undefined) || 'Attachment was rejected.'
          break
        case 'rate_limit.lane_switch':
        case 'economics.user_underfunded_absorbed':
          return
        default:
          message =
            rateLimit?.user_message ||
            (data.user_message as string | undefined) ||
            `${env.type}: service message received`
      }

      setState((previous) => {
        let next = addBanner(previous, tone, message)
        const shouldLockInput =
          tone === 'error' &&
          env.type !== 'rate_limit.attachment_failure' &&
          env.type !== 'rate_limit.warning'

        if (shouldLockInput) {
          next = {
            ...next,
            inputLocked: true,
            inputLockMessage: message,
          }
        }

        if (env.type === 'rate_limit.attachment_failure') {
          next = {
            ...next,
            composerFiles: [],
          }
        }

        return next
      })
    })
  }

  const connectStream = async () => {
    if (eventSourceRef.current && streamIdRef.current) {
      return
    }

    if (connectPromiseRef.current) {
      await connectPromiseRef.current
      return
    }

    connectPromiseRef.current = (async () => {
      setState((previous) => ({ ...previous, connection: 'connecting' }))
      const sessionId = await fetchProfile()
      const streamId = createLocalId('stream')
      streamIdRef.current = streamId

      await new Promise<void>((resolve, reject) => {
        const url = new URL(`${settings.getBaseUrl()}/sse/stream`)
        url.searchParams.set('user_session_id', sessionId)
        url.searchParams.set('stream_id', streamId)
        if (settings.getTenant()) url.searchParams.set('tenant', settings.getTenant())
        if (settings.getProject()) url.searchParams.set('project', settings.getProject())
        if (settings.getAccessToken()) url.searchParams.set('bearer_token', settings.getAccessToken()!)
        if (settings.getIdToken()) url.searchParams.set('id_token', settings.getIdToken()!)

        const eventSource = new EventSource(url.toString(), { withCredentials: true })
        eventSourceRef.current = eventSource
        bindEventSource(eventSource)

        let opened = false
        const timeout = window.setTimeout(() => {
          if (!opened) {
            eventSource.close()
            if (eventSourceRef.current === eventSource) eventSourceRef.current = null
            streamIdRef.current = null
            reject(new Error('Timed out connecting to the event stream.'))
          }
        }, 8000)

        eventSource.addEventListener('open', () => {
          opened = true
          window.clearTimeout(timeout)
          setState((previous) => ({ ...previous, connection: 'connected' }))
          if (stateRef.current.conversationId) {
            void requestConversationStatus(stateRef.current.conversationId)
          }
          resolve()
        })

        eventSource.addEventListener('error', () => {
          if (!opened) {
            window.clearTimeout(timeout)
            eventSource.close()
            if (eventSourceRef.current === eventSource) eventSourceRef.current = null
            streamIdRef.current = null
            reject(new Error('Unable to open the event stream.'))
            return
          }
          setState((previous) => ({ ...previous, connection: 'disconnected' }))
        })
      })
    })()

    try {
      await connectPromiseRef.current
    } finally {
      connectPromiseRef.current = null
    }
  }

  const sendMessage = async (textOverride?: string) => {
    const draftText = (textOverride ?? stateRef.current.composerText).trim()
    const draftFiles = textOverride ? [] : stateRef.current.composerFiles
    if (!draftText && draftFiles.length === 0) return

    const turnId = createLocalId('turn')
    const conversationId = stateRef.current.conversationId || createLocalId('conv')

    setState((previous) => ({
      ...previous,
      conversationId,
      composerText: '',
      composerFiles: [],
          turns: [
        ...previous.turns,
        {
          id: turnId,
          state: 'pending',
          createdAt: Date.now(),
          userMessage: draftText,
          userAttachments: draftFiles.map((file, index) =>
            normalizeTurnAttachment(
              {
                filename: file.name,
                size: file.size,
                mime: file.type,
              },
              `live:${turnId}:${index}`,
              file,
            ),
          ),
          answer: '',
          error: null,
          steps: {},
          artifacts: [],
          timeline: [],
          followups: [],
        },
      ],
    }))

    try {
      await connectStream()
      const streamId = streamIdRef.current
      if (!streamId) {
        throw new Error('No SSE stream is available.')
      }

      const messagePayload = {
        message: {
          message: draftText,
          chat_history: buildChatHistory(stateRef.current.turns),
          project: settings.getProject(),
          tenant: settings.getTenant(),
          turn_id: turnId,
          conversation_id: conversationId,
          bundle_id: bundleId,
        },
        attachment_meta: draftFiles.map((file) => ({ filename: file.name })),
      }

      const url = new URL(`${settings.getBaseUrl()}/sse/chat`)
      url.searchParams.set('stream_id', streamId)

      let response: Response
      if (draftFiles.length > 0) {
        const form = new FormData()
        form.set('message', JSON.stringify(messagePayload))
        form.set('attachment_meta', JSON.stringify(draftFiles.map((file) => ({ filename: file.name }))))
        draftFiles.forEach((file) => form.append('files', file, file.name))
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
          body: JSON.stringify(messagePayload),
        })
      }

      if (!response.ok) {
        const detail = await response.text().catch(() => response.statusText)
        throw new Error(`sse/chat failed (${response.status}) ${detail}`)
      }
      await response.json().catch(() => null)
      void refreshConversationList()
    } catch (error) {
      const text = messageForError(error)
      setState((previous) => applyChatError(previous, {
        type: 'chat.error',
        timestamp: new Date().toISOString(),
        service: { request_id: createLocalId('request') },
        conversation: {
          session_id: previous.sessionId || '',
          conversation_id: conversationId,
          turn_id: turnId,
        },
        event: {
          step: 'send',
          status: 'error',
          title: 'Send failed',
        },
        data: { error: text },
      }))
    }
  }

  const handleReconnect = async () => {
    resetTransport()
    try {
      await connectStream()
      setBootError(null)
    } catch (error) {
      setBootError(messageForError(error))
    }
  }

  useEffect(() => {
    let mounted = true
    ;(async () => {
      try {
        await settings.setupParentListener()
        if (!mounted) return
        setReady(true)
        await connectStream()
      } catch (error) {
        if (!mounted) return
        setBootError(messageForError(error))
      }
    })()

    return () => {
      mounted = false
      resetTransport()
    }
  }, [])

  useEffect(() => {
    if (!ready) return
    void refreshConversationList()
  }, [ready, bundleId])

  if (!ready) {
    return (
      <div className="shell-grid flex min-h-screen items-center justify-center px-6">
        <div className="glass-panel rounded-[32px] px-8 py-7 text-center">
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">
            Versatile Bundle
          </div>
          <div className="pt-3 text-lg font-medium">Connecting iframe config…</div>
        </div>
      </div>
    )
  }

  return (
    <div className="shell-grid">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-5 sm:px-6 lg:px-8">
        <header className="glass-panel rounded-[34px] px-5 py-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
                Versatile Bundle Main View
              </div>
              <h1 className="pt-2 text-2xl font-semibold tracking-tight text-[var(--ink)]">
                Lightweight iframe chat over the same SSE and REST contract as the platform client
              </h1>
              <p className="pt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
                Bundle: <span className="font-medium text-[var(--ink)]">{bundleId}</span>
                {' '}•{' '}
                Scope: <span className="font-medium text-[var(--ink)]">{settings.getTenant() || '(tenant)'}</span>
                {' / '}
                <span className="font-medium text-[var(--ink)]">{settings.getProject() || '(project)'}</span>
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] ${
                  state.connection === 'connected'
                    ? 'bg-[rgba(35,114,79,0.12)] text-[var(--success)]'
                    : state.connection === 'disconnected'
                      ? 'bg-[rgba(165,63,50,0.12)] text-[var(--danger)]'
                      : 'bg-[rgba(29,109,115,0.12)] text-[var(--accent)]'
                }`}
              >
                {state.connection}
              </span>
              <button
                type="button"
                onClick={startNewChat}
                disabled={hasPendingTurn}
                className="rounded-full border border-[var(--line)] bg-white/70 px-3 py-1.5 text-sm font-medium transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                New chat
              </button>
              <button
                type="button"
                onClick={handleReconnect}
                className="rounded-full border border-[var(--line)] bg-white/70 px-3 py-1.5 text-sm font-medium transition hover:bg-white"
              >
                Reconnect
              </button>
            </div>
          </div>
        </header>

        <div className="pt-4">
          <BannerStrip
            banners={bootError ? [{ id: 'boot-error', tone: 'error', text: bootError }, ...state.banners] : state.banners}
            onDismiss={(id) => {
              if (id === 'boot-error') {
                setBootError(null)
                return
              }
              setState((previous) => ({
                ...previous,
                banners: previous.banners.filter((banner) => banner.id !== id),
              }))
            }}
          />
        </div>

        <main className="flex-1 pt-4">
          <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
            <ConversationsSidebar
              conversations={filteredConversations}
              query={conversationQuery}
              activeConversationId={state.conversationId}
              disabled={hasPendingTurn}
              loading={state.conversationsLoading}
              error={state.conversationsError}
              loadingConversationId={state.conversationLoadingId}
              onQueryChange={setConversationQuery}
              onRefresh={() => void refreshConversationList()}
              onSelect={(conversationId) => void loadConversation(conversationId)}
              onStartNew={startNewChat}
            />

            <div className="min-w-0">
              <section className="glass-panel rounded-[32px] px-5 py-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">
                      {state.conversationId ? 'Selected chat' : 'New chat'}
                    </div>
                    <div className="truncate pt-1 text-lg font-semibold text-[var(--ink)]">
                      {state.conversationTitle || (state.conversationId ? 'Untitled conversation' : 'Start a bundle-scoped conversation')}
                    </div>
                    <div className="truncate pt-1 text-sm text-[var(--muted)]">
                      {state.conversationId || 'No saved conversation selected yet'}
                    </div>
                  </div>
                  <div className="text-sm text-[var(--muted)]">
                    {state.conversationsLoading ? 'Refreshing chats…' : `${state.conversations.length} saved chat${state.conversations.length === 1 ? '' : 's'}`}
                  </div>
                </div>
              </section>

              <div className="pt-4">
                {state.turns.length === 0 ? (
                  <section className="glass-panel rounded-[36px] px-6 py-10 text-center">
                    <div className="mx-auto max-w-2xl">
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">
                        Sample capabilities
                      </div>
                      <h2 className="pt-3 text-3xl font-semibold tracking-tight">
                        One bundle UI, minimal slice
                      </h2>
                      <p className="pt-4 text-[15px] leading-7 text-[var(--muted)]">
                        This reference main view intentionally stays small while still covering the important runtime
                        behaviors: existing chats, attachments, SSE markdown streaming, step updates, followups,
                        citations, rate-limit banners, and tool widgets for exec, web search, and web fetch.
                      </p>
                      <div className="flex flex-wrap justify-center gap-2 pt-6">
                        {[
                          'Summarize the last attachment as markdown',
                          'Search the web and cite three sources about React compiler',
                          'Run an exec tool to generate a small report',
                        ].map((prompt) => (
                          <button
                            key={prompt}
                            type="button"
                            className="rounded-full border border-[rgba(29,109,115,0.16)] bg-[rgba(29,109,115,0.09)] px-4 py-2 text-sm text-[var(--accent)] transition hover:bg-[rgba(29,109,115,0.14)]"
                            onClick={() => setState((previous) => ({ ...previous, composerText: prompt }))}
                          >
                            {prompt}
                          </button>
                        ))}
                      </div>
                    </div>
                  </section>
                ) : (
                  <div className="space-y-4">
                    {state.turns.map((turn) => (
                      <TurnView
                        key={turn.id}
                        turn={turn}
                        sendingDisabled={hasPendingTurn || state.inputLocked}
                        onDownloadError={(text) =>
                          setState((previous) => addBanner(previous, 'error', `Download failed: ${text}`))
                        }
                        onFollowup={(text) => {
                          if (hasPendingTurn || state.inputLocked) return
                          void sendMessage(text)
                        }}
                      />
                    ))}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            </div>
          </div>
        </main>

        <div className="pt-4">
          <Composer
            text={state.composerText}
            files={state.composerFiles}
            disabled={state.inputLocked || hasPendingTurn || state.connection === 'booting'}
            lockedMessage={state.inputLockMessage}
            onTextChange={(value) => setState((previous) => ({ ...previous, composerText: value }))}
            onFilesAdd={(files) =>
              setState((previous) => ({
                ...previous,
                composerFiles: files ? [...previous.composerFiles, ...Array.from(files)] : previous.composerFiles,
              }))
            }
            onFileRemove={(index) =>
              setState((previous) => ({
                ...previous,
                composerFiles: previous.composerFiles.filter((_, currentIndex) => currentIndex !== index),
              }))
            }
            onSubmit={() => {
              if (hasPendingTurn || state.inputLocked) return
              void sendMessage()
            }}
          />
        </div>
      </div>
    </div>
  )
}
