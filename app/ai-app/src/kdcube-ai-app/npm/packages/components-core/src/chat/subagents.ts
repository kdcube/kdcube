/**
 * Subagent threads — the client half of ReAct subagents v2 threaded comm.
 *
 * LIVE: every child-conversation emission arrives on the PARENT conversation's
 * channel carrying a `subagent` envelope stamp
 * (`{child_conversation_id, forked_from_conversation_id, forked_from_turn_id,
 * charter_goal}`) while the envelope's own `conversation` identity is the
 * CHILD's. The engine multiplexes on the stamp: stamped envelopes route here
 * instead of the main-lane reducers, and each child conversation accumulates
 * as a `SubagentThread` (keyed by child conversation id) whose turns are built
 * by the SAME pure `apply*` reducers the main lane uses — a thread turn IS a
 * regular turn, drawn nested under the fork turn.
 *
 * RELOAD: fetched parent turns carry `forks` descriptors
 * (`{child_conversation_id, charter_goal, forked_at}`); each becomes a
 * collapsed thread stub. Expanding a stub fetches the child conversation
 * through the same conversation-fetch endpoint and hydrates its turns with
 * `hydrateHistoricalConversation` — live and reload render the same source.
 *
 * Thread status rides the `subagent.charter` / `subagent.contribution` /
 * `subagent.converged` / `subagent.failed` lane events (stamped like all
 * subagent traffic): charter opens `running`, contributions are the thread's
 * milestones, converged/failed are terminal.
 */

import type {
  BaseEnvelope,
  ChatCompleteEnvelope,
  ChatDeltaEnvelope,
  ChatErrorEnvelope,
  ChatStartEnvelope,
  ChatStepEnvelope,
  ConversationDTO,
  ConversationTurnDTO,
  SubagentEnvelopeStamp,
} from './protocol.ts'
import type {
  ChatState,
  SubagentContribution,
  SubagentThread,
  SubagentThreadStatus,
} from './state.ts'
import { initialState } from './state.ts'
import {
  applyChatComplete,
  applyChatDelta,
  applyChatError,
  applyChatStart,
  applyChatStep,
  extractPayload,
  hydrateHistoricalConversation,
} from './reducers.ts'
import { timestampValue } from './util.ts'

/** Which engine stream handler an envelope came through. */
export type SubagentStreamKind = 'start' | 'step' | 'delta' | 'complete' | 'error'

/** The `subagent.*` lane-event families the client reacts to. */
export type SubagentLaneEventKind = 'charter' | 'contribution' | 'converged' | 'failed'

function recordOf(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function textOf(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** The subagent stamp of an envelope, or null for main-lane traffic. Reads the
 *  top-level `subagent` field (the contract) with a `data.subagent` fallback
 *  for relays that tuck the stamp into the data record. */
export function subagentStampOf(env: BaseEnvelope): SubagentEnvelopeStamp | null {
  const raw = recordOf(env.subagent) ?? recordOf(recordOf(env.data)?.subagent)
  if (!raw) return null
  const childId = textOf(raw.child_conversation_id) || textOf(env.conversation?.conversation_id)
  if (!childId) return null
  return {
    ...raw,
    child_conversation_id: childId,
    ...(textOf(raw.forked_from_conversation_id)
      ? { forked_from_conversation_id: textOf(raw.forked_from_conversation_id) }
      : {}),
    ...(textOf(raw.forked_from_turn_id)
      ? { forked_from_turn_id: textOf(raw.forked_from_turn_id) }
      : {}),
    ...(textOf(raw.charter_goal) ? { charter_goal: textOf(raw.charter_goal) } : {}),
    ...(textOf(raw.agent_title) ? { agent_title: textOf(raw.agent_title) } : {}),
  }
}

/** The child conversation an envelope belongs to, if any: the stamp names it
 *  directly; an emission the backend didn't stamp (some widget deltas ride a
 *  contextvar communicator that isn't the stamping one) still carries the
 *  CHILD's own `conversation.conversation_id`, so it folds into a thread that
 *  child already opened. The stamp is what routes; identity is the fallback so
 *  routing is total regardless of emission sub_type. */
export function subagentThreadChildId(
  env: BaseEnvelope,
  threads: Record<string, SubagentThread>,
): string | null {
  const stamp = subagentStampOf(env)
  if (stamp) return stamp.child_conversation_id
  const conversationId = textOf(env.conversation?.conversation_id)
  return conversationId && threads[conversationId] ? conversationId : null
}

const LANE_EVENT_PATTERN = /^subagent\.(charter|contribution|converged|failed)$/

function laneKindFromToken(token: unknown): SubagentLaneEventKind | null {
  const match = LANE_EVENT_PATTERN.exec(textOf(token))
  return match ? (match[1] as SubagentLaneEventKind) : null
}

/** The `subagent.*` semantic family of an envelope, if it carries one. The
 *  semantic type may ride the envelope type, the step name, or the nested
 *  external-event record (`data.event.type` — the lane transport's home). */
export function subagentLaneEventKind(env: BaseEnvelope): SubagentLaneEventKind | null {
  const data = recordOf(env.data)
  const nestedEvent = recordOf(data?.event)
  return (
    laneKindFromToken(env.type)
    ?? laneKindFromToken(env.event?.step)
    ?? laneKindFromToken(data?.type)
    ?? laneKindFromToken(nestedEvent?.type)
  )
}

/** Human body of a subagent lane event, wherever the relay put it. */
function laneEventText(env: BaseEnvelope): string {
  const data = recordOf(env.data)
  const nestedEvent = recordOf(data?.event)
  const nestedPayloadEvent = recordOf(recordOf(nestedEvent?.payload)?.event)
  return (
    textOf(data?.text)
    || textOf(data?.report)
    || textOf(data?.reason)
    || textOf(nestedPayloadEvent?.text)
    || textOf(nestedEvent?.text)
    || textOf(env.event?.markdown)
  )
}

function laneEventRefs(env: BaseEnvelope): string[] {
  const data = recordOf(env.data)
  const nestedEvent = recordOf(data?.event)
  const nestedPayloadEvent = recordOf(recordOf(nestedEvent?.payload)?.event)
  const raw = data?.refs ?? nestedPayloadEvent?.refs
  return Array.isArray(raw) ? raw.map((item) => textOf(item)).filter(Boolean) : []
}

function createThread(input: {
  childConversationId: string
  parentTurnId: string
  parentConversationId?: string | null
  agentTitle?: string
  charterGoal?: string
  forkedAt: number
  status?: SubagentThreadStatus
  hydration?: SubagentThread['hydration']
}): SubagentThread {
  return {
    childConversationId: input.childConversationId,
    parentTurnId: input.parentTurnId,
    parentConversationId: input.parentConversationId ?? null,
    agentTitle: input.agentTitle || '',
    charterGoal: input.charterGoal || '',
    forkedAt: input.forkedAt,
    status: input.status ?? 'running',
    statusDetail: null,
    contributions: [],
    turns: [],
    hydration: input.hydration ?? 'live',
    hydrationError: null,
  }
}

function appendContribution(
  thread: SubagentThread,
  contribution: SubagentContribution,
): SubagentThread {
  if (thread.contributions.some((item) => item.id === contribution.id)) return thread
  return { ...thread, contributions: [...thread.contributions, contribution] }
}

/** Route one stamped envelope into its thread. Pure `ChatState → ChatState`:
 *  ensures the thread (fan-out threads coexist keyed by child id), folds
 *  lane-event status/milestones, and runs plain child stream traffic through
 *  the SAME apply* reducers as the main lane over the thread's own turn list. */
export function applySubagentEnvelope(
  state: ChatState,
  kind: SubagentStreamKind,
  env: BaseEnvelope,
): ChatState {
  const stamp = subagentStampOf(env)
  /* Route by the stamp when present; otherwise by the child's own conversation
   * identity matching a live thread (an emission the backend didn't stamp). */
  const childId = stamp?.child_conversation_id
    || subagentThreadChildId(env, state.threads)
  if (!childId) return state
  /* Another conversation's subagent traffic (the socket is per-user, the
   * thread model is per-open-conversation): drop it. Only stamped traffic
   * names its parent; an identity-matched emission already belongs to a
   * thread of THIS conversation. */
  if (
    stamp
    && state.conversationId
    && stamp.forked_from_conversation_id
    && stamp.forked_from_conversation_id !== state.conversationId
  ) {
    return state
  }
  const timestamp = timestampValue(env.timestamp)
  let thread = state.threads[childId]
    ?? createThread({
      childConversationId: childId,
      parentTurnId: stamp?.forked_from_turn_id || '',
      parentConversationId: stamp?.forked_from_conversation_id || state.conversationId,
      agentTitle: stamp?.agent_title,
      charterGoal: stamp?.charter_goal,
      forkedAt: timestamp,
    })
  /* A reload stub receiving live traffic becomes a live thread; the stamp may
   * also fill anchors the stub creation didn't know. */
  if (thread.hydration === 'stub') thread = { ...thread, hydration: 'live' }
  if (!thread.parentTurnId && stamp?.forked_from_turn_id) {
    thread = { ...thread, parentTurnId: stamp.forked_from_turn_id }
  }
  if (!thread.charterGoal && stamp?.charter_goal) {
    thread = { ...thread, charterGoal: stamp.charter_goal }
  }
  if (!thread.agentTitle && stamp?.agent_title) {
    thread = { ...thread, agentTitle: stamp.agent_title }
  }

  const laneKind = subagentLaneEventKind(env)
  if (laneKind) {
    const text = laneEventText(env)
    switch (laneKind) {
      case 'charter': {
        thread = { ...thread, status: 'running' }
        if (!thread.charterGoal && text) thread = { ...thread, charterGoal: text }
        break
      }
      case 'contribution': {
        const data = recordOf(env.data)
        const id = textOf(data?.event_id)
          || `${env.conversation?.turn_id || childId}:${timestamp}`
        const refs = laneEventRefs(env)
        thread = appendContribution(thread, {
          id,
          timestamp,
          text,
          ...(refs.length ? { refs } : {}),
        })
        break
      }
      case 'converged': {
        thread = { ...thread, status: 'converged', statusDetail: text || thread.statusDetail }
        break
      }
      case 'failed': {
        thread = { ...thread, status: 'failed', statusDetail: text || thread.statusDetail }
        break
      }
    }
    return { ...state, threads: { ...state.threads, [childId]: thread } }
  }

  /* Plain child stream traffic: the same delta/step/event pipeline as the
   * main lane, run over a scratch state that owns the thread's turn list. */
  let scratch: ChatState = {
    ...initialState,
    conversationId: childId,
    turns: thread.turns,
  }
  switch (kind) {
    case 'start':
      scratch = applyChatStart(scratch, env as ChatStartEnvelope)
      break
    case 'step':
      scratch = applyChatStep(scratch, env as ChatStepEnvelope)
      break
    case 'delta':
      scratch = applyChatDelta(scratch, env as ChatDeltaEnvelope)
      break
    case 'complete':
      scratch = applyChatComplete(scratch, env as ChatCompleteEnvelope)
      break
    case 'error':
      scratch = applyChatError(scratch, env as ChatErrorEnvelope)
      break
  }
  if (scratch.turns !== thread.turns) {
    thread = { ...thread, turns: scratch.turns }
  }
  return { ...state, threads: { ...state.threads, [childId]: thread } }
}

// ── reload reconstruction ────────────────────────────────────────────────────

interface ForkDescriptorLike {
  child_conversation_id?: unknown
  charter_goal?: unknown
  agent_title?: unknown
  forked_at?: unknown
}

function forkStubFromDescriptor(
  turnId: string,
  conversationId: string,
  raw: ForkDescriptorLike,
): SubagentThread | null {
  const childId = textOf(raw.child_conversation_id)
  if (!childId) return null
  const forkedAtRaw = raw.forked_at
  const forkedAt = typeof forkedAtRaw === 'number'
    ? forkedAtRaw
    : typeof forkedAtRaw === 'string' && forkedAtRaw
      ? timestampValue(forkedAtRaw)
      : Date.now()
  return createThread({
    childConversationId: childId,
    parentTurnId: turnId,
    parentConversationId: conversationId,
    agentTitle: textOf(raw.agent_title),
    charterGoal: textOf(raw.charter_goal),
    forkedAt,
    status: 'unknown',
    hydration: 'stub',
  })
}

function forkDescriptorsOfTurn(turnDto: ConversationTurnDTO): ForkDescriptorLike[] {
  const out: ForkDescriptorLike[] = []
  if (Array.isArray(turnDto.forks)) {
    for (const item of turnDto.forks) {
      const record = recordOf(item)
      if (record) out.push(record)
    }
  }
  /* Defensive twin source: the fork records live on the turn log payload
   *  (`forks`, or nested `turn_log.forks`) — read them when the fetch
   *  response doesn't lift them to the turn DTO. */
  for (const artifact of turnDto.artifacts || []) {
    const payload = extractPayload(artifact.data)
    const candidates = [payload.forks, recordOf(payload.turn_log)?.forks]
    for (const candidate of candidates) {
      if (!Array.isArray(candidate)) continue
      for (const item of candidate) {
        const record = recordOf(item)
        if (record && textOf(record.child_conversation_id)) out.push(record)
      }
    }
  }
  return out
}

/** A folded `subagent.*` occurrence inside a stored events/steps artifact row:
 *  terminal events set the reconstructed thread's status, contributions become
 *  its milestones — so a reloaded thread shows the same header a live one did. */
function subagentSignalFromRow(row: Record<string, unknown>): {
  kind: SubagentLaneEventKind
  childId: string
  text: string
  timestamp: number
} | null {
  const event = recordOf(row.event)
  const data = recordOf(row.data)
  const payload = recordOf(row.payload)
  const nestedEvent = recordOf(payload?.event) ?? recordOf(data?.event)
  const kind = laneKindFromToken(row.type)
    ?? laneKindFromToken(event?.type)
    ?? laneKindFromToken(event?.step)
    ?? laneKindFromToken(data?.type)
    ?? laneKindFromToken(nestedEvent?.type)
  if (!kind) return null
  const nestedPayloadEvent = recordOf(recordOf(nestedEvent?.payload)?.event)
  const facts = [data, payload, nestedPayloadEvent, row]
  let childId = ''
  let text = ''
  for (const source of facts) {
    if (!source) continue
    childId = childId || textOf(source.child_conversation_id)
    text = text || textOf(source.text) || textOf(source.report) || textOf(source.reason)
  }
  if (!childId) return null
  const ts = row.ts ?? row.timestamp ?? data?.timestamp
  return {
    kind,
    childId,
    text,
    timestamp: typeof ts === 'number' ? ts : timestampValue(typeof ts === 'string' ? ts : undefined),
  }
}

/** Rebuild the conversation's thread map from a fetched parent conversation:
 *  one collapsed stub per fork descriptor, statuses/milestones folded in from
 *  the stored `subagent.*` occurrences anywhere in the parent's turns. */
export function subagentThreadsFromConversation(
  conversation: ConversationDTO,
): Record<string, SubagentThread> {
  const threads: Record<string, SubagentThread> = {}
  for (const turnDto of conversation.turns || []) {
    for (const descriptor of forkDescriptorsOfTurn(turnDto)) {
      const stub = forkStubFromDescriptor(turnDto.turn_id, conversation.conversation_id, descriptor)
      if (stub && !threads[stub.childConversationId]) {
        threads[stub.childConversationId] = stub
      }
    }
  }
  if (!Object.keys(threads).length) return threads
  /* Status + milestones from the stored event stream (the folded lane
   * events live in the events/steps artifacts of whichever turn consumed
   * them — scan them all; unmatched child ids are other turns' business). */
  for (const turnDto of conversation.turns || []) {
    for (const artifact of turnDto.artifacts || []) {
      if (artifact.type !== 'artifact:conv.artifacts.events' && artifact.type !== 'artifact:conv.artifacts.steps') continue
      const payload = extractPayload(artifact.data)
      const items = Array.isArray(payload.items) ? payload.items : []
      for (const item of items) {
        const row = recordOf(item)
        if (!row) continue
        const signal = subagentSignalFromRow(row)
        if (!signal) continue
        const thread = threads[signal.childId]
        if (!thread) continue
        if (signal.kind === 'converged' || signal.kind === 'failed') {
          threads[signal.childId] = {
            ...thread,
            status: signal.kind,
            statusDetail: signal.text || thread.statusDetail,
          }
        } else if (signal.kind === 'contribution') {
          threads[signal.childId] = appendContribution(thread, {
            id: `stored:${signal.childId}:${thread.contributions.length}`,
            timestamp: signal.timestamp,
            text: signal.text,
          })
        }
      }
    }
  }
  return threads
}

/** Fold a fetched CHILD conversation into its thread: the child's turns
 *  hydrate through the same historical pipeline as a main conversation. */
export function hydrateSubagentThread(
  thread: SubagentThread,
  conversation: ConversationDTO,
): SubagentThread {
  const turns = hydrateHistoricalConversation(conversation)
  let status = thread.status
  if (status === 'unknown' && turns.some((turn) => turn.state === 'error')) {
    status = 'failed'
  }
  return {
    ...thread,
    turns,
    status,
    hydration: 'ready',
    hydrationError: null,
  }
}

/** The threads anchored under one parent turn, oldest fork first (fan-out
 *  renders them in delegate order). */
export function subagentThreadsForTurn(
  threads: Record<string, SubagentThread>,
  turnId: string,
): SubagentThread[] {
  return Object.values(threads)
    .filter((thread) => thread.parentTurnId === turnId)
    .sort((left, right) => left.forkedAt - right.forkedAt)
}
