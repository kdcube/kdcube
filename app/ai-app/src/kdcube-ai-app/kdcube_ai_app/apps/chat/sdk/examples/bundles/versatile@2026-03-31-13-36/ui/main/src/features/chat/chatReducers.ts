/**
 * Pure ChatState reducers + helpers.
 *
 * These are exactly the `apply*`, `addBanner`, `updateTurn`, `ensureTurn`,
 * `syncConversationFromEnvelope`, `upsert*`, `buildChatHistory`,
 * `findActiveTurn`, `fallbackRateLimitMessage`, `timelineTitle*`,
 * `extractPayload`, `normalizeTurnAttachment`, `createEmptyTurn`, and
 * `hydrateHistoricalConversation` functions from the pre-Wave-3 App.tsx —
 * moved verbatim so the state machine logic is unchanged. The slice in
 * `./chatSlice.ts` calls these from its reducer cases.
 *
 * Each apply* takes a plain `ChatState` and returns a new `ChatState`.
 * Within `createSlice`, Immer accepts a returned-from-reducer state as the
 * new state, so the pure functional shape stays intact.
 */

import {
  BUILT_BUNDLE_ID,
  createLocalId,
} from '../../settings.ts'
import type {
  BannerTone,
  BaseEnvelope,
  ChatCompleteEnvelope,
  ChatDeltaEnvelope,
  ChatErrorEnvelope,
  ChatHistoryItem,
  ChatStartEnvelope,
  ChatStepEnvelope,
  ConversationArtifactDTO,
  ConversationDTO,
  ConvStatusEnvelope,
  RateLimitPayload,
} from '../../service.ts'
import {
  closeStreamingMarkdown,
  formatBytes,
  formatTime,
  prettyJson,
  safeJsonParse,
  timestampValue,
} from '../../components/utils.ts'
import { inferLanguage } from '../../components/highlight.ts'
import type {
  AdditionalUserMessage,
  Artifact,
  CanvasArtifact,
  ChatState,
  ChatTurn,
  CodeExecArtifact,
  CodeExecContractItem,
  CodeExecStatus,
  FileArtifact,
  LinkArtifact,
  ServiceErrorArtifact,
  TimelineArtifact,
  TimelineEntry,
  TimelineEntryFormat,
  TimelineEntryKind,
  TurnAttachment,
  TurnState,
  TurnStep,
  WebFetchArtifact,
  WebFetchItem,
  WebSearchArtifact,
  WebSearchItem,
} from './chatTypes.ts'
import { initialState } from './chatTypes.ts'

export function addBanner(
  state: ChatState,
  tone: BannerTone,
  text: string,
  placement: 'top' | 'composer' = 'top',
): ChatState {
  const trimmed = text.trim()
  if (!trimmed) return state
  if (state.banners.some((banner) => banner.text === trimmed && banner.tone === tone)) {
    return state
  }
  const banners = [{ id: createLocalId('banner'), tone, text: trimmed, placement }, ...state.banners].slice(0, 4)
  return { ...state, banners }
}

export function updateTurn(
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

export function ensureTurn(state: ChatState, turnId: string, createdAt: number, message = ''): ChatState {
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

export function syncConversationFromEnvelope(state: ChatState, env: BaseEnvelope): ChatState {
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

export function upsertArtifact<T extends Artifact>(
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

export function upsertTimelineEntry(
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


export function buildChatHistory(turns: ChatTurn[]): ChatHistoryItem[] {
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

export function findActiveTurn(turns: ChatTurn[]): ChatTurn | null {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index]
    if (turn.state === 'pending' || turn.state === 'running') return turn
  }
  return null
}

export function fallbackRateLimitMessage(rateLimit: RateLimitPayload | undefined, data: Record<string, unknown>): string {
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

export function timelineTitleForMarker(marker: string, fallbackName?: string | null): string {
  switch (marker) {
    case 'answer':
      return 'Assistant answer'
    case 'thinking':
      return 'Reasoning'
    case 'timeline_text':
      return fallbackName || 'Timeline update'
    case 'canvas':
      return fallbackName || 'Artifact update'
    default:
      return fallbackName || 'Stream update'
  }
}

export function timelineTitleForSubsystem(subtype: string, fallbackName?: string | null): string {
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

export function extractPayload(record: ConversationArtifactDTO['data']): Record<string, unknown> {
  if (record?.payload && typeof record.payload === 'object') return record.payload
  if (record && typeof record === 'object') return record as Record<string, unknown>
  return {}
}

export function normalizeTurnAttachment(
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

export function createEmptyTurn(turnId: string, createdAt: number, message = ''): ChatTurn {
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
    costUsd: null,
    elapsedMs: null,
  }
}

export function hydrateHistoricalConversation(conversation: ConversationDTO): ChatTurn[] {
  return (conversation.turns || []).map((turnDto, turnIndex) => {
    let turn = createEmptyTurn(turnDto.turn_id, Date.now())
    /* Which user-message slot owns the next `artifact:user.attachment` we
     * encounter. Walked positionally: the most recent `chat:user` we saw
     * (the main message until a continuation user message arrives, then
     * the latest additional message). Server stores attachments
     * interleaved with the `chat:user` they were sent with, in
     * chronological order. */
    let currentUserSlot: 'main' | 'additional' = 'main'

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
            currentUserSlot = 'additional'
            break
          }
          turn = {
            ...turn,
            createdAt: ts,
            userMessage: text,
          }
          currentUserSlot = 'main'
          break
        }
        case 'artifact:user.attachment': {
          /* Route this attachment to whichever user message it was sent
           * with — the main message or the latest additional one. */
          const normalized = normalizeTurnAttachment(
            payload,
            `stored:${turnDto.turn_id}:${turn.userAttachments.length}:${turn.additionalUserMessages.length}`,
          )
          if (currentUserSlot === 'additional' && turn.additionalUserMessages.length > 0) {
            const updated = turn.additionalUserMessages.slice()
            const last = updated[updated.length - 1]
            updated[updated.length - 1] = {
              ...last,
              attachments: [...last.attachments, normalized],
            }
            turn = {
              ...turn,
              createdAt: Math.min(turn.createdAt, ts),
              additionalUserMessages: updated,
            }
          } else {
            turn = {
              ...turn,
              createdAt: Math.min(turn.createdAt, ts),
              userAttachments: [...turn.userAttachments, normalized],
            }
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
        case 'artifact:conv.artifacts.events': {
          const items = Array.isArray(payload.items) ? payload.items : []
          let costUsd = turn.costUsd ?? null
          let elapsedMs = turn.elapsedMs ?? null
          for (const item of items) {
            if (!item || typeof item !== 'object') continue
            const row = item as Record<string, unknown>
            const type = typeof row.type === 'string' ? row.type : ''
            const data = (row.data && typeof row.data === 'object') ? row.data as Record<string, unknown> : {}
            if (type === 'accounting.usage' && typeof data.cost_total_usd === 'number') {
              costUsd = data.cost_total_usd
            }
            if (type === 'chat.turn.summary' && typeof data.elapsed_ms === 'number') {
              elapsedMs = data.elapsed_ms
            }
          }
          turn = { ...turn, costUsd, elapsedMs }
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

export function applyChatStart(state: ChatState, env: ChatStartEnvelope): ChatState {
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

export function applyChatComplete(state: ChatState, env: ChatCompleteEnvelope): ChatState {
  const ensuredState = ensureTurn(state, env.conversation.turn_id, timestampValue(env.timestamp))
  const syncedState = syncConversationFromEnvelope(ensuredState, env)
  return updateTurn(syncedState, env.conversation.turn_id, (turn) => {
    const completeFollowups = Array.isArray(env.data?.followups)
      ? (env.data.followups as unknown[]).filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      : []
    return {
      ...turn,
      state: env.data?.error_message ? 'error' : 'completed',
      // The visible answer comes from streamed `marker="answer"` deltas, where citation tokens
      // [[S:n]] are replaced into resolved links live. The chat.complete envelope may still
      // carry `data.final_answer` in raw token form; reading it here would arrive after the
      // stream completes and clobber the rendered text. Keep `turn.answer` (streamed text).
      answer: turn.answer,
      error: (env.data?.error_message as string | undefined) || turn.error,
      // `chat.followups` usually arrives as a completed step before `chat.complete`.
      // Some completion envelopes still carry an empty followups array, so only use
      // completion followups when they are actually populated.
      followups: completeFollowups.length ? completeFollowups : turn.followups,
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
    }
  })
}

export function applyChatError(state: ChatState, env: ChatErrorEnvelope): ChatState {
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

export function applyConvStatus(state: ChatState, env: ConvStatusEnvelope): ChatState {
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

export function applyChatStep(state: ChatState, env: ChatStepEnvelope): ChatState {
  /* The backend names the conversation mid-turn via a `conversation_title` step
   * (the title arrives in data.title). Apply it straight to the header so it
   * updates live, instead of waiting for the post-turn conversations-list
   * refresh — and don't record it as a timeline step. Mirrors the OSS chat
   * client (chat-web-app).
   * Contract: docs/sdk/bundle/bundle-chat-stream-events-README.md
   * ("Conversation Title (`conversation_title`)"). */
  if (env.event?.step === 'conversation_title') {
    const title = typeof env.data?.title === 'string' ? env.data.title.trim() : ''
    return title ? { ...state, conversationTitle: title } : state
  }
  const syncedState = syncConversationFromEnvelope(
    ensureTurn(state, env.conversation.turn_id, timestampValue(env.timestamp)),
    env,
  )
  return updateTurn(syncedState, env.conversation.turn_id, (turn) => {
    const timestamp = timestampValue(env.timestamp)
    /* Turn accounting for the status line: cost from the `accounting.usage`
     * event (step "accounting"), wall time from `chat.turn.summary` (step
     * "turn.summary"). Both arrive once near the end of the turn. */
    const costUsd = env.event.step === 'accounting' && typeof env.data?.cost_total_usd === 'number'
      ? env.data.cost_total_usd
      : turn.costUsd
    const elapsedMs = env.event.step === 'turn.summary' && typeof env.data?.elapsed_ms === 'number'
      ? env.data.elapsed_ms
      : turn.elapsedMs
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

      if (env.event.step === 'followups' && Array.isArray(env.data?.items)) {
        // chat.followups arrives via the `chat.step` route (status === "completed"),
        // so this is the canonical place to populate turn.followups during a live
        // stream. The reload path populates them directly from the stored
        // `artifact:conv.user_shortcuts`.
        const followups = (env.data.items as unknown[]).filter(
          (item): item is string => typeof item === 'string' && item.trim().length > 0,
        )
        return {
          ...turn,
          steps: {
            ...turn.steps,
            [env.event.step]: nextStep,
          },
          artifacts,
          followups: followups.length ? followups : turn.followups,
        }
      }
    }

    return {
      ...turn,
      costUsd,
      elapsedMs,
      steps: {
        ...turn.steps,
        [env.event.step]: nextStep,
      },
      artifacts,
    }
  })
}

export function applyChatDelta(state: ChatState, env: ChatDeltaEnvelope): ChatState {
  /* Some deployments emit `conversation_title` on the delta route; honor it
   * here too so the header updates live (see applyChatStep). */
  if (env.event?.step === 'conversation_title') {
    const title = typeof env.data?.title === 'string' ? env.data.title.trim() : ''
    return title ? { ...state, conversationTitle: title } : state
  }
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

    // followups handling moved to applyChatStep (chat.followups is dispatched as
    // a `chat.step` event with step === "followups", not a delta).

    return {
      ...nextTurn,
      artifacts,
      timeline,
    }
  })
}


















