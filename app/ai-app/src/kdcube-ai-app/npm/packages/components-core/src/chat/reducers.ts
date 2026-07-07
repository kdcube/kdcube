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

import { createLocalId } from './runtime.ts'
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
} from './protocol.ts'
import {
  prettyJson,
  safeJsonParse,
  timestampValue,
} from './util.ts'
import type {
  Artifact,
  CanvasArtifact,
  ChatState,
  ChatTurn,
  CodeExecArtifact,
  CodeExecContractItem,
  CodeExecStatus,
  FileArtifact,
  NamedServiceSearchArtifact,
  NamedServiceSearchItem,
  TimelineArtifact,
  TimelineEntry,
  TimelineEntryFormat,
  TurnAttachment,
  TurnState,
  TurnStep,
  WebFetchArtifact,
  WebFetchItem,
  WebSearchArtifact,
  WebSearchItem,
} from './state.ts'
import { initialState } from './state.ts'
import { canonicalObjectRef, durableHistoricalObjectRef } from './refs.ts'
import { messageWithContextChips } from './contextChips.ts'
import { connectionsConsentOpen } from './connectionsConsent.ts'
import type { ConnectionsConsentOpen } from '../shared/index.ts'

export function addBanner(
  state: ChatState,
  tone: BannerTone,
  text: string,
  placement: 'top' | 'composer' = 'top',
  action?: { label?: string; url?: string; consent?: ConnectionsConsentOpen } | null,
): ChatState {
  const trimmed = text.trim()
  if (!trimmed) return state
  if (state.banners.some((banner) => banner.text === trimmed && banner.tone === tone)) {
    return state
  }
  const actionLabel = typeof action?.label === 'string' ? action.label.trim() : ''
  const actionUrl = typeof action?.url === 'string' ? action.url.trim() : ''
  const banners = [{
    id: createLocalId('banner'),
    tone,
    text: trimmed,
    placement,
    ...(actionLabel && actionUrl ? { actionLabel, actionUrl } : {}),
    ...(action?.consent ? { consent: action.consent } : {}),
  }, ...state.banners].slice(0, 4)
  return { ...state, banners }
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function consentActionUrl(value: unknown): string {
  const raw = stringValue(value)
  if (!raw) return ''
  if (raw.startsWith('/') || raw.startsWith('http://') || raw.startsWith('https://')) return raw
  return ''
}

function findConnectedAccountConsentPayload(value: unknown, depth = 0): Record<string, unknown> | null {
  if (depth > 5 || value == null) return null
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findConnectedAccountConsentPayload(item, depth + 1)
      if (found) return found
    }
    return null
  }
  const record = recordValue(value)
  if (!record) return null
  const error = recordValue(record.error)
  const code = stringValue(record.code || record.error_code || error?.code || error?.error)
  if (code === 'needs_connected_account_consent' || code === 'needs_connected_account') {
    return record
  }
  for (const key of ['consent', 'connected_account', 'result', 'items', 'data']) {
    const found = findConnectedAccountConsentPayload(record[key], depth + 1)
    if (found) return found
  }
  return null
}

function connectedAccountConsentBanner(data: Record<string, unknown> | undefined): {
  text: string
  actionLabel: string
  actionUrl: string
  consent: ConnectionsConsentOpen
  signature: string
  tools: string[]
} | null {
  const payload = findConnectedAccountConsentPayload(data)
  if (!payload) return null
  const error = recordValue(payload.error) || {}
  const consent = recordValue(payload.consent)
    || recordValue(payload.connected_account)
    || recordValue(error.consent)
    || {}
  const provider = stringValue(consent.provider_label || consent.provider_id)
  const connector = stringValue(consent.connector_app_label || consent.connector_app_id)
  const tool = stringValue(consent.tool_label || consent.tool_id)
  const claims = Array.isArray(consent.claims)
    ? consent.claims.map((item) => stringValue(item)).filter(Boolean)
    : []
  const url = consentActionUrl(
    consent.url
    || consent.consent_url
    || consent.connect_url
    || consent.action_url
    || payload.url
    || payload.consent_url
    || payload.connect_url
    || payload.action_url,
  )
  const message = stringValue(error.message || payload.message)
  const subject = [provider, connector].filter(Boolean).join(' / ')
  const claimText = claims.length ? ` (${claims.join(', ')})` : ''
  const text = message
    || `Connect or approve ${subject || 'an external account'}${claimText}${tool ? ` for ${tool}` : ''}.`
  const blockedTools = Array.isArray(consent.tools)
    ? consent.tools.map((item) => stringValue(item)).filter(Boolean)
    : []
  return {
    text,
    actionLabel: stringValue(consent.action_label || payload.action_label) || 'Open Connection Hub',
    actionUrl: url,
    consent: connectionsConsentOpen({
      provider: stringValue(consent.provider_id),
      claims,
      accountId: stringValue(consent.account_id),
      url,
    }),
    signature: `${stringValue(consent.provider_id)}|${[...claims].sort().join(',')}`,
    tools: blockedTools,
  }
}

/** One consent banner per provider at a time, with dismissal memory.
 *
 * - A signature the user dismissed this conversation stays quiet.
 * - The identical signature already on screen stays as-is (stable id, so
 *   dismiss keeps working while turn envelopes repeat the consent state).
 * - A NEW consent state for the same provider supersedes the older banner
 *   (a stale connect_required vanishes once the state becomes
 *   claim_upgrade_required with fewer claims). */
export function upsertConsentBanner(
  state: ChatState,
  input: {
    text: string
    actionLabel: string
    actionUrl: string
    consent: ConnectionsConsentOpen
    signature: string
    tools: string[]
  },
): ChatState {
  const trimmed = input.text.trim()
  if (!trimmed || !input.signature) return state
  if (state.dismissedConsentSignatures.includes(input.signature)) return state
  if (state.banners.some((banner) => banner.consentSignature === input.signature)) return state
  const providerPrefix = input.signature.slice(0, input.signature.indexOf('|') + 1)
  const kept = state.banners.filter(
    (banner) => !(banner.consentSignature && banner.consentSignature.startsWith(providerPrefix)),
  )
  const banners = [{
    id: createLocalId('banner'),
    tone: 'warning' as BannerTone,
    text: trimmed,
    placement: 'composer' as const,
    ...(input.actionLabel && input.actionUrl ? { actionLabel: input.actionLabel, actionUrl: input.actionUrl } : {}),
    consent: input.consent,
    consentSignature: input.signature,
    ...(input.tools.length ? { consentTools: input.tools } : {}),
  }, ...kept].slice(0, 4)
  return { ...state, banners }
}

function canonicalPayloadRef(
  payload: Record<string, unknown>,
  meta: Record<string, unknown> = {},
  conversationId?: string,
): string | null {
  const ref = canonicalObjectRef(
    durableHistoricalObjectRef(payload.logical_path, conversationId),
    durableHistoricalObjectRef(payload.logicalPath, conversationId),
    durableHistoricalObjectRef(payload.artifact_path, conversationId),
    durableHistoricalObjectRef(payload.artifactPath, conversationId),
    durableHistoricalObjectRef(payload.file_ref, conversationId),
    durableHistoricalObjectRef(payload.fileRef, conversationId),
    durableHistoricalObjectRef(payload.event_ref, conversationId),
    durableHistoricalObjectRef(payload.eventRef, conversationId),
    durableHistoricalObjectRef(payload.ref, conversationId),
    durableHistoricalObjectRef(payload.path, conversationId),
    durableHistoricalObjectRef(payload.source_path, conversationId),
    durableHistoricalObjectRef(meta.logical_path, conversationId),
    durableHistoricalObjectRef(meta.logicalPath, conversationId),
    durableHistoricalObjectRef(meta.artifact_path, conversationId),
    durableHistoricalObjectRef(meta.artifactPath, conversationId),
    durableHistoricalObjectRef(meta.file_ref, conversationId),
    durableHistoricalObjectRef(meta.fileRef, conversationId),
    durableHistoricalObjectRef(meta.event_ref, conversationId),
    durableHistoricalObjectRef(meta.eventRef, conversationId),
    durableHistoricalObjectRef(meta.ref, conversationId),
    durableHistoricalObjectRef(meta.path, conversationId),
    durableHistoricalObjectRef(meta.source_path, conversationId),
  )
  return ref || null
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
  const latest = turns[turns.length - 1] || null
  return latest && (latest.state === 'pending' || latest.state === 'running') ? latest : null
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
    case 'named_service.search_results':
      return fallbackName || 'Named-service search results'
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

function extractTurnLogElapsedMs(payload: Record<string, unknown>, text?: string): number | null {
  const candidates: Record<string, unknown>[] = [payload]
  if (payload.turn_log && typeof payload.turn_log === 'object') {
    candidates.push(payload.turn_log as Record<string, unknown>)
  }
  if (text) {
    const parsed = safeJsonParse<Record<string, unknown> | null>(text, null)
    if (parsed && typeof parsed === 'object') {
      candidates.push(parsed)
    }
  }
  for (const candidate of candidates) {
    const start = typeof candidate.ts === 'string' ? Date.parse(candidate.ts) : NaN
    const end = typeof candidate.end_ts === 'string' ? Date.parse(candidate.end_ts) : NaN
    if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
      return end - start
    }
  }
  return null
}

function historicalEventType(row: Record<string, unknown>): string {
  if (typeof row.type === 'string') return row.type
  if (typeof row.event_type === 'string') return row.event_type
  const event = row.event && typeof row.event === 'object' ? row.event as Record<string, unknown> : null
  if (event && typeof event.type === 'string') return event.type
  if (event && event.step === 'accounting') return 'accounting.usage'
  if (event && event.step === 'turn.summary') return 'chat.turn.summary'
  return ''
}

function historicalEventData(row: Record<string, unknown>): Record<string, unknown> {
  if (row.data && typeof row.data === 'object') return row.data as Record<string, unknown>
  if (row.metrics && typeof row.metrics === 'object') return row.metrics as Record<string, unknown>
  return {}
}

function conversationFromRecord(row: Record<string, unknown>): Record<string, unknown> | null {
  return row.conversation && typeof row.conversation === 'object'
    ? row.conversation as Record<string, unknown>
    : null
}

function historicalEventMatchesTurn(row: Record<string, unknown>, conversationId: string, turnId: string): boolean {
  const conversation = conversationFromRecord(row)
  if (!conversation) return true
  const rowConversationId = typeof conversation.conversation_id === 'string' ? conversation.conversation_id : ''
  const rowTurnId = typeof conversation.turn_id === 'string' ? conversation.turn_id : ''
  if (rowConversationId && rowConversationId !== conversationId) return false
  if (rowTurnId && rowTurnId !== turnId) return false
  return true
}

function isAccountingStep(env: ChatStepEnvelope): boolean {
  return String(env.type || '') === 'accounting.usage' || env.event?.step === 'accounting'
}

function eventTargetsExistingTurn(state: ChatState, env: ChatStepEnvelope): boolean {
  const conversationId = env.conversation?.conversation_id || ''
  const turnId = env.conversation?.turn_id || ''
  if (!turnId) return false
  if (state.conversationId && conversationId && conversationId !== state.conversationId) return false
  return state.turns.some((turn) => turn.id === turnId)
}

function parseRecordTextJson(row: Record<string, unknown>): Record<string, unknown> | null {
  const text = typeof row.text === 'string' ? row.text : ''
  if (!text.trim() || !/^\s*[{[]/.test(text)) return null
  const parsed = safeJsonParse<unknown>(text, null)
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
    ? parsed as Record<string, unknown>
    : null
}

function basenameFromRef(ref: string): string {
  const clean = String(ref || '').split(/[?#]/, 1)[0] || ''
  const last = clean.split('/').filter(Boolean).pop() || clean
  return decodeURIComponent(last || 'file')
}

function normalizedFileArtifact(
  row: Record<string, unknown>,
  timestamp: number,
  fallbackId: string,
  conversationId?: string,
): FileArtifact | null {
  const payload = extractPayload(row as ConversationArtifactDTO['data'])
  const normalized = normalizeTurnAttachment(payload, fallbackId, undefined, conversationId)
  const objectRef = normalized.logicalPath || ''
  if (!objectRef) return null
  const filename =
    (normalized.name && normalized.name !== 'attachment' ? normalized.name : '') ||
    (typeof payload.filename === 'string' && payload.filename) ||
    (typeof payload.name === 'string' && payload.name) ||
    basenameFromRef(objectRef)
  if (!filename) return null
  return {
    kind: 'file',
    surface: 'files',
    timestamp,
    filename,
    objectRef,
    logicalPath: objectRef,
    mime: normalized.mime,
    description: normalized.description,
  }
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function appendHistoricalFileArtifacts(
  artifacts: Artifact[],
  rows: Array<Record<string, unknown>>,
  timestamp: number,
  fallbackPrefix: string,
  conversationId?: string,
): Artifact[] {
  let next = artifacts
  rows.forEach((row, index) => {
    const fileArtifact = normalizedFileArtifact(row, timestamp, `${fallbackPrefix}:${index}`, conversationId)
    if (!fileArtifact) return
    next = upsertArtifact(
      next,
      (item) => item.kind === 'file' && Boolean(fileArtifact.objectRef && item.objectRef === fileArtifact.objectRef),
      fileArtifact,
    )
  })
  return next
}

function collectHistoricalFileRows(
  payload: Record<string, unknown>,
  artifactText?: string,
  conversationId?: string,
): Array<Record<string, unknown>> {
  const rows: Array<Record<string, unknown>> = []

  function addFileRow(candidate: unknown, explicitFileContainer = false) {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return
    const row = candidate as Record<string, unknown>
    const event = row.event && typeof row.event === 'object' ? row.event as Record<string, unknown> : null
    const data = row.data && typeof row.data === 'object' ? row.data as Record<string, unknown> : null
    const parsedText = parseRecordTextJson(row)
    const artifactType = typeof row.artifact_type === 'string' ? row.artifact_type.trim() : ''

    if (event?.step === 'files' && data && Array.isArray(data.items)) {
      for (const item of data.items) addFileRow(item, true)
      return
    }
    const nextExplicitFileContainer = explicitFileContainer || artifactType === 'files'
    if (nextExplicitFileContainer && Array.isArray(row.files)) {
      for (const item of row.files) addFileRow(item, nextExplicitFileContainer)
    }
    if (Array.isArray(row.items)) {
      for (const item of row.items) addFileRow(item, nextExplicitFileContainer)
    }
    if (Array.isArray(row.blocks)) {
      for (const item of row.blocks) addFileRow(item, nextExplicitFileContainer)
    }
    if (nextExplicitFileContainer && data && Array.isArray(data.files)) {
      for (const item of data.files) addFileRow(item, nextExplicitFileContainer)
    }
    if (parsedText) {
      addFileRow(parsedText, explicitFileContainer)
    }

    const kind = typeof row.kind === 'string' ? row.kind : ''
    const logicalPath = canonicalPayloadRef(row, {}, conversationId)
    if (!logicalPath) return
    if (!nextExplicitFileContainer && kind !== 'file' && kind !== 'assistant.file') return
    if (
      kind === 'file' ||
      kind === 'assistant.file' ||
      explicitFileContainer ||
      nonEmptyString(row.filename) ||
      nonEmptyString(row.mime) ||
      nonEmptyString(row.mime_type)
    ) {
      rows.push(row)
    }
  }

  addFileRow(payload)
  const assistant = payload.assistant && typeof payload.assistant === 'object'
    ? payload.assistant as Record<string, unknown>
    : null
  if (assistant && Array.isArray(assistant.files)) {
    for (const item of assistant.files) addFileRow(item)
  }
  const turnLog = payload.turn_log && typeof payload.turn_log === 'object'
    ? payload.turn_log as Record<string, unknown>
    : null
  const turnLogAssistant = turnLog?.assistant && typeof turnLog.assistant === 'object'
    ? turnLog.assistant as Record<string, unknown>
    : null
  if (turnLogAssistant && Array.isArray(turnLogAssistant.files)) {
    for (const item of turnLogAssistant.files) addFileRow(item)
  }
  if (Array.isArray(payload.items)) {
    for (const item of payload.items) addFileRow(item)
  }
  if (Array.isArray(payload.blocks)) {
    for (const item of payload.blocks) addFileRow(item)
  }
  if (artifactText) {
    const parsed = safeJsonParse<unknown>(artifactText, null)
    addFileRow(parsed)
  }
  return rows
}

export function normalizeTurnAttachment(
  payload: Record<string, unknown>,
  fallbackId: string,
  file?: File,
  conversationId?: string,
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
    logicalPath: canonicalPayloadRef(payload, meta, conversationId),
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
    interruptedNotice: null,
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
      const turnLogElapsedMs = extractTurnLogElapsedMs(payload, artifact.data?.text)
      if (turnLogElapsedMs != null && turn.elapsedMs == null) {
        turn = { ...turn, elapsedMs: turnLogElapsedMs }
      }
      const historicalFileRows = collectHistoricalFileRows(payload, artifact.data?.text, conversation.conversation_id)
      if (historicalFileRows.length) {
        turn = {
          ...turn,
          artifacts: appendHistoricalFileArtifacts(
            turn.artifacts,
            historicalFileRows,
            ts,
            `history-file:${turnDto.turn_id}:${turn.artifacts.length}`,
            conversation.conversation_id,
          ),
        }
      }

      switch (artifact.type) {
        case 'chat:user': {
          const dataRecord = artifact.data && typeof artifact.data === 'object'
            ? artifact.data as Record<string, unknown>
            : {}
          const text =
            (typeof artifact.data?.text === 'string' && artifact.data.text) ||
            (typeof payload.text === 'string' && payload.text) ||
            ''
          const displayText = messageWithContextChips(text, dataRecord.contexts)
          const eventType =
            (typeof dataRecord.event_type === 'string' && dataRecord.event_type) ||
            (typeof payload.event_type === 'string' && payload.event_type) ||
            null
          const isAdditionalUserEvent = eventType === 'event.user.followup' || eventType === 'event.user.steer'
          if (turn.userMessage || isAdditionalUserEvent) {
            turn = {
              ...turn,
              createdAt: Math.min(turn.createdAt, ts),
              additionalUserMessages: [
                ...turn.additionalUserMessages,
                {
                  id: `stored-user:${turnDto.turn_id}:${turn.additionalUserMessages.length}`,
                  text: displayText,
                  timestamp: ts,
                  attachments: [],
                  eventType: eventType === 'event.user.steer' ? 'event.user.steer' : 'event.user.followup',
                },
              ],
            }
            currentUserSlot = 'additional'
            break
          }
          turn = {
            ...turn,
            createdAt: ts,
            userMessage: displayText,
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
          // The answer renders inline in the feed as a `final_answer:<attempt>`
          // timeline artifact, exactly like the live stream. A turn may hold
          // several assistant completions; each becomes its own block in order.
          const attempt = turn.artifacts.filter(
            (item) => item.kind === 'timeline' && /^final_answer:\d+$/.test(item.name),
          ).length
          turn = {
            ...turn,
            answer: text,
            artifacts: [
              ...turn.artifacts,
              { kind: 'timeline', surface: 'timeline', timestamp: ts, name: `final_answer:${attempt}`, markdown: text },
            ],
          }
          break
        }
        case 'artifact:assistant.file': {
          const normalized = normalizeTurnAttachment(
            payload,
            `assistant-file:${turnDto.turn_id}:${turn.artifacts.length}`,
            undefined,
            conversation.conversation_id,
          )
          const objectRef = normalized.logicalPath || ''
          if (!objectRef) break
          const fileArtifact: FileArtifact = {
            kind: 'file',
            surface: 'files',
            timestamp: ts,
            filename: normalized.name,
            objectRef,
            logicalPath: objectRef || null,
            mime: normalized.mime,
            description: normalized.description,
          }
          turn = {
            ...turn,
            artifacts: upsertArtifact(
              turn.artifacts,
              (item) =>
                item.kind === 'file' &&
                Boolean(fileArtifact.objectRef && item.objectRef === fileArtifact.objectRef),
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
              surface: 'links',
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
              surface: 'timeline',
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
        case 'artifact:conv.artifacts.events':
        case 'artifact:conv.artifacts.steps': {
          const items = Array.isArray(payload.items) ? payload.items : []
          let costUsd = turn.costUsd ?? null
          let elapsedMs = turn.elapsedMs ?? null
          for (const item of items) {
            if (!item || typeof item !== 'object') continue
            const row = item as Record<string, unknown>
            const type = historicalEventType(row)
            const data = historicalEventData(row)
            if (
              type === 'accounting.usage'
              && typeof data.cost_total_usd === 'number'
              && historicalEventMatchesTurn(row, conversation.conversation_id, turnDto.turn_id)
            ) {
              costUsd = data.cost_total_usd
            }
            if (
              type === 'chat.turn.summary'
              && typeof data.elapsed_ms === 'number'
              && historicalEventMatchesTurn(row, conversation.conversation_id, turnDto.turn_id)
            ) {
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
        surface: 'artifacts',
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
  /* Service/project broadcasts are delivered to every SSE subscriber. Chat
   * consumes accounting as a turn step only when it targets an existing local
   * turn. Server-side producers should also avoid sending non-chat accounting
   * over the chat_step route; this guard protects older or mixed deployments.
   * Next ownership field to add server-side: origin_agent_id / target_surface_ref. */
  if (isAccountingStep(env) && !eventTargetsExistingTurn(state, env)) {
    return state
  }
  const syncedState = syncConversationFromEnvelope(
    ensureTurn(state, env.conversation.turn_id, timestampValue(env.timestamp)),
    env,
  )
  const consentBanner = connectedAccountConsentBanner(env.data)
  const stateWithBanner = consentBanner
    ? upsertConsentBanner(syncedState, consentBanner)
    : syncedState
  return updateTurn(stateWithBanner, env.conversation.turn_id, (turn) => {
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
            surface: 'links',
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
          const filename = typeof item.filename === 'string' ? item.filename : ''
          const logicalPath = canonicalPayloadRef(item)
          if (!logicalPath || !filename) continue
          artifacts = upsertArtifact(artifacts, (artifact) => {
            if (artifact.kind !== 'file') return false
            return Boolean(logicalPath && artifact.objectRef === logicalPath)
          }, {
            kind: 'file',
            surface: 'files',
            timestamp,
            objectRef: logicalPath || '',
            filename,
            logicalPath,
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

    /* One-time recovery notice: a new turn that reclaimed a stale-open event
     * lane emits this step so the user knows the prior (unsaved) response is
     * being regenerated. Surfaced as a transient banner; never persisted. */
    const interruptedNotice = env.event.step === 'external_event.handler.reclaim'
      ? (env.event.markdown
          || (typeof env.data?.message === 'string' ? env.data.message : '')
          || turn.interruptedNotice
          || '')
      : turn.interruptedNotice

    return {
      ...turn,
      costUsd,
      elapsedMs,
      steps: {
        ...turn.steps,
        [env.event.step]: nextStep,
      },
      artifacts,
      interruptedNotice,
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
      case 'answer': {
        /* The final answer is just another timeline block in the chronological
         * feed — not a special pinned bubble. A turn can stream more than one
         * answer (the agent answers, then does more work and answers again);
         * the backend restarts the delta index at 0 for each, so a fresh index
         * 0 after the previous answer already has text marks a new attempt. Each
         * attempt is its own timeline artifact, so it renders in order and is
         * never concatenated into the previous answer. */
        const answerArtifacts = artifacts.filter(
          (artifact): artifact is TimelineArtifact =>
            artifact.kind === 'timeline' && /^final_answer:\d+$/.test(artifact.name),
        )
        const lastAnswer = answerArtifacts[answerArtifacts.length - 1]
        const lastAttempt = lastAnswer ? Number(lastAnswer.name.split(':')[1]) : -1
        const startsNewAttempt = index === 0 && !!lastAnswer && lastAnswer.markdown.length > 0 && textDelta.length > 0
        const attempt = !lastAnswer ? 0 : startsNewAttempt ? lastAttempt + 1 : lastAttempt
        const name = `final_answer:${attempt}`
        const current = artifacts.find(
          (artifact): artifact is TimelineArtifact => artifact.kind === 'timeline' && artifact.name === name,
        )
        const nextArtifact: TimelineArtifact = {
          kind: 'timeline',
          surface: 'timeline',
          timestamp: current?.timestamp ?? timestamp,
          name,
          markdown: `${current?.markdown || ''}${textDelta}`,
        }
        artifacts = upsertArtifact(
          artifacts,
          (artifact) => artifact.kind === 'timeline' && artifact.name === name,
          nextArtifact,
        )
        // Keep the scalar in sync (latest attempt) for the streaming indicator
        // and copy / history fallback.
        nextTurn.answer = nextArtifact.markdown
        nextTurn.state = 'running'
        break
      }
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
          surface: 'timeline',
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
          surface: 'artifacts',
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
            surface: 'artifacts',
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
        } else if (subtype === 'named_service.search_results') {
          const searchId = String(env.extra?.search_id || artifactName)
          const parsed = safeJsonParse<Record<string, unknown>>(textDelta, {})
          const nextArtifact: NamedServiceSearchArtifact = {
            kind: 'named_service_search',
            surface: 'artifacts',
            timestamp,
            searchId,
            name: artifactName,
            title,
            namespace: typeof parsed.namespace === 'string' ? parsed.namespace : undefined,
            searchScope: typeof parsed.search_scope === 'string' ? parsed.search_scope : undefined,
            query: typeof parsed.query === 'string' ? parsed.query : undefined,
            items: Array.isArray(parsed.items) ? (parsed.items as NamedServiceSearchItem[]) : [],
          }
          artifacts = upsertArtifact(
            artifacts,
            (artifact) => artifact.kind === 'named_service_search' && artifact.searchId === searchId,
            nextArtifact,
          )
          timelineBody = prettyJson(parsed)
          timelineFormat = 'json'
        } else if (subtype === 'web_fetch.results') {
          const executionId = String(env.extra?.execution_id || artifactName)
          const parsed = safeJsonParse<Record<string, unknown>>(textDelta, {})
          const nextArtifact: WebFetchArtifact = {
            kind: 'web_fetch',
            surface: 'artifacts',
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
            surface: 'artifacts',
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
