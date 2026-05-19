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
import { BUILT_BUNDLE_ID, createLocalId, createTurnId, settings } from './settings'

type ConnectionState = 'booting' | 'connecting' | 'connected' | 'disconnected'
type TurnState = 'pending' | 'running' | 'completed' | 'error'
type TurnTab = 'overview' | 'timeline' | 'steps' | 'links' | 'files' | 'canvases'

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
    <div className={`markdown-body ${compact ? 'text-[13px]' : 'text-[14px]'}`}>
      <ReactMarkdown
        remarkPlugins={markdownPlugins}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          p: ({ children }) => (
            <p className={compact ? 'my-1 leading-5' : 'my-2 leading-6'}>{children}</p>
          ),
          ul: ({ children }) => <ul className={compact ? 'my-1 list-disc pl-5' : 'my-2 list-disc pl-5'}>{children}</ul>,
          ol: ({ children }) => <ol className={compact ? 'my-1 list-decimal pl-5' : 'my-2 list-decimal pl-5'}>{children}</ol>,
          li: ({ children }) => <li className="my-0.5">{children}</li>,
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
  const noticeClass = (tone: BannerTone) => {
    switch (tone) {
      case 'error':
        return 'k-notice k-error'
      case 'warning':
        return 'k-notice k-warning'
      case 'success':
        return 'k-notice k-success'
      default:
        return 'k-notice k-info'
    }
  }
  return (
    <div className="flex flex-col gap-2">
      {banners.map((banner) => (
        <div key={banner.id} className={noticeClass(banner.tone)}>
          <div className="min-w-0 flex-1">{banner.text}</div>
          <button
            type="button"
            className="k-iconbtn k-borderless"
            onClick={() => onDismiss(banner.id)}
            aria-label="Dismiss"
            title="Dismiss"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
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
    <aside className="glass-panel flex min-h-[520px] flex-col overflow-hidden lg:sticky lg:top-4">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--line-soft)] px-3 py-2">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-[var(--ink)]">Chats</div>
          <div className="text-[11px] text-[var(--muted)]">Bundle conversations</div>
        </div>
        <button
          type="button"
          onClick={onStartNew}
          disabled={disabled}
          className="k-iconbtn"
          aria-label="New chat"
          title="New chat"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>

      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--line-soft)]">
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search chats"
          disabled={disabled}
          className="k-input"
        />
        <button
          type="button"
          onClick={onRefresh}
          className="k-iconbtn"
          aria-label="Refresh"
          title="Refresh"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 12a9 9 0 1 1-3-6.7" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
      </div>

      {error ? (
        <div className="px-3 pt-3">
          <div className="k-notice k-error">
            <span>{error}</span>
          </div>
        </div>
      ) : null}

      {loading && conversations.length === 0 ? (
        <p className="px-3 py-3 text-[12px] text-[var(--muted)]">Loading conversations…</p>
      ) : null}

      {!loading && conversations.length === 0 ? (
        <p className="px-3 py-3 text-[12px] leading-5 text-[var(--muted)]">
          {query.trim()
            ? 'No chats match the current search.'
            : 'No saved chats yet. Start a new one and it will appear here.'}
        </p>
      ) : null}

      {conversations.length > 0 ? (
        <div className="k-rows min-w-0 flex-1 overflow-auto">
          {conversations.map((conversation) => {
            const isActive = conversation.id === activeConversationId
            const isLoading = loadingConversationId === conversation.id
            return (
              <button
                key={conversation.id}
                type="button"
                onClick={() => onSelect(conversation.id)}
                disabled={disabled || isLoading}
                className={`k-row ${isActive ? 'k-active' : ''}`}
              >
                <div className="k-row-main">
                  <div className="k-row-title">
                    {conversation.title || 'Untitled conversation'}
                  </div>
                  <div className="k-row-sub">
                    {formatConversationTime(conversation.lastActivityAt || conversation.startedAt)}
                    {isLoading ? ' · loading…' : ''}
                  </div>
                </div>
                {isActive ? <span className="k-chip k-teal">open</span> : null}
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
    <div className="flex flex-wrap gap-1.5 pt-2">
      {items.map((item) => (
        <button
          key={item}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(item)}
          className="k-followup"
        >
          {item}
        </button>
      ))}
    </div>
  )
}

function StepList({ steps }: { steps: TurnStep[] }) {
  if (steps.length === 0) return null
  const statusChip = (status: StepStatus) => {
    switch (status) {
      case 'completed':
        return 'k-chip k-green'
      case 'error':
        return 'k-chip k-pink'
      case 'started':
        return 'k-chip k-teal'
      default:
        return 'k-chip'
    }
  }
  return (
    <div className="flex flex-col gap-1.5 pt-1">
      {steps.map((step) => {
        const hasBody = Boolean(
          step.markdown || (typeof step.data?.message === 'string') || step.error,
        )
        return (
          <div key={step.step} className="k-workitem">
            <div className="k-workitem-head">
              <span className="k-workitem-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M9 12l2 2 4-4" />
                </svg>
              </span>
              <span className="k-workitem-title">
                <span className="k-text">{step.title || step.step}</span>
                <span className={statusChip(step.status)}>{step.status}</span>
                {step.agent ? <span className="k-micro">{step.agent}</span> : null}
              </span>
            </div>
            {hasBody ? (
              <div className="k-workitem-body">
                {step.markdown ? <MarkdownBlock content={step.markdown} compact /> : null}
                {!step.markdown && typeof step.data?.message === 'string' ? (
                  <p className="text-[12px] text-[var(--muted)]">{step.data.message}</p>
                ) : null}
                {step.error ? (
                  <p className="text-[12px] text-[var(--pink-dark)]">{step.error}</p>
                ) : null}
              </div>
            ) : null}
          </div>
        )
      })}
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
    return <p className="pt-2 text-[12px] text-[var(--muted)]">No links have been produced for this turn yet.</p>
  }

  const linkChip = (kind: TurnLink['kind']) => {
    switch (kind) {
      case 'web_search':
        return 'k-chip k-teal'
      case 'web_fetch':
        return 'k-chip k-gold'
      default:
        return 'k-chip k-blue'
    }
  }

  return (
    <div className="k-result-list mt-1">
      {links.map((link) => (
        <a
          key={link.id}
          href={link.url}
          target="_blank"
          rel="noreferrer"
          className="k-result-row"
        >
          <span className="k-result-favicon" aria-hidden="true" />
          <div className="k-result-main">
            <span className="k-result-title">{link.title}</span>
            <span className="k-result-host">{shortUrl(link.url)}</span>
            {link.body ? <span className="k-result-body">{link.body}</span> : null}
          </div>
          <span className={linkChip(link.kind)}>{link.kind.replace('_', ' ')}</span>
        </a>
      ))}
    </div>
  )
}

/* Minimal token highlighter — Python/JS/TS/Bash/JSON. No external deps.
   Recognises strings, comments, numbers, decorators, keywords, builtins,
   function-call names. Anything else stays default colour. */
const HL_KEYWORDS: Record<string, Set<string>> = {
  python: new Set([
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break',
    'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally',
    'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal',
    'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
    'match', 'case',
  ]),
  javascript: new Set([
    'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue',
    'debugger', 'default', 'delete', 'do', 'else', 'export', 'extends',
    'finally', 'for', 'function', 'if', 'import', 'in', 'instanceof', 'let',
    'new', 'null', 'of', 'return', 'super', 'switch', 'this', 'throw', 'true',
    'false', 'try', 'typeof', 'undefined', 'var', 'void', 'while', 'yield',
  ]),
  bash: new Set([
    'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'do', 'done', 'case',
    'esac', 'function', 'in', 'select', 'until', 'return', 'export', 'local',
    'readonly', 'set', 'unset', 'echo', 'cd', 'pwd', 'source',
  ]),
  json: new Set(['true', 'false', 'null']),
}

const HL_BUILTINS: Record<string, Set<string>> = {
  python: new Set([
    'print', 'len', 'range', 'list', 'dict', 'set', 'tuple', 'str', 'int',
    'float', 'bool', 'bytes', 'open', 'isinstance', 'type', 'super',
    'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed', 'any', 'all',
    'min', 'max', 'sum', 'abs', 'round', 'hash', 'id', '__init__', 'self',
    'cls', 'Path',
  ]),
  javascript: new Set([
    'console', 'window', 'document', 'Math', 'JSON', 'Object', 'Array',
    'String', 'Number', 'Boolean', 'Promise', 'Map', 'Set', 'Date',
    'Error', 'parseInt', 'parseFloat',
  ]),
}

function inferLanguage(hint: string | null | undefined, code: string): keyof typeof HL_KEYWORDS {
  const h = String(hint || '').toLowerCase()
  if (h.startsWith('py')) return 'python'
  if (h === 'js' || h === 'jsx' || h === 'ts' || h === 'tsx' || h === 'javascript' || h === 'typescript') return 'javascript'
  if (h === 'sh' || h === 'bash' || h === 'shell') return 'bash'
  if (h === 'json') return 'json'
  const sample = code.slice(0, 240)
  if (/^\s*(def |class |import |from |if __name__)/m.test(sample)) return 'python'
  if (/^\s*(const |let |var |function |export |import )/m.test(sample)) return 'javascript'
  if (/^\s*(#!\/|echo |cd |export )/m.test(sample)) return 'bash'
  return 'python'
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function highlightCode(code: string, lang: keyof typeof HL_KEYWORDS): string {
  if (!code) return ''
  const keywords = HL_KEYWORDS[lang] || new Set<string>()
  const builtins = HL_BUILTINS[lang] || new Set<string>()
  const tokens: Array<{ kind: string; text: string }> = []
  let index = 0
  const length = code.length

  const isPython = lang === 'python'
  const isBash = lang === 'bash'
  const isJs = lang === 'javascript'

  while (index < length) {
    const char = code[index]

    // Comments
    if (isPython && char === '#') {
      const end = code.indexOf('\n', index)
      const stop = end === -1 ? length : end
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (isBash && char === '#') {
      const end = code.indexOf('\n', index)
      const stop = end === -1 ? length : end
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (isJs && char === '/' && code[index + 1] === '/') {
      const end = code.indexOf('\n', index)
      const stop = end === -1 ? length : end
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (isJs && char === '/' && code[index + 1] === '*') {
      const end = code.indexOf('*/', index + 2)
      const stop = end === -1 ? length : end + 2
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Strings (single, double, triple-quoted python, template literals)
    if (isPython && (code.startsWith('"""', index) || code.startsWith("'''", index))) {
      const quote = code.slice(index, index + 3)
      const end = code.indexOf(quote, index + 3)
      const stop = end === -1 ? length : end + 3
      tokens.push({ kind: 's', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (char === '"' || char === "'" || (isJs && char === '`')) {
      const quote = char
      let stop = index + 1
      while (stop < length) {
        if (code[stop] === '\\') { stop += 2; continue }
        if (code[stop] === quote) { stop += 1; break }
        if (code[stop] === '\n' && quote !== '`') { break }
        stop += 1
      }
      tokens.push({ kind: 's', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Decorators (Python)
    if (isPython && char === '@' && /[A-Za-z_]/.test(code[index + 1] || '')) {
      let stop = index + 1
      while (stop < length && /[A-Za-z0-9_.]/.test(code[stop])) stop += 1
      tokens.push({ kind: 'd', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Numbers
    if (/[0-9]/.test(char)) {
      let stop = index + 1
      while (stop < length && /[0-9._eExXa-fA-F]/.test(code[stop])) stop += 1
      tokens.push({ kind: 'n', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Identifiers / keywords / builtins / function calls
    if (/[A-Za-z_$]/.test(char)) {
      let stop = index + 1
      while (stop < length && /[A-Za-z0-9_$]/.test(code[stop])) stop += 1
      const word = code.slice(index, stop)
      if (keywords.has(word)) {
        tokens.push({ kind: 'k', text: word })
      } else if (builtins.has(word)) {
        tokens.push({ kind: 'b', text: word })
      } else if (code[stop] === '(') {
        tokens.push({ kind: 'f', text: word })
      } else {
        tokens.push({ kind: 'o', text: word })
      }
      index = stop
      continue
    }

    // Default — accumulate until next interesting char
    let stop = index + 1
    while (
      stop < length &&
      !/[A-Za-z_$0-9"'`#]/.test(code[stop]) &&
      !(isJs && code[stop] === '/' && (code[stop + 1] === '/' || code[stop + 1] === '*')) &&
      !(isPython && code[stop] === '@')
    ) {
      stop += 1
    }
    tokens.push({ kind: 'plain', text: code.slice(index, stop) })
    index = stop
  }

  return tokens
    .map((token) => {
      const safe = escapeHtml(token.text)
      if (token.kind === 'plain' || token.kind === 'o') return safe
      return `<span class="tok-${token.kind}">${safe}</span>`
    })
    .join('')
}

function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
  // Fallback for non-secure contexts
  return new Promise((resolve, reject) => {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      resolve()
    } catch (error) {
      reject(error)
    }
  })
}

function CopyButton({ value, title = 'Copy' }: { value: string; title?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="k-tinybtn"
      title={title}
      data-flash={done ? 'true' : undefined}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        void copyToClipboard(value).then(() => {
          setDone(true)
          window.setTimeout(() => setDone(false), 1200)
        })
      }}
    >
      {done ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
          <path d="M5 12l4 4 10-10" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      )}
    </button>
  )
}

function DownloadButton({
  data,
  filename,
  mime = 'text/plain',
  title = 'Download',
}: {
  data: string
  filename: string
  mime?: string
  title?: string
}) {
  return (
    <button
      type="button"
      className="k-tinybtn"
      title={title}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        try {
          const blob = new Blob([data], { type: mime })
          const url = URL.createObjectURL(blob)
          const anchor = document.createElement('a')
          anchor.href = url
          anchor.download = filename
          anchor.style.display = 'none'
          document.body.appendChild(anchor)
          anchor.click()
          window.setTimeout(() => {
            URL.revokeObjectURL(url)
            document.body.removeChild(anchor)
          }, 0)
        } catch (error) {
          console.warn('Download failed', error)
        }
      }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
      </svg>
    </button>
  )
}

interface SnippetProps {
  content: string
  format: 'markdown' | 'code' | 'json' | 'text'
  language?: keyof typeof HL_KEYWORDS
  label?: string
  filename?: string
  downloadMime?: string
  showCopy?: boolean
  showDownload?: boolean
  maxHeight?: number
}

function Snippet({
  content,
  format,
  language,
  label,
  filename,
  downloadMime,
  showCopy = true,
  showDownload = false,
  maxHeight,
}: SnippetProps) {
  const isCodeFamily = format === 'code' || format === 'json'
  const lang = language || (format === 'json' ? 'json' : inferLanguage(null, content))
  const html = isCodeFamily ? highlightCode(content, lang) : null
  const labelText = label || (isCodeFamily ? lang : format)

  return (
    <div className={`k-snippet ${isCodeFamily ? 'k-snippet-dark' : ''}`}>
      <div className="k-snippet-head">
        <span className={`k-snippet-label ${isCodeFamily ? 'k-mono' : ''}`}>{labelText}</span>
        <span className="k-snippet-tools">
          {showCopy ? <CopyButton value={content} /> : null}
          {showDownload && filename ? (
            <DownloadButton data={content} filename={filename} mime={downloadMime} />
          ) : null}
        </span>
      </div>
      {format === 'markdown' ? (
        <div
          className="k-snippet-body"
          style={maxHeight ? { maxHeight: `${maxHeight}px` } : undefined}
        >
          <MarkdownBlock content={content} compact />
        </div>
      ) : isCodeFamily ? (
        <pre
          className="k-snippet-body k-snippet-pre"
          style={maxHeight ? { maxHeight: `${maxHeight}px` } : undefined}
          dangerouslySetInnerHTML={{ __html: html || '' }}
        />
      ) : (
        <pre
          className="k-snippet-body k-snippet-pre k-snippet-wrap"
          style={maxHeight ? { maxHeight: `${maxHeight}px` } : undefined}
        >
          {content}
        </pre>
      )}
    </div>
  )
}

function canvasFilename(canvas: CanvasArtifact): string {
  const base = canvas.name || canvas.title || 'canvas'
  // strip path-y parts the agent might emit
  const trimmed = String(base).split('/').pop() || base
  const format = String(canvas.format || '').toLowerCase()
  const ext = format === 'markdown' || format === 'md' ? 'md'
    : format === 'html' || format === 'srcdoc' ? 'html'
    : format === 'json' ? 'json'
    : format === 'csv' ? 'csv'
    : format === 'python' || format === 'py' ? 'py'
    : format === 'javascript' || format === 'js' ? 'js'
    : format === 'bash' || format === 'shell' || format === 'sh' ? 'sh'
    : format === 'text' || !format ? 'txt'
    : format
  return trimmed.includes('.') ? trimmed : `${trimmed}.${ext}`
}

function canvasMime(canvas: CanvasArtifact): string {
  const format = String(canvas.format || '').toLowerCase()
  if (format === 'html' || format === 'srcdoc') return 'text/html'
  if (format === 'markdown' || format === 'md') return 'text/markdown'
  if (format === 'json') return 'application/json'
  if (format === 'csv') return 'text/csv'
  return 'text/plain'
}

/* Canvas content renderer — picks markdown / html-iframe / code-with-highlight
   based on the canvas format. Falls back to plain pre-text for unknown types. */
function CanvasRender({ canvas }: { canvas: CanvasArtifact }) {
  const format = String(canvas.format || '').toLowerCase()
  const content = canvas.content || ''

  if (format === 'html' || format === 'srcdoc') {
    return (
      <iframe
        className="k-canvas-frame"
        srcDoc={content}
        sandbox="allow-same-origin"
        title={canvas.title || canvas.name}
      />
    )
  }

  if (format === 'markdown' || format === 'md') {
    return (
      <div className="k-canvas-markdown markdown-body">
        <ReactMarkdown
          remarkPlugins={markdownPlugins}
          components={{
            a: ({ children, href }) => (
              <a href={href} target="_blank" rel="noreferrer">{children}</a>
            ),
          }}
        >
          {closeStreamingMarkdown(content)}
        </ReactMarkdown>
      </div>
    )
  }

  return (
    <Snippet
      content={content}
      format={format === 'json' ? 'json' : 'code'}
      language={inferLanguage(format, content)}
      label={format || inferLanguage(format, content)}
    />
  )
}

function CanvasPanel({ canvases }: { canvases: CanvasArtifact[] }) {
  if (canvases.length === 0) {
    return <p className="pt-2 text-[12px] text-[var(--muted)]">No canvas items in this turn yet.</p>
  }
  return (
    <div className="flex flex-col gap-2 pt-1">
      {canvases.map((canvas) => (
        <details key={`${canvas.kind}-${canvas.name}-${canvas.timestamp}`} className="k-workitem k-tint-green" open>
          <summary className="k-workitem-head">
            <span className="k-workitem-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <path d="M3 9h18M9 21V9" />
              </svg>
            </span>
            <span className="k-workitem-title">
              <span className="k-text">{canvas.title || canvas.name}</span>
              <span className="k-micro">{canvas.format || 'text'}</span>
            </span>
            <span className="k-workitem-meta">{formatTime(canvas.timestamp)}</span>
            <span className="k-snippet-tools" onClick={(e) => e.stopPropagation()}>
              <CopyButton value={canvas.content} title="Copy canvas" />
              <DownloadButton
                data={canvas.content}
                filename={canvasFilename(canvas)}
                mime={canvasMime(canvas)}
                title="Download canvas"
              />
            </span>
            <CaretIcon />
          </summary>
          <div className="k-workitem-body">
            <CanvasRender canvas={canvas} />
          </div>
        </details>
      ))}
    </div>
  )
}

function CaretIcon() {
  return (
    <svg className="k-workitem-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M9 6l6 6-6 6" />
    </svg>
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
    <details className={`k-workitem k-tint-gold ${active ? 'k-live' : ''}`} open={active}>
      <summary className="k-workitem-head">
        <span className="k-workitem-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 2v3M12 19v3M4.93 4.93l2.12 2.12M16.95 16.95l2.12 2.12M2 12h3M19 12h3M4.93 19.07l2.12-2.12M16.95 7.05l2.12-2.12" />
          </svg>
        </span>
        <span className="k-workitem-title">
          <span className="k-text">Thinking</span>
          <span className="k-micro">{sortedEntries.length} step{sortedEntries.length === 1 ? '' : 's'}</span>
        </span>
        {active ? <span className="k-status k-live" aria-label="live" /> : null}
        <CaretIcon />
      </summary>
      <div className="k-workitem-body">
        <div className="max-h-[260px] overflow-auto pr-1">
          {sortedEntries.map((entry) => (
            <div key={entry.id} className="border-l border-[var(--line-soft)] pl-3 py-1.5 text-[12px]">
              <div className="flex flex-wrap items-center gap-2 text-[var(--muted)]">
                <span className="font-medium text-[var(--ink)]">{entry.agent || entry.title}</span>
                {entry.status ? <span>{entry.status}</span> : null}
                <span className="ml-auto">{formatTime(entry.timestamp)}</span>
              </div>
              {entry.body ? (
                <div className="pt-1">
                  <MarkdownBlock content={entry.body} compact />
                </div>
              ) : (
                <p className="pt-1 text-[var(--muted)]">Reasoning started.</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </details>
  )
}

function TimelineFeed({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return <p className="pt-2 text-[12px] text-[var(--muted)]">No timeline events yet.</p>
  }

  const sortedEntries = entries.slice().sort((left, right) => left.timestamp - right.timestamp)

  const chipClass = (kind: TimelineEntryKind): string => {
    switch (kind) {
      case 'answer':
        return 'k-chip k-teal'
      case 'thinking':
        return 'k-chip k-gold'
      case 'subsystem':
        return 'k-chip k-blue'
      case 'error':
        return 'k-chip k-pink'
      case 'lifecycle':
        return 'k-chip k-green'
      default:
        return 'k-chip'
    }
  }

  /* Backend frequently sets agent = title-in-caps for subsystem entries.
     If the agent string is just the title (case-insensitive) or longer than
     ~24 chars, hide it — the title already says what the entry is. */
  const visibleAgent = (entry: TimelineEntry): string | null => {
    const raw = String(entry.agent || '').trim()
    if (!raw) return null
    if (raw.length > 24) return null
    if (raw.toLowerCase() === String(entry.title || '').toLowerCase()) return null
    return raw
  }

  return (
    <div className="flex flex-col gap-1.5 pt-1">
      {sortedEntries.map((entry) => {
        const agent = visibleAgent(entry)
        const hasBody = Boolean(entry.body)
        return (
          <details key={entry.id} className="k-workitem">
            <summary className="k-workitem-head">
              <span className={chipClass(entry.kind)}>{entry.kind}</span>
              <span className="k-workitem-title">
                <span className="k-text">{entry.title}</span>
                {agent ? <span className="k-micro">{agent}</span> : null}
                {entry.status ? <span className="k-micro">{entry.status}</span> : null}
              </span>
              <span className="k-workitem-meta">{formatTime(entry.timestamp)}</span>
              <CaretIcon />
            </summary>
            <div className="k-workitem-body">
              {hasBody ? (
                <Snippet
                  content={entry.body!}
                  format={entry.format === 'json' ? 'json' : entry.format === 'code' ? 'code' : entry.format === 'markdown' ? 'markdown' : 'text'}
                  language={entry.format === 'code' ? inferLanguage(null, entry.body!) : undefined}
                  maxHeight={240}
                />
              ) : (
                <p className="text-[12px] text-[var(--muted)]">No body payload.</p>
              )}
            </div>
          </details>
        )
      })}
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
    return <p className="pt-2 text-[12px] text-[var(--muted)]">No downloadable files for this turn yet.</p>
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
    <div className="flex flex-col gap-3 pt-1">
      {attachments.length > 0 ? (
        <div>
          <div className="k-micro pb-1">Sent attachments</div>
          <div className="k-result-list">
            {attachments.map((attachment, index) => (
              <button
                key={attachment.id}
                type="button"
                onClick={() => void handleAttachmentDownload(attachment, index)}
                className="k-result-row"
                style={{ background: 'transparent', border: 0, font: 'inherit' }}
              >
                <span className="k-workitem-icon" style={{ width: 18, height: 18 }}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21.4 11.05 12.5 19.95a5 5 0 1 1-7-7l9-9a3.5 3.5 0 1 1 5 5l-9 9a2 2 0 1 1-3-3l8.5-8.5" />
                  </svg>
                </span>
                <div className="k-result-main">
                  <span className="k-result-title">{attachment.name}</span>
                  <span className="k-result-host">
                    {typeof attachment.size === 'number' ? formatBytes(attachment.size) : attachment.mime || attachment.rn || 'Stored attachment'}
                  </span>
                </div>
                <span className="text-[12px] text-[var(--blue-dark)]">
                  {downloadingId === `attachment:${index}` ? 'Preparing…' : 'Download'}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {files.length > 0 ? (
        <div>
          <div className="k-micro pb-1">Assistant files</div>
          <div className="k-result-list">
            {files.map((file) => (
              <button
                key={file.rn}
                type="button"
                onClick={() => void handleFileDownload(file)}
                className="k-result-row"
                style={{ background: 'transparent', border: 0, font: 'inherit' }}
              >
                <span className="k-workitem-icon" style={{ width: 18, height: 18 }}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                  </svg>
                </span>
                <div className="k-result-main">
                  <span className="k-result-title">{file.filename}</span>
                  <span className="k-result-host">{file.description || file.mime || file.rn}</span>
                </div>
                <span className="text-[12px] text-[var(--blue-dark)]">
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
    <div className="flex flex-col gap-2 pt-1">
      {sortedArtifacts.map((artifact) => {
        if (artifact.kind === 'timeline') {
          return (
            <details key={`${artifact.kind}-${artifact.name}`} className="k-workitem k-tint-teal k-live" open>
              <summary className="k-workitem-head">
                <span className="k-workitem-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 7v6l4 2" />
                  </svg>
                </span>
                <span className="k-workitem-title">
                  <span className="k-text">{artifact.name}</span>
                  <span className="k-micro">live update</span>
                </span>
                <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
                <CaretIcon />
              </summary>
              <div className="k-workitem-body">
                <div className="max-h-[320px] overflow-auto pr-1">
                  <MarkdownBlock content={artifact.markdown} compact />
                </div>
              </div>
            </details>
          )
        }

        if (artifact.kind === 'canvas') {
          return (
            <details key={`${artifact.kind}-${artifact.name}`} className="k-workitem k-tint-green" open>
              <summary className="k-workitem-head">
                <span className="k-workitem-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" />
                    <path d="M3 9h18M9 21V9" />
                  </svg>
                </span>
                <span className="k-workitem-title">
                  <span className="k-text">{artifact.title || artifact.name}</span>
                  <span className="k-micro">{artifact.format || 'text'}</span>
                </span>
                <CaretIcon />
              </summary>
              <div className="k-workitem-body">
                <CanvasRender canvas={artifact} />
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
              className="k-workitem"
              style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}
            >
              <div className="k-workitem-head">
                <span className="k-workitem-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 1 0-7.07-7.07L11.5 4.5" />
                    <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5" />
                  </svg>
                </span>
                <span className="k-workitem-title">
                  <span className="k-text">{artifact.title || artifact.url}</span>
                </span>
                <span className="k-workitem-meta">{shortUrl(artifact.url)}</span>
              </div>
              {artifact.body ? (
                <div className="k-workitem-body">
                  <div className="line-clamp-2 text-[12px] text-[var(--text-2)]">{artifact.body}</div>
                </div>
              ) : null}
            </a>
          )
        }

        if (artifact.kind === 'file') {
          return (
            <div key={`${artifact.kind}-${artifact.rn}`} className="k-workitem">
              <div className="k-workitem-head">
                <span className="k-workitem-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                  </svg>
                </span>
                <span className="k-workitem-title">
                  <span className="k-text">{artifact.filename}</span>
                  <span className="k-micro">file</span>
                </span>
                <span className="k-workitem-meta">
                  {artifact.description || artifact.mime || (artifact.rn ? artifact.rn.split(':').pop() : '')}
                </span>
              </div>
            </div>
          )
        }

        if (artifact.kind === 'web_search') {
          return (
            <details key={`${artifact.kind}-${artifact.searchId}`} className="k-workitem k-tint-sky" open>
              <summary className="k-workitem-head">
                <span className="k-workitem-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="11" cy="11" r="7" />
                    <path d="M21 21l-4.3-4.3" />
                  </svg>
                </span>
                <span className="k-workitem-title">
                  <span className="k-text">{artifact.title || artifact.name || 'Web search'}</span>
                  <span className="k-micro">
                    web search · {artifact.items.length} result{artifact.items.length === 1 ? '' : 's'}
                  </span>
                </span>
                <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
                <CaretIcon />
              </summary>
              <div className="k-workitem-body">
                {artifact.objective ? (
                  <p className="text-[12px] text-[var(--muted)]">{artifact.objective}</p>
                ) : null}
                {artifact.queries.length > 0 ? (
                  <div className="k-query-row">
                    <span className="k-micro">queries</span>
                    {artifact.queries.map((query) => (
                      <span key={query} className="k-query-chip">{query}</span>
                    ))}
                  </div>
                ) : null}
                {artifact.items.length > 0 ? (
                  <div className="k-result-list">
                    {artifact.items.slice(0, 6).map((item, idx) => (
                      <a
                        key={item.url}
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="k-result-row"
                      >
                        <span className="k-result-favicon" aria-hidden="true" />
                        <div className="k-result-main">
                          <span className="k-result-title">{item.title || shortUrl(item.url)}</span>
                          <span className="k-result-host">{shortUrl(item.url)}</span>
                          {item.body ? <span className="k-result-body">{item.body}</span> : null}
                        </div>
                        <span className="k-result-tag">[{idx + 1}]</span>
                      </a>
                    ))}
                  </div>
                ) : null}
                {artifact.reportContent ? (
                  <details>
                    <summary className="cursor-pointer text-[12px] font-medium text-[var(--blue-dark)]">
                      Show report
                    </summary>
                    <div className="mt-1 max-h-[360px] overflow-auto pr-1">
                      <MarkdownBlock content={artifact.reportContent} compact />
                    </div>
                  </details>
                ) : null}
              </div>
            </details>
          )
        }

        if (artifact.kind === 'web_fetch') {
          return (
            <details key={`${artifact.kind}-${artifact.executionId}`} className="k-workitem k-tint-gold" open>
              <summary className="k-workitem-head">
                <span className="k-workitem-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21 12a9 9 0 1 1-9-9" />
                    <path d="M21 3v6h-6" />
                  </svg>
                </span>
                <span className="k-workitem-title">
                  <span className="k-text">{artifact.title || artifact.name || 'Web fetch'}</span>
                  <span className="k-micro">
                    web fetch · {artifact.items.length} URL{artifact.items.length === 1 ? '' : 's'}
                  </span>
                </span>
                <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
                <CaretIcon />
              </summary>
              <div className="k-workitem-body">
                <div className="k-result-list">
                  {artifact.items.slice(0, 6).map((item) => (
                    <a
                      key={item.url}
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="k-result-row"
                    >
                      <span className="k-result-favicon" aria-hidden="true" />
                      <div className="k-result-main">
                        <span className="k-result-title">{shortUrl(item.url)}</span>
                        <span className="k-result-host">
                          {(item.status || 'unknown').toUpperCase()}
                          {item.mime ? ` · ${item.mime}` : ''}
                          {typeof item.content_length === 'number' ? ` · ${formatBytes(item.content_length)}` : ''}
                        </span>
                      </div>
                    </a>
                  ))}
                </div>
              </div>
            </details>
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
          const isError = artifact.status?.status === 'error'
          const isRunning = artifact.status?.status === 'exec' || artifact.status?.status === 'gen'
          const lang = inferLanguage(null, artifact.program || '')

          return (
            <details
              key={`${artifact.kind}-${artifact.executionId}`}
              className={`k-workitem k-tint-purple ${isError ? 'k-err' : isRunning ? 'k-live' : ''}`}
              open
            >
              <summary className="k-workitem-head">
                <span className="k-workitem-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="16 18 22 12 16 6" />
                    <polyline points="8 6 2 12 8 18" />
                  </svg>
                </span>
                <span className="k-workitem-title">
                  <span className="k-text">{artifact.title || artifact.name || 'Program'}</span>
                  <span className="k-micro">exec · {statusLabel.toLowerCase()}</span>
                </span>
                <span className="k-workitem-meta">{formatTime(artifact.timestamp)}</span>
                <CaretIcon />
              </summary>
              <div className="k-workitem-body">
                {artifact.objective ? (
                  <p className="text-[12px] text-[var(--muted)]">{artifact.objective}</p>
                ) : null}
                {artifact.contract && artifact.contract.length > 0 ? (
                  <div className="k-result-list">
                    {artifact.contract.map((item) => (
                      <div key={item.filename} className="k-result-row" style={{ gridTemplateColumns: 'auto minmax(0,1fr)' }}>
                        <span className="k-workitem-icon" style={{ width: 18, height: 18 }}>
                          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                            <path d="M14 2v6h6" />
                          </svg>
                        </span>
                        <div className="k-result-main">
                          <span className="k-result-title">{item.filename}</span>
                          {item.description ? <span className="k-result-host">{item.description}</span> : null}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {artifact.program ? (
                  <Snippet
                    content={artifact.program}
                    format="code"
                    language={lang}
                    label={lang}
                    filename={`program.${lang === 'python' ? 'py' : lang === 'javascript' ? 'js' : lang === 'bash' ? 'sh' : 'txt'}`}
                    downloadMime="text/plain"
                    showDownload
                  />
                ) : null}
                {artifact.status?.status === 'error' && artifact.status.error ? (
                  <div className="k-notice k-error">
                    <span>{Object.values(artifact.status.error).join(' ')}</span>
                  </div>
                ) : null}
              </div>
            </details>
          )
        }

        return (
          <div key={`${artifact.kind}-${artifact.timestamp}`} className="k-notice k-error">
            <span>{artifact.message}</span>
          </div>
        )
      })}
    </div>
  )
}

function FollowupMessageBlock({ message }: { message: AdditionalUserMessage }) {
  const isSteer = message.continuationKind === 'steer'
  const text = message.text || (isSteer ? 'Stop requested' : '')
  return (
    <div className="flex flex-col gap-1 self-end max-w-[760px]" style={{ marginLeft: 'auto' }}>
      <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
        <span className={`k-chip ${isSteer ? 'k-pink' : 'k-teal'}`}>
          {isSteer ? 'steer' : 'follow-up'}
        </span>
        <span>{formatTime(message.timestamp)}</span>
      </div>
      <div className="k-msg rounded-md border border-[var(--line-soft)] bg-[var(--surface-2)] px-3 py-2 text-[14px] leading-6 whitespace-pre-wrap">
        {text}
        {text ? (
          <span className="k-msg-toolbar">
            <CopyButton value={text} title="Copy follow-up" />
          </span>
        ) : null}
        {message.attachments.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 pt-1.5">
            {message.attachments.map((attachment) => (
              <span key={attachment.id} className="k-chip">
                {attachment.name}
                {typeof attachment.size === 'number' ? ` · ${formatBytes(attachment.size)}` : ''}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

type OverviewEvent =
  | { kind: 'artifact'; timestamp: number; artifact: Artifact; key: string }
  | { kind: 'followup'; timestamp: number; message: AdditionalUserMessage; key: string }

function mergeOverviewEvents(
  artifacts: Artifact[],
  additionalUserMessages: AdditionalUserMessage[],
): OverviewEvent[] {
  const events: OverviewEvent[] = []
  artifacts.forEach((artifact, index) => {
    events.push({
      kind: 'artifact',
      timestamp: artifact.timestamp,
      artifact,
      key: `artifact:${artifact.kind}:${index}:${artifact.timestamp}`,
    })
  })
  additionalUserMessages.forEach((message) => {
    events.push({
      kind: 'followup',
      timestamp: message.timestamp,
      message,
      key: `followup:${message.id}`,
    })
  })
  events.sort((left, right) => left.timestamp - right.timestamp)
  return events
}

function MergedOverviewFeed({
  events,
}: {
  events: OverviewEvent[]
}) {
  if (events.length === 0) return null
  /* Each artifact pass goes through ArtifactFeed with a one-element list so
     we reuse its existing per-kind rendering without duplicating logic. */
  return (
    <div className="flex flex-col gap-2 pt-1">
      {events.map((event) => {
        if (event.kind === 'followup') {
          return <FollowupMessageBlock key={event.key} message={event.message} />
        }
        return <ArtifactFeed key={event.key} artifacts={[event.artifact]} />
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
  const canvases = useMemo(
    () => turn.artifacts.filter((artifact): artifact is CanvasArtifact => artifact.kind === 'canvas'),
    [turn.artifacts],
  )
  /* Overview shows artifacts AND follow-up user messages in real timestamp
     order so the user can see the conversation evolve. Thinking entries are
     consolidated separately into ThinkingBlock and never enter this list. */
  const overviewEvents = useMemo(
    () => mergeOverviewEvents(turn.artifacts, turn.additionalUserMessages),
    [turn.artifacts, turn.additionalUserMessages],
  )

  const stateChipClass =
    turn.state === 'error'
      ? 'k-chip k-pink'
      : turn.state === 'completed'
        ? 'k-chip k-green'
        : 'k-chip k-teal'

  return (
    <article className="flex flex-col gap-3">
      {/* User turn */}
      <div className="flex flex-col gap-1 self-end max-w-[760px]">
        <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
          <span className="font-semibold text-[var(--text-2)]">You</span>
          <span>{formatTime(turn.createdAt)}</span>
        </div>
        <div className="k-msg rounded-md border border-[var(--line-soft)] bg-[var(--surface-2)] px-3 py-2 text-[14px] leading-6 whitespace-pre-wrap">
          {turn.userMessage || 'Sent attachments only'}
          {turn.userMessage ? (
            <span className="k-msg-toolbar">
              <CopyButton value={turn.userMessage} title="Copy message" />
            </span>
          ) : null}
        </div>
        {turn.userAttachments.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {turn.userAttachments.map((attachment) => (
              <span key={attachment.id} className="k-chip">
                {attachment.name}
                {typeof attachment.size === 'number' ? ` · ${formatBytes(attachment.size)}` : ''}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {/* Assistant turn */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-[var(--text-2)]">Assistant</span>
            <span className={stateChipClass}>{turn.state}</span>
          </div>
        </div>

        <div className="k-tabs">
          {([
            ['overview', 'Overview', null],
            ['timeline', 'Timeline', turn.timeline.length || null],
            ['steps', 'Steps', steps.length || null],
            ['canvases', 'Canvas', canvases.length || null],
            ['links', 'Links', turnLinks.length || null],
            ['files', 'Files', (turn.userAttachments.length + assistantFiles.length) || null],
          ] as Array<[TurnTab, string, number | null]>).map(([tab, label, count]) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`k-tab ${activeTab === tab ? 'k-active' : ''}`}
            >
              {label}
              {count ? <span className="k-count">{count}</span> : null}
            </button>
          ))}
        </div>

        <div className="flex flex-col gap-2 pt-1">
          {activeTab === 'overview' ? (
            <>
              <ThinkingBlock entries={thinkingEntries} active={turn.state === 'pending' || turn.state === 'running'} />
              <MergedOverviewFeed events={overviewEvents} />
              {turn.answer ? (
                <div className="k-msg mt-1 rounded-md border border-[var(--line-soft)] bg-[var(--surface)] px-3 py-2">
                  <MarkdownBlock content={turn.answer} />
                  <span className="k-msg-toolbar">
                    <CopyButton value={turn.answer} title="Copy answer" />
                  </span>
                </div>
              ) : turn.state === 'error' ? (
                <div className="k-notice k-error">
                  <span>{turn.error || 'Request failed.'}</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 text-[12px] text-[var(--muted)]">
                  <span className="k-status k-live" />
                  <span>Streaming response…</span>
                </div>
              )}
              <SuggestedQuestions items={turn.followups} disabled={sendingDisabled} onSelect={onFollowup} />
            </>
          ) : null}

          {activeTab === 'timeline' ? <TimelineFeed entries={turn.timeline} /> : null}
          {activeTab === 'steps' ? <StepList steps={steps} /> : null}
          {activeTab === 'canvases' ? <CanvasPanel canvases={canvases} /> : null}
          {activeTab === 'links' ? <LinksPanel links={turnLinks} /> : null}
          {activeTab === 'files' ? (
            <DownloadsPanel attachments={turn.userAttachments} files={assistantFiles} onError={onDownloadError} />
          ) : null}
        </div>
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
    <div className="flex flex-col gap-2">
      {lockedMessage ? (
        <div className="k-notice k-warning">
          <span>{lockedMessage}</span>
        </div>
      ) : null}

      <div className="k-composer">
        {files.length > 0 ? (
          <div className="k-composer-attachments">
            {files.map((file, index) => (
              <span key={`${file.name}-${file.size}-${index}`} className="k-composer-attach-pill">
                <span>{file.name}</span>
                <span className="text-[var(--muted)]">{formatBytes(file.size)}</span>
                <button type="button" aria-label="Remove" onClick={() => onFileRemove(index)}>×</button>
              </span>
            ))}
          </div>
        ) : null}

        <textarea
          value={text}
          disabled={disabled}
          onChange={(event) => onTextChange(event.target.value)}
          placeholder={
            inProgress
              ? 'Send a follow-up while the current turn is still running.'
              : 'Ask anything — attachments, web search, code exec, and follow-ups are supported.'
          }
          rows={2}
        />

        <div className="k-composer-bar">
          <div className="left">
            <label className="k-iconbtn cursor-pointer" title="Attach files">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.4 11.05 12.5 19.95a5 5 0 1 1-7-7l9-9a3.5 3.5 0 1 1 5 5l-9 9a2 2 0 1 1-3-3l8.5-8.5" />
              </svg>
              <input
                type="file"
                multiple
                className="hidden"
                disabled={disabled}
                onChange={(event) => onFilesAdd(event.target.files)}
              />
            </label>
            {inProgress ? (
              <button
                type="button"
                disabled={disabled}
                onClick={onStop}
                className="k-btn k-sm k-danger"
                title="Stop the current turn"
              >
                Stop
              </button>
            ) : null}
          </div>
          <div className="right">
            <span className="k-micro hidden sm:inline">⌘↵ to send</span>
            <button
              type="button"
              disabled={disabled || (!text.trim() && files.length === 0)}
              onClick={onSubmit}
              className="k-btn k-primary"
            >
              {inProgress ? 'Follow up' : 'Send'}
            </button>
          </div>
        </div>
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
  const [showScrollDown, setShowScrollDown] = useState(false)

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    const updateAutoScroll = () => {
      const doc = document.documentElement
      const scrollTop = window.scrollY || doc.scrollTop || 0
      const remaining = doc.scrollHeight - (scrollTop + window.innerHeight)
      const near = remaining < 140
      autoScrollRef.current = near
      setShowScrollDown(!near && doc.scrollHeight > window.innerHeight + 80)
    }

    updateAutoScroll()
    window.addEventListener('scroll', updateAutoScroll, { passive: true })
    window.addEventListener('resize', updateAutoScroll)
    return () => {
      window.removeEventListener('scroll', updateAutoScroll)
      window.removeEventListener('resize', updateAutoScroll)
    }
  }, [])

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

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

    const turnId = createTurnId()
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
        <div className="glass-panel px-6 py-5 text-center">
          <div className="k-status k-live justify-center">Connecting application config…</div>
        </div>
      </div>
    )
  }

  const connectionDotClass =
    state.connection === 'connected'
      ? 'k-status'
      : state.connection === 'disconnected'
        ? 'k-status k-crit'
        : 'k-status k-live'

  return (
    <div className="shell-grid">
      <button
        type="button"
        className={`k-scroll-to-bottom ${showScrollDown ? 'k-show' : ''}`}
        onClick={scrollToBottom}
        aria-label="Scroll to latest"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 5v14M5 12l7 7 7-7" />
        </svg>
        <span>Latest</span>
      </button>
      <div className="mx-auto flex min-h-screen w-full max-w-[1320px] flex-col">
        <header className="k-appbar">
          <div className="k-brand min-w-0">
            <span className="k-brand-mark" aria-hidden="true" />
            <span className="k-brand-name">Versatile</span>
            <span className="k-brand-sep">/</span>
            <span className="k-brand-path">{bundleId}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={connectionDotClass}>
              {state.connection === 'connected'
                ? `${settings.getTenant() || 'tenant'} / ${settings.getProject() || 'project'}`
                : state.connection}
            </span>
            <button
              type="button"
              onClick={handleReconnect}
              className="k-iconbtn"
              aria-label="Reconnect"
              title="Reconnect"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 12a9 9 0 1 1-3-6.7" />
                <path d="M21 3v6h-6" />
              </svg>
            </button>
            <button
              type="button"
              onClick={startNewChat}
              disabled={hasPendingTurn}
              className="k-btn"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 5v14M5 12h14" />
              </svg>
              New chat
            </button>
          </div>
        </header>

        <main className="flex-1 px-3 py-3 sm:px-4 sm:py-4 lg:px-6 lg:py-5">
          {bootError || state.banners.length > 0 ? (
            <div className="pb-3">
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
          ) : null}

          <div className="grid gap-3 lg:gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
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

            <div className="glass-panel min-w-0 overflow-hidden flex flex-col">
              <section className="flex items-center justify-between gap-3 border-b border-[var(--line-soft)] px-4 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-semibold text-[var(--ink)]">
                    {state.conversationTitle || (state.conversationId ? 'Untitled conversation' : 'New chat')}
                  </div>
                  <div className="truncate text-[11px] text-[var(--muted)]">
                    {state.conversationId || (state.conversationsLoading ? 'Refreshing chats…' : `${state.conversations.length} saved chat${state.conversations.length === 1 ? '' : 's'}`)}
                  </div>
                </div>
              </section>

              <div className="flex-1 px-4 py-3">
                {state.turns.length === 0 ? (
                  <div className="k-empty">
                    <div className="k-empty-title">No turns yet</div>
                    <div className="k-empty-body">Ask anything — attachments, web search, and code exec are available.</div>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {[
                        'Summarize the last attachment as markdown',
                        'Search the web and cite three sources',
                        'Run a small exec report',
                      ].map((prompt) => (
                        <button
                          key={prompt}
                          type="button"
                          className="k-followup"
                          onClick={() => setState((previous) => ({ ...previous, composerText: prompt }))}
                        >
                          {prompt}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col gap-4">
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

              <div className="border-t border-[var(--line-soft)] px-3 py-3">
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
