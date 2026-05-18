import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import {
  downloadBlobAsFile,
  downloadHostedFile,
  downloadResourceByRN,
  fetchConversationById,
  listBundleConversations,
  openChatStream,
  requestConversationStatus,
  submitChatMessage,
} from './service'
import type {
  BaseEnvelope,
  BannerTone,
  ChatCompleteEnvelope,
  ChatDeltaEnvelope,
  ChatErrorEnvelope,
  ChatHistoryItem,
  ChatServiceEnvelope,
  ChatStartEnvelope,
  ChatStepEnvelope,
  ConversationArtifactDTO,
  ConversationDTO,
  ConversationSummary,
  ContinuationKind,
  ConvStatusEnvelope,
  RateLimitPayload,
  StepStatus,
} from './service'
import { BUILT_BUNDLE_ID, createLocalId, settings } from './settings'

type ConnectionState = 'booting' | 'connecting' | 'connected' | 'disconnected'
type TurnState = 'pending' | 'running' | 'completed' | 'error'
type TurnTab = 'overview' | 'timeline' | 'steps' | 'links' | 'files'

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

interface AdditionalUserMessage {
  id: string
  text: string
  timestamp: number
  attachments: TurnAttachment[]
  continuationKind: Exclude<ContinuationKind, 'regular'>
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
  additionalUserMessages: AdditionalUserMessage[]
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
      return 'border-[rgba(247,96,154,0.3)] bg-[var(--danger-soft)] text-[var(--danger)]'
    case 'warning':
      return 'border-[rgba(240,188,46,0.38)] bg-[var(--gold-soft)] text-[var(--warning)]'
    default:
      return 'border-[rgba(217,229,99,0.34)] bg-[var(--accent-soft)] text-[var(--accent)]'
  }
}

function stepTone(status: StepStatus): string {
  switch (status) {
    case 'completed':
      return 'bg-[var(--success-soft)] text-[var(--success)]'
    case 'error':
      return 'bg-[var(--danger-soft)] text-[var(--danger)]'
    case 'skipped':
      return 'bg-[rgba(94,107,120,0.12)] text-[var(--muted)]'
    default:
      return 'bg-[var(--accent-soft)] text-[var(--accent)]'
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

function ensureTurn(state: ChatState, turnId: string, createdAt: number, message = ''): ChatState {
  if (state.turns.some((turn) => turn.id === turnId)) return state
  return {
    ...state,
    turns: [
      ...state.turns,
      {
        ...createEmptyTurn(turnId, createdAt, message),
        state: 'running',
      },
    ],
  }
}

function syncConversationFromEnvelope(state: ChatState, env: BaseEnvelope): ChatState {
  const conversationId = env.conversation?.conversation_id
  const turnId = env.conversation?.turn_id
  if (!conversationId || !turnId) return state
  if (!state.turns.some((turn) => turn.id === turnId)) return state
  if (state.conversationId && state.conversationId !== conversationId) return state
  return {
    ...state,
    conversationId,
  }
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

function buildChatHistory(turns: ChatTurn[]): ChatHistoryItem[] {
  return turns.reduce<ChatHistoryItem[]>((items, turn) => {
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

function findActiveTurn(turns: ChatTurn[]): ChatTurn | null {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index]
    if (turn.state === 'pending' || turn.state === 'running') return turn
  }
  return null
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
    additionalUserMessages: [],
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
          const dataRecord = artifact.data && typeof artifact.data === 'object'
            ? artifact.data as Record<string, unknown>
            : {}
          const text =
            (typeof artifact.data?.text === 'string' && artifact.data.text) ||
            (typeof payload.text === 'string' && payload.text) ||
            ''
          const continuationKind =
            (typeof dataRecord.continuation_kind === 'string' && dataRecord.continuation_kind) ||
            (typeof payload.continuation_kind === 'string' && payload.continuation_kind) ||
            null
          if (turn.userMessage || continuationKind === 'followup' || continuationKind === 'steer') {
            turn = {
              ...turn,
              createdAt: Math.min(turn.createdAt, ts),
              additionalUserMessages: [
                ...turn.additionalUserMessages,
                {
                  id: `stored-user:${turnDto.turn_id}:${turn.additionalUserMessages.length}`,
                  text,
                  timestamp: ts,
                  attachments: [],
                  continuationKind: continuationKind === 'steer' ? 'steer' : 'followup',
                },
              ],
            }
            break
          }
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
    const hydratedTurn: ChatTurn = {
      ...turn,
      createdAt: Number.isFinite(turn.createdAt) ? turn.createdAt : Date.now() + turnIndex,
      state: 'completed',
      timeline: sortedTimeline,
    }
    return hydratedTurn
  }).sort((left, right) => left.createdAt - right.createdAt)
}

function applyChatStart(state: ChatState, env: ChatStartEnvelope): ChatState {
  const timestamp = timestampValue(env.timestamp)
  const ensuredState = ensureTurn(
    state,
    env.conversation.turn_id,
    timestamp,
    typeof env.data?.message === 'string' ? env.data.message : '',
  )
  const syncedState = syncConversationFromEnvelope(ensuredState, env)
  return updateTurn(syncedState, env.conversation.turn_id, (turn) => ({
    ...turn,
    state: 'running',
    timeline: [
      ...turn.timeline,
      {
        id: `lifecycle:start:${env.service.request_id}:${env.conversation.turn_id}`,
        timestamp,
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
  const ensuredState = ensureTurn(state, env.conversation.turn_id, timestampValue(env.timestamp))
  const syncedState = syncConversationFromEnvelope(ensuredState, env)
  return updateTurn(syncedState, env.conversation.turn_id, (turn) => ({
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
  const ensuredState = ensureTurn(state, env.conversation.turn_id, timestampValue(env.timestamp))
  const syncedState = syncConversationFromEnvelope(ensuredState, env)
  return updateTurn(syncedState, env.conversation.turn_id, (turn) => ({
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
      const nextState: TurnState = env.data.state === 'error' ? 'error' : 'completed'
      return {
        ...turn,
        state: nextState,
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
  const syncedState = syncConversationFromEnvelope(
    ensureTurn(state, env.conversation.turn_id, timestampValue(env.timestamp)),
    env,
  )
  return updateTurn(syncedState, env.conversation.turn_id, (turn) => {
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

  const syncedState = syncConversationFromEnvelope(ensureTurn(state, turnId, timestamp), env)
  return updateTurn(syncedState, turnId, (turn) => {
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
    <div className="markdown-body text-[15px]">
      <ReactMarkdown
        remarkPlugins={markdownPlugins}
        components={{
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-[var(--action)] underline underline-offset-2"
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
            <blockquote className="my-3 border-l-2 border-[rgba(244,204,71,0.42)] pl-4 text-[var(--muted)]">
              {children}
            </blockquote>
          ),
          pre: ({ children }) => (
            <pre className="my-3 overflow-x-auto rounded-lg bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
              {children}
            </pre>
          ),
          code: ({ children, className }) =>
            className ? (
              <code className={className}>{children}</code>
            ) : (
              <code className="rounded bg-[rgba(17,32,43,0.08)] px-1.5 py-0.5 text-[0.92em]">
                {children}
              </code>
            ),
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-lg border border-[var(--line)]">
              <table className="min-w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => <th className="border-b border-[var(--line)] px-3 py-2 text-left">{children}</th>,
          td: ({ children }) => <td className="border-b border-[var(--line)] px-3 py-2 align-top">{children}</td>,
        }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
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
          className={`flex items-start gap-3 rounded-lg border px-4 py-3 text-sm ${toneClass(banner.tone)}`}
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
    <aside className="glass-panel flex min-h-[520px] overflow-hidden rounded-lg lg:sticky lg:top-4">
      <nav className="flex w-12 flex-col items-center gap-2 border-r border-[var(--line)] bg-[rgba(255,255,255,0.54)] py-3">
        {[
          ['Chats', 'C'],
          ['Artifacts', 'A'],
          ['Settings', 'S'],
        ].map(([label, glyph], index) => (
          <button
            key={label}
            type="button"
            aria-label={label}
            className={`grid h-8 w-8 place-items-center rounded-md border text-xs font-semibold transition ${
              index === 0
                ? 'border-[rgba(67,114,195,0.26)] bg-[var(--action-soft)] text-[var(--action)]'
                : 'border-transparent text-[var(--muted)] hover:border-[var(--line)] hover:bg-white'
            }`}
          >
            {glyph}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1 px-3 py-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">
              Chats
            </div>
            <div className="pt-1 text-base font-semibold text-[var(--ink)]">
              Conversations
            </div>
          </div>
          <button
            type="button"
            onClick={onStartNew}
            disabled={disabled}
            className="rounded-md border border-[var(--line)] bg-white/70 px-2.5 py-1.5 text-sm font-medium transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
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
            className="min-w-0 flex-1 rounded-md border border-[var(--line)] bg-white/75 px-3 py-2 text-sm outline-none transition placeholder:text-[var(--muted)] focus:border-[var(--action)]"
          />
          <button
            type="button"
            onClick={onRefresh}
            className="rounded-md border border-[var(--line)] bg-white/70 px-2.5 py-2 text-sm font-medium transition hover:bg-white"
          >
            R
          </button>
        </div>

        {error ? (
          <div className="mt-3 rounded-lg border border-[rgba(247,96,154,0.28)] bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">
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
              : 'No saved chats yet. Start a new one and it will appear here.'}
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
                  className={`block w-full rounded-lg border px-3 py-3 text-left transition ${
                    isActive
                      ? 'border-[rgba(67,114,195,0.26)] bg-[var(--action-soft)]'
                      : 'border-[var(--line)] bg-white/65 hover:border-[rgba(67,114,195,0.22)] hover:bg-white'
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
                      <span className="rounded-md bg-[var(--action-soft)] px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--action)]">
                        Open
                      </span>
                    ) : null}
                  </div>
                  {isLoading ? (
                    <div className="pt-2 text-xs font-medium text-[var(--action)]">Loading chat…</div>
                  ) : null}
                </button>
              )
            })}
          </div>
        ) : null}
      </div>
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
          className="rounded-md border border-[rgba(67,114,195,0.24)] bg-[var(--action-soft)] px-3 py-1.5 text-sm text-[var(--action)] transition hover:bg-[rgba(67,114,195,0.16)] disabled:cursor-not-allowed disabled:opacity-50"
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
        <div key={step.step} className="rounded-lg border border-[var(--line)] bg-white/55 px-3 py-3">
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

interface TurnLink {
  id: string
  kind: 'citation' | 'web_search' | 'web_fetch'
  title: string
  url: string
  body?: string | null
}

function shortUrl(url: string): string {
  try {
    const parsed = new URL(url)
    return parsed.hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function collectTurnLinks(artifacts: Artifact[]): TurnLink[] {
  const links: TurnLink[] = []
  const seen = new Set<string>()

  const addLink = (link: TurnLink) => {
    if (!link.url || seen.has(link.url)) return
    seen.add(link.url)
    links.push(link)
  }

  artifacts.forEach((artifact) => {
    if (artifact.kind === 'citation') {
      addLink({
        id: `citation:${artifact.url}`,
        kind: 'citation',
        title: artifact.title || artifact.url,
        url: artifact.url,
        body: artifact.body,
      })
    }
    if (artifact.kind === 'web_search') {
      artifact.items.forEach((item) => {
        addLink({
          id: `web-search:${item.url}`,
          kind: 'web_search',
          title: item.title || item.url,
          url: item.url,
          body: item.body,
        })
      })
    }
    if (artifact.kind === 'web_fetch') {
      artifact.items.forEach((item) => {
        addLink({
          id: `web-fetch:${item.url}`,
          kind: 'web_fetch',
          title: item.url,
          url: item.url,
          body: [
            item.status ? item.status.toUpperCase() : null,
            item.mime,
            typeof item.content_length === 'number' ? formatBytes(item.content_length) : null,
          ].filter(Boolean).join(' • '),
        })
      })
    }
  })

  return links
}

function LinksPanel({ links }: { links: TurnLink[] }) {
  if (links.length === 0) {
    return <p className="pt-3 text-sm text-[var(--muted)]">No links have been produced for this turn yet.</p>
  }

  const toneClassForLink = (kind: TurnLink['kind']) => {
    switch (kind) {
      case 'web_search':
        return 'bg-[var(--action-soft)] text-[var(--action)]'
      case 'web_fetch':
        return 'bg-[var(--gold-soft)] text-[var(--warning)]'
      default:
        return 'bg-[var(--blue-pale)] text-[var(--blue-dark)]'
    }
  }

  return (
    <div className="space-y-2 pt-3">
      {links.map((link) => (
        <a
          key={link.id}
          href={link.url}
          target="_blank"
          rel="noreferrer"
          className="block rounded-lg border border-[var(--line)] bg-white/65 px-4 py-3 transition hover:border-[rgba(67,114,195,0.28)] hover:bg-white"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] ${toneClassForLink(link.kind)}`}>
              {link.kind.replace('_', ' ')}
            </span>
            <span className="text-xs text-[var(--muted)]">{shortUrl(link.url)}</span>
          </div>
          <div className="pt-2 font-medium text-[var(--ink)]">{link.title}</div>
          {link.body ? <p className="line-clamp-3 pt-1 text-sm leading-6 text-[var(--muted)]">{link.body}</p> : null}
        </a>
      ))}
    </div>
  )
}

function ThinkingBlock({
  entries,
  active,
}: {
  entries: TimelineEntry[]
  active: boolean
}) {
  if (entries.length === 0) return null

  const sortedEntries = entries.slice().sort((left, right) => left.timestamp - right.timestamp)

  return (
    <section className="mb-4 rounded-lg border border-[rgba(240,188,46,0.28)] bg-[var(--gold-soft)] px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 pb-2">
        <span className="rounded-full bg-white/75 px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--warning)]">
          Thinking
        </span>
        {active ? <span className="text-xs font-medium text-[var(--warning)]">live</span> : null}
      </div>
      <div className="max-h-[260px] space-y-3 overflow-auto pr-1">
        {sortedEntries.map((entry) => (
          <div key={entry.id} className="rounded-lg border border-[rgba(240,188,46,0.2)] bg-white/65 px-3 py-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
              <span className="font-semibold text-[var(--ink)]">{entry.agent || entry.title}</span>
              {entry.status ? <span>{entry.status}</span> : null}
              <span className="ml-auto">{formatTime(entry.timestamp)}</span>
            </div>
            {entry.body ? (
              <div className="pt-2">
                <MarkdownBlock content={entry.body} compact />
              </div>
            ) : (
              <p className="pt-2 text-sm text-[var(--muted)]">Reasoning started.</p>
            )}
          </div>
        ))}
      </div>
    </section>
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
        return 'bg-[var(--accent-soft)] text-[var(--accent)]'
      case 'thinking':
        return 'bg-[var(--gold-soft)] text-[var(--warning)]'
      case 'subsystem':
        return 'bg-[var(--blue-pale)] text-[var(--blue-dark)]'
      case 'error':
        return 'bg-[var(--danger-soft)] text-[var(--danger)]'
      case 'lifecycle':
        return 'bg-[var(--success-soft)] text-[var(--success)]'
      default:
        return 'bg-[rgba(94,107,120,0.12)] text-[var(--muted)]'
    }
  }

  return (
    <div className="space-y-3 pt-3">
      {sortedEntries.map((entry) => (
        <div key={entry.id} className="rounded-lg border border-[var(--line)] bg-white/60 px-4 py-4">
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
              <div className="max-h-[360px] overflow-auto pr-1">
                <MarkdownBlock content={entry.body} compact />
              </div>
            ) : entry.format === 'json' || entry.format === 'code' ? (
              <pre className="max-h-[360px] overflow-auto rounded-lg bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
                {entry.body}
              </pre>
            ) : (
              <p className="max-h-[360px] overflow-auto whitespace-pre-wrap pr-1 text-sm leading-6 text-[var(--ink)]">{entry.body}</p>
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
        await downloadHostedFile(attachment.hostedUri, attachment.name)
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
                className="flex w-full items-center justify-between rounded-lg border border-[var(--line)] bg-white/60 px-4 py-3 text-left transition hover:bg-white"
              >
                <div>
                  <div className="font-medium">{attachment.name}</div>
                  <div className="text-sm text-[var(--muted)]">
                    {typeof attachment.size === 'number' ? formatBytes(attachment.size) : attachment.mime || attachment.rn || 'Stored attachment'}
                  </div>
                </div>
                <span className="text-sm text-[var(--action)]">
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
                className="flex w-full items-center justify-between rounded-lg border border-[var(--line)] bg-white/60 px-4 py-3 text-left transition hover:bg-white"
              >
                <div>
                  <div className="font-medium">{file.filename}</div>
                  <div className="text-sm text-[var(--muted)]">{file.description || file.mime || file.rn}</div>
                </div>
                <span className="text-sm text-[var(--action)]">
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
            <div key={`${artifact.kind}-${artifact.name}`} className="rounded-lg border border-[rgba(217,229,99,0.34)] bg-[var(--accent-soft)] px-4 py-4">
              <div className="flex flex-wrap items-center gap-2 pb-2">
                <span className="rounded-full bg-white/75 px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--accent)]">
                  Live update
                </span>
                <span className="font-medium text-[var(--ink)]">{artifact.name}</span>
                <span className="ml-auto text-xs text-[var(--muted)]">{formatTime(artifact.timestamp)}</span>
              </div>
              <div className="max-h-[320px] overflow-auto pr-1">
                <MarkdownBlock content={artifact.markdown} />
              </div>
            </div>
          )
        }

        if (artifact.kind === 'canvas') {
          return (
            <details key={`${artifact.kind}-${artifact.name}`} className="rounded-lg border border-[var(--line)] bg-white/60 px-4 py-4">
              <summary className="cursor-pointer list-none font-medium">
                {artifact.title || artifact.name}
                <span className="pl-2 text-xs uppercase tracking-[0.12em] text-[var(--muted)]">
                  {artifact.format || 'text'}
                </span>
              </summary>
              <div className="pt-3">
                {artifact.format === 'markdown' ? (
                  <div className="max-h-[360px] overflow-auto pr-1">
                    <MarkdownBlock content={artifact.content} />
                  </div>
                ) : (
                  <pre className="max-h-[360px] overflow-auto rounded-lg bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
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
              className="block rounded-lg border border-[var(--line)] bg-white/65 px-4 py-4 transition hover:-translate-y-0.5 hover:border-[rgba(67,114,195,0.26)]"
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
            <div key={`${artifact.kind}-${artifact.rn}`} className="rounded-lg border border-[var(--line)] bg-white/65 px-4 py-4">
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
            <div key={`${artifact.kind}-${artifact.searchId}`} className="rounded-lg border border-[rgba(217,229,99,0.34)] bg-[var(--accent-soft)] px-4 py-4">
              <div className="flex flex-wrap items-center gap-2 pb-2">
                <span className="rounded-full bg-white/75 px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--accent)]">
                  Web Search
                </span>
                <span className="font-medium text-[var(--ink)]">{artifact.title || artifact.name}</span>
                <span className="text-xs text-[var(--muted)]">
                  {artifact.items.length} result{artifact.items.length === 1 ? '' : 's'}
                </span>
                <span className="ml-auto text-xs text-[var(--muted)]">{formatTime(artifact.timestamp)}</span>
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
                    className="block rounded-lg border border-[var(--line)] bg-[rgba(255,255,255,0.7)] px-3 py-3 transition hover:border-[rgba(67,114,195,0.26)]"
                  >
                    <div className="font-medium">{item.title || item.url}</div>
                    {item.body ? <p className="pt-1 text-sm text-[var(--muted)]">{item.body}</p> : null}
                  </a>
                ))}
              </div>
              {artifact.reportContent ? (
                <details className="pt-3">
                  <summary className="cursor-pointer text-sm font-medium text-[var(--action)]">
                    Show report
                  </summary>
                  <div className="max-h-[360px] overflow-auto pt-3 pr-1">
                    <MarkdownBlock content={artifact.reportContent} compact />
                  </div>
                </details>
              ) : null}
            </div>
          )
        }

        if (artifact.kind === 'web_fetch') {
          return (
            <div key={`${artifact.kind}-${artifact.executionId}`} className="rounded-lg border border-[rgba(240,188,46,0.3)] bg-[var(--gold-soft)] px-4 py-4">
              <div className="flex flex-wrap items-center gap-2 pb-2">
                <span className="rounded-full bg-white/75 px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--warning)]">
                  Web Fetch
                </span>
                <span className="font-medium text-[var(--ink)]">{artifact.title || artifact.name}</span>
                <span className="text-xs text-[var(--muted)]">
                  {artifact.items.length} URL{artifact.items.length === 1 ? '' : 's'}
                </span>
                <span className="ml-auto text-xs text-[var(--muted)]">{formatTime(artifact.timestamp)}</span>
              </div>
              <div className="space-y-2">
                {artifact.items.slice(0, 4).map((item) => (
                  <div key={item.url} className="rounded-lg border border-[var(--line)] bg-[rgba(255,255,255,0.7)] px-3 py-3">
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
            <article key={`${artifact.kind}-${artifact.executionId}`} className="rounded-lg border border-[rgba(24,42,58,0.14)] bg-white/70 px-4 py-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-[rgba(22,35,47,0.08)] px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--ink)]">
                  Exec
                </span>
                <span className="font-medium">{artifact.title || artifact.name || 'Program'}</span>
                <span className="text-sm text-[var(--muted)]">{statusLabel}</span>
                <span className="ml-auto text-xs text-[var(--muted)]">{formatTime(artifact.timestamp)}</span>
              </div>
              {artifact.name && artifact.title && artifact.name !== artifact.title ? (
                <p className="pt-2 text-sm font-medium text-[var(--ink)]">{artifact.name}</p>
              ) : null}
              {artifact.objective ? <p className="pt-3 text-sm text-[var(--muted)]">{artifact.objective}</p> : null}
              {artifact.contract && artifact.contract.length > 0 ? (
                <div className="space-y-1 pt-3 text-sm">
                  {artifact.contract.map((item) => (
                    <div key={item.filename} className="rounded-lg bg-[rgba(17,32,43,0.05)] px-3 py-2">
                      <span className="font-medium">{item.filename}</span>
                      {item.description ? <span className="text-[var(--muted)]"> • {item.description}</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {artifact.program ? (
                <div className="pt-3">
                  <pre className="max-h-[360px] overflow-auto rounded-lg bg-[#11202b] px-4 py-3 text-sm text-[#edf5f6]">
                    {artifact.program}
                  </pre>
                </div>
              ) : null}
              {artifact.status?.status === 'error' && artifact.status.error ? (
                <div className="pt-3 text-sm text-[var(--danger)]">
                  {Object.values(artifact.status.error).join(' ')}
                </div>
              ) : null}
            </article>
          )
        }

        return (
          <div key={`${artifact.kind}-${artifact.timestamp}`} className="rounded-lg border border-[rgba(247,96,154,0.28)] bg-[var(--danger-soft)] px-4 py-4 text-[var(--danger)]">
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
  const turnLinks = useMemo(() => collectTurnLinks(turn.artifacts), [turn.artifacts])
  const thinkingEntries = useMemo(
    () => turn.timeline.filter((entry) => entry.kind === 'thinking'),
    [turn.timeline],
  )
  const overviewArtifacts = useMemo(
    () => turn.artifacts,
    [turn.artifacts],
  )

  return (
    <article className="glass-panel rounded-lg px-5 py-5">
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
          {turn.additionalUserMessages.length > 0 ? (
            <div className="space-y-2 pt-4">
              {turn.additionalUserMessages.map((message) => (
                <div
                  key={message.id}
                  className="rounded-lg border border-[rgba(217,229,99,0.32)] bg-[var(--accent-soft)] px-3 py-3"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
                    <span className="rounded-full bg-white/75 px-2 py-0.5 font-semibold uppercase tracking-[0.08em] text-[var(--accent)]">
                      {message.continuationKind === 'steer' ? 'Steer' : 'Follow-up'}
                    </span>
                    <span>{formatTime(message.timestamp)}</span>
                  </div>
                  <p className="pt-2 whitespace-pre-wrap text-sm leading-6 text-[var(--ink)]">
                    {message.text || 'Stop requested'}
                  </p>
                  {message.attachments.length > 0 ? (
                    <div className="flex flex-wrap gap-2 pt-2">
                      {message.attachments.map((attachment) => (
                        <span
                          key={attachment.id}
                          className="rounded-full border border-[rgba(24,42,58,0.12)] bg-white/65 px-3 py-1 text-xs text-[var(--muted)]"
                        >
                          {attachment.name}
                          {typeof attachment.size === 'number' ? ` • ${formatBytes(attachment.size)}` : ''}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] ${
            turn.state === 'error'
              ? 'bg-[var(--danger-soft)] text-[var(--danger)]'
              : turn.state === 'completed'
                ? 'bg-[var(--success-soft)] text-[var(--success)]'
                : 'bg-[var(--accent-soft)] text-[var(--accent)]'
          }`}
        >
          {turn.state}
        </span>
      </div>

      <div className="mt-5 rounded-lg border border-[var(--line)] bg-[var(--paper-strong)] px-4 py-4">
        <div className="pb-4 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
          Assistant
        </div>

        <div className="flex flex-wrap gap-2 border-b border-[var(--line)] pb-3">
          {([
            ['overview', 'Overview'],
            ['timeline', `Timeline${turn.timeline.length ? ` (${turn.timeline.length})` : ''}`],
            ['steps', `Steps${steps.length ? ` (${steps.length})` : ''}`],
            ['links', `Links${turnLinks.length ? ` (${turnLinks.length})` : ''}`],
            ['files', `Files${turn.userAttachments.length + assistantFiles.length ? ` (${turn.userAttachments.length + assistantFiles.length})` : ''}`],
          ] as Array<[TurnTab, string]>).map(([tab, label]) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`rounded-md px-3 py-1.5 text-sm transition ${
                activeTab === tab
                  ? 'bg-[var(--action)] text-white'
                  : 'border border-[var(--line)] bg-white/70 text-[var(--ink)] hover:bg-white'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {activeTab === 'overview' ? (
          <>
            <ThinkingBlock entries={thinkingEntries} active={turn.state === 'pending' || turn.state === 'running'} />
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
        {activeTab === 'links' ? <LinksPanel links={turnLinks} /> : null}
        {activeTab === 'files' ? (
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
  inProgress,
  lockedMessage,
  onTextChange,
  onFilesAdd,
  onFileRemove,
  onSubmit,
  onStop,
}: {
  text: string
  files: File[]
  disabled: boolean
  inProgress: boolean
  lockedMessage: string | null
  onTextChange: (value: string) => void
  onFilesAdd: (files: FileList | null) => void
  onFileRemove: (index: number) => void
  onSubmit: () => void
  onStop: () => void
}) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-white/70 px-3 py-3">
      {lockedMessage ? (
        <div className="mb-3 rounded-lg border border-[rgba(240,188,46,0.34)] bg-[var(--gold-soft)] px-3 py-2 text-sm text-[var(--warning)]">
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

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="inline-flex cursor-pointer items-center rounded-md border border-[rgba(24,42,58,0.12)] bg-white/70 px-3 py-1.5 text-sm font-medium text-[var(--ink)] transition hover:bg-white">
          Attach files
          <input type="file" multiple className="hidden" disabled={disabled} onChange={(event) => onFilesAdd(event.target.files)} />
        </label>
        {inProgress ? (
          <button
            type="button"
            disabled={disabled}
            onClick={onStop}
            className="inline-flex items-center rounded-md border border-[rgba(247,96,154,0.26)] bg-[var(--danger-soft)] px-3 py-1.5 text-sm font-medium text-[var(--danger)] transition hover:bg-[rgba(247,96,154,0.18)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            Stop
          </button>
        ) : null}
      </div>

      <div className="grid gap-3 md:grid-cols-[1fr_auto]">
        <textarea
          value={text}
          disabled={disabled}
          onChange={(event) => onTextChange(event.target.value)}
          placeholder={
            inProgress
              ? 'Send a follow-up while the current turn is still running.'
              : 'Ask anything. This view supports attachments, streaming responses, followups, and tool outputs.'
          }
          rows={4}
          className="min-h-[96px] rounded-md border border-[var(--line)] bg-white/80 px-3 py-2.5 text-[15px] leading-7 shadow-[inset_0_1px_1px_rgba(0,0,0,0.02)] outline-none transition placeholder:text-[var(--muted)] focus:border-[var(--action)] disabled:cursor-not-allowed disabled:opacity-60"
        />
        <button
          type="button"
          disabled={disabled || (!text.trim() && files.length === 0)}
          onClick={onSubmit}
          className="h-fit rounded-md bg-[var(--action)] px-4 py-2.5 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:bg-[var(--action-dark)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {inProgress ? 'Follow up' : 'Send'}
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
  const autoScrollRef = useRef(true)

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    const updateAutoScroll = () => {
      const doc = document.documentElement
      const scrollTop = window.scrollY || doc.scrollTop || 0
      const remaining = doc.scrollHeight - (scrollTop + window.innerHeight)
      autoScrollRef.current = remaining < 140
    }

    updateAutoScroll()
    window.addEventListener('scroll', updateAutoScroll, { passive: true })
    window.addEventListener('resize', updateAutoScroll)
    return () => {
      window.removeEventListener('scroll', updateAutoScroll)
      window.removeEventListener('resize', updateAutoScroll)
    }
  }, [])

  useEffect(() => {
    if (!autoScrollRef.current) return
    bottomRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
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

  const refreshConversationList = async () => {
    if (!bundleId) return

    setState((previous) => ({
      ...previous,
      conversationsLoading: true,
      conversationsError: null,
    }))

    try {
      const conversations = await listBundleConversations(bundleId)
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

  const requestConversationStatusForCurrentStream = async (conversationId: string) => {
    const streamId = streamIdRef.current
    if (!streamId) return
    try {
      await requestConversationStatus(conversationId, streamId)
    } catch (error) {
      console.warn('Unable to request conversation status', error)
    }
  }

  const loadConversation = async (conversationId: string) => {
    setState((previous) => ({
      ...previous,
      conversationLoadingId: conversationId,
      inputLocked: false,
      inputLockMessage: null,
    }))

    try {
      const conversation = await fetchConversationById(conversationId)
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
        void requestConversationStatusForCurrentStream(conversation.conversation_id)
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

  const handleServiceEvent = (env: ChatServiceEnvelope) => {
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
      const transport = await openChatStream({
        sessionId: sessionIdRef.current,
        onChatStart: (env) => {
          setState((previous) => applyChatStart(previous, env))
        },
        onChatStep: (env) => {
          setState((previous) => applyChatStep(previous, env))
        },
        onChatDelta: (env) => {
          setState((previous) => applyChatDelta(previous, env))
        },
        onChatComplete: (env) => {
          setState((previous) => applyChatComplete(previous, env))
          void refreshConversationList()
        },
        onChatError: (env) => {
          setState((previous) => applyChatError(previous, env))
        },
        onConversationStatus: (env) => {
          setState((previous) => applyConvStatus(previous, env))
        },
        onChatService: handleServiceEvent,
        onDisconnect: () => {
          setState((previous) => ({ ...previous, connection: 'disconnected' }))
        },
      })

      eventSourceRef.current = transport.eventSource
      streamIdRef.current = transport.streamId
      sessionIdRef.current = transport.sessionId
      setState((previous) => ({
        ...previous,
        connection: 'connected',
        sessionId: transport.sessionId,
      }))
      if (stateRef.current.conversationId) {
        void requestConversationStatusForCurrentStream(stateRef.current.conversationId)
      }
    })()

    try {
      await connectPromiseRef.current
    } catch (error) {
      resetTransport()
      setState((previous) => ({ ...previous, connection: 'disconnected' }))
      throw error
    } finally {
      connectPromiseRef.current = null
    }
  }

  const sendMessage = async (textOverride?: string, requestedKind?: ContinuationKind) => {
    const snapshot = stateRef.current
    const activeTurn = findActiveTurn(snapshot.turns)
    let continuationKind: ContinuationKind = requestedKind ?? (activeTurn ? 'followup' : 'regular')
    if (continuationKind !== 'regular' && !activeTurn) {
      continuationKind = 'regular'
    }
    const isContinuation = continuationKind === 'followup' || continuationKind === 'steer'
    const isSteer = continuationKind === 'steer'
    const continuationMessageKind: Exclude<ContinuationKind, 'regular'> =
      continuationKind === 'steer' ? 'steer' : 'followup'
    const targetTurnId = isContinuation ? activeTurn?.id : undefined
    const draftText = (textOverride ?? snapshot.composerText).trim()
    const draftFiles = isSteer || textOverride !== undefined ? [] : snapshot.composerFiles
    if (!draftText && draftFiles.length === 0 && !isSteer) return

    const turnId = createLocalId('turn')
    const sentAt = Date.now()
    const existingConversationId = snapshot.conversationId
    const draftAttachments = draftFiles.map((file, index) =>
      normalizeTurnAttachment(
        {
          filename: file.name,
          size: file.size,
          mime: file.type,
        },
        `live:${turnId}:${index}`,
        file,
      ),
    )

    if (isContinuation && targetTurnId) {
      setState((previous) => ({
        ...previous,
        composerText: '',
        composerFiles: [],
        turns: previous.turns.map((turn) => {
          if (turn.id !== targetTurnId) return turn
          return {
            ...turn,
            additionalUserMessages: [
              ...turn.additionalUserMessages,
              {
                id: `continuation:${turnId}`,
                text: draftText,
                timestamp: sentAt,
                attachments: draftAttachments,
                continuationKind: continuationMessageKind,
              },
            ],
          }
        }),
      }))
    } else {
      setState((previous) => ({
        ...previous,
        composerText: '',
        composerFiles: [],
        turns: [
          ...previous.turns,
          {
            id: turnId,
            state: 'pending',
            createdAt: sentAt,
            userMessage: draftText,
            userAttachments: draftAttachments,
            additionalUserMessages: [],
            answer: '',
            error: null,
            steps: {},
            artifacts: [],
            timeline: [],
            followups: [],
          },
        ],
      }))
    }

    try {
      await connectStream()
      const streamId = streamIdRef.current
      if (!streamId) {
        throw new Error('No SSE stream is available.')
      }
      const response = await submitChatMessage({
        streamId,
        bundleId,
        conversationId: existingConversationId,
        turnId,
        text: draftText,
        files: draftFiles,
        chatHistory: isContinuation ? [] : buildChatHistory(snapshot.turns),
        ...(isContinuation
          ? {
              messageKind: continuationKind,
              continuationKind,
              activeTurnId: targetTurnId,
              targetTurnId,
              followup: continuationKind === 'followup',
              steer: continuationKind === 'steer',
            }
          : {}),
      })
      setState((previous) => {
        const stillOwnsTurn = isContinuation
          ? previous.turns.some((turn) => turn.id === targetTurnId)
          : previous.turns.some((turn) => turn.id === turnId)
        const canBindConversation =
          !previous.conversationId ||
          previous.conversationId === existingConversationId ||
          previous.conversationId === response.conversationId
        if (!stillOwnsTurn || !canBindConversation) return previous
        let next: ChatState = {
          ...previous,
          conversationId: response.conversationId,
        }
        const ackStatus = typeof response.status === 'string' ? response.status : null
        const continuationAccepted = ackStatus === 'followup_accepted' || ackStatus === 'steer_accepted'
        const continuationStartedNewTurn = isContinuation && !!ackStatus && !continuationAccepted
        if (continuationStartedNewTurn && !next.turns.some((turn) => turn.id === turnId)) {
          next = {
            ...next,
            turns: [
              ...next.turns,
              {
                id: turnId,
                state: 'pending',
                createdAt: sentAt,
                userMessage: draftText,
                userAttachments: draftAttachments,
                additionalUserMessages: [],
                answer: '',
                error: null,
                steps: {},
                artifacts: [],
                timeline: [],
                followups: [],
              },
            ],
          }
        }
        return next
      })
      void refreshConversationList()
    } catch (error) {
      const text = messageForError(error)
      const errorTurnId = isContinuation && targetTurnId ? targetTurnId : turnId
      setState((previous) => applyChatError(previous, {
        type: 'chat.error',
        timestamp: new Date().toISOString(),
        service: { request_id: createLocalId('request') },
        conversation: {
          session_id: previous.sessionId || '',
          conversation_id: existingConversationId || previous.conversationId || '',
          turn_id: errorTurnId,
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
        <div className="glass-panel rounded-lg px-8 py-7 text-center">
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">
            Versatile
          </div>
          <div className="pt-3 text-lg font-medium">Connecting application config...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="shell-grid">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-4 sm:px-6 lg:px-8">
        <header className="px-1 py-2">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">
                Versatile
              </div>
              <h1 className="pt-1 text-xl font-semibold tracking-tight text-[var(--ink)]">
                Application chat workspace
              </h1>
              <p className="pt-1 max-w-3xl text-sm leading-6 text-[var(--muted)]">
                Application: <span className="font-medium text-[var(--ink)]">{bundleId}</span>
                {' '}•{' '}
                Scope: <span className="font-medium text-[var(--ink)]">{settings.getTenant() || '(tenant)'}</span>
                {' / '}
                <span className="font-medium text-[var(--ink)]">{settings.getProject() || '(project)'}</span>
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] ${
                  state.connection === 'connected'
                    ? 'bg-[var(--success-soft)] text-[var(--success)]'
                    : state.connection === 'disconnected'
                      ? 'bg-[var(--danger-soft)] text-[var(--danger)]'
                      : 'bg-[var(--accent-soft)] text-[var(--accent)]'
                }`}
              >
                {state.connection}
              </span>
              <button
                type="button"
                onClick={startNewChat}
                disabled={hasPendingTurn}
                className="rounded-md border border-[var(--line)] bg-white/70 px-3 py-1.5 text-sm font-medium transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                New chat
              </button>
              <button
                type="button"
                onClick={handleReconnect}
                className="rounded-md border border-[var(--line)] bg-white/70 px-3 py-1.5 text-sm font-medium transition hover:bg-white"
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

        <main className="flex-1 pt-3">
          <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
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

            <div className="glass-panel min-w-0 overflow-hidden rounded-lg">
              <section className="border-b border-[var(--line)] px-4 py-3">
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">
                      {state.conversationId ? 'Selected chat' : 'New chat'}
                    </div>
                    <div className="truncate pt-1 text-lg font-semibold text-[var(--ink)]">
                      {state.conversationTitle || (state.conversationId ? 'Untitled conversation' : 'Start a conversation')}
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

              <div className="px-4 py-4">
                {state.turns.length === 0 ? (
                  <section className="rounded-lg border border-dashed border-[var(--line-strong)] bg-white/55 px-3 py-3">
                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                      <div className="min-w-0">
                        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
                          Start
                        </div>
                        <h2 className="pt-1 text-base font-semibold tracking-tight text-[var(--ink)]">
                        Open a focused chat
                        </h2>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {[
                          'Summarize the last attachment as markdown',
                          'Search the web and cite three sources about React compiler',
                          'Run an exec tool to generate a small report',
                        ].map((prompt) => (
                          <button
                            key={prompt}
                            type="button"
                            className="rounded-md border border-[rgba(67,114,195,0.24)] bg-[var(--action-soft)] px-3 py-1.5 text-sm text-[var(--action)] transition hover:bg-[rgba(67,114,195,0.16)]"
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
                        sendingDisabled={state.inputLocked || state.connection === 'booting'}
                        onDownloadError={(text) =>
                          setState((previous) => addBanner(previous, 'error', `Download failed: ${text}`))
                        }
                        onFollowup={(text) => {
                          if (state.inputLocked || state.connection === 'booting') return
                          void sendMessage(text, hasPendingTurn ? 'followup' : 'regular')
                        }}
                      />
                    ))}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              <div className="border-t border-[var(--line)] bg-[rgba(255,255,255,0.52)] px-4 py-4">
                <Composer
                  text={state.composerText}
                  files={state.composerFiles}
                  disabled={state.inputLocked || state.connection === 'booting'}
                  inProgress={hasPendingTurn}
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
                    if (state.inputLocked || state.connection === 'booting') return
                    void sendMessage(undefined, hasPendingTurn ? 'followup' : 'regular')
                  }}
                  onStop={() => {
                    if (state.inputLocked || state.connection === 'booting') return
                    void sendMessage('', 'steer')
                  }}
                />
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
