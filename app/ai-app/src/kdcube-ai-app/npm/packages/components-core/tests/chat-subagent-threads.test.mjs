import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  applySelectionPatch,
  chatActions,
  chatReducer,
  initialState,
  isSubagentsDisabled,
  mergeSelectionPatches,
  subagentLaneEventKind,
  subagentStampOf,
  subagentThreadChildId,
  subagentThreadsForTurn,
  subagentsTogglePatch,
} from '../dist/chat/index.js'

// ReAct subagents v2, client half: stamped child-conversation emissions
// multiplex into threads keyed by child conversation id (same reducer
// pipeline as the main lane, nested turn list), lane events drive the
// thread's status/milestones, and reload rebuilds the same threads from the
// parent turns' `forks` descriptors. Plus the helper-agents deny-key
// selection logic the picker toggle rides.

const STAMP = {
  child_conversation_id: 'conv_child_a',
  forked_from_conversation_id: 'conv_parent',
  forked_from_turn_id: 'turn_p1',
  charter_goal: 'Research the market',
}

function envelope({ type = 'chat.delta', stamp = STAMP, turnId = 'turn_c1', data = {}, delta, event } = {}) {
  return {
    type,
    timestamp: '2026-07-12T10:00:00Z',
    service: { request_id: 'req1' },
    conversation: { session_id: '', conversation_id: stamp?.child_conversation_id || 'conv_child_a', turn_id: turnId },
    event: event || { step: 'react', status: 'running' },
    data,
    ...(delta ? { delta, extra: {} } : {}),
    ...(stamp ? { subagent: stamp } : {}),
  }
}

function parentState() {
  return { ...initialState, conversationId: 'conv_parent' }
}

test('the stamp discriminates subagent traffic; main-lane envelopes carry none', () => {
  const stamped = envelope({ delta: { text: 'hi', marker: 'answer', index: 0 } })
  assert.equal(subagentStampOf(stamped).child_conversation_id, 'conv_child_a')
  const plain = envelope({ stamp: null, delta: { text: 'hi', marker: 'answer', index: 0 } })
  assert.equal(subagentStampOf(plain), null)
})

test('a stamped delta builds the thread with the SAME answer pipeline; main turns stay untouched', () => {
  let state = parentState()
  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'delta',
    envelope: envelope({ delta: { text: 'Part one ', marker: 'answer', index: 0 } }),
  }))
  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'delta',
    envelope: envelope({ delta: { text: 'part two.', marker: 'answer', index: 1 } }),
  }))
  const thread = state.threads.conv_child_a
  assert.ok(thread, 'thread keyed by child conversation id')
  assert.equal(thread.parentTurnId, 'turn_p1')
  assert.equal(thread.charterGoal, 'Research the market')
  assert.equal(thread.status, 'running')
  assert.equal(thread.hydration, 'live')
  assert.equal(thread.turns.length, 1)
  assert.equal(thread.turns[0].id, 'turn_c1')
  assert.equal(thread.turns[0].answer, 'Part one part two.')
  assert.equal(state.turns.length, 0, 'no phantom turn on the main lane')
})

test("another conversation's stamped traffic is dropped", () => {
  const foreign = { ...STAMP, forked_from_conversation_id: 'conv_other' }
  const state = chatReducer(parentState(), chatActions.subagentStreamEvent({
    kind: 'delta',
    envelope: envelope({ stamp: foreign, delta: { text: 'x', marker: 'answer', index: 0 } }),
  }))
  assert.deepEqual(state.threads, {})
})

test('fan-out: concurrent children anchor as separate threads under the fork turn, in fork order', () => {
  let state = parentState()
  const stampB = { ...STAMP, child_conversation_id: 'conv_child_b', charter_goal: 'Draft the summary' }
  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'start',
    envelope: envelope({ type: 'chat.start', data: { message: '' } }),
  }))
  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'start',
    envelope: { ...envelope({ type: 'chat.start', stamp: stampB, turnId: 'turn_c2', data: { message: '' } }), timestamp: '2026-07-12T10:00:05Z' },
  }))
  assert.equal(Object.keys(state.threads).length, 2)
  const anchored = subagentThreadsForTurn(state.threads, 'turn_p1')
  assert.deepEqual(anchored.map((t) => t.childConversationId), ['conv_child_a', 'conv_child_b'])
  assert.deepEqual(subagentThreadsForTurn(state.threads, 'turn_p9'), [])
})

test('lane events drive status and milestones: charter/contribution/converged/failed', () => {
  assert.equal(subagentLaneEventKind(envelope({ data: { event: { type: 'subagent.contribution' } } })), 'contribution')
  assert.equal(subagentLaneEventKind(envelope({ type: 'subagent.converged' })), 'converged')

  let state = parentState()
  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'step',
    envelope: envelope({ data: { event: { type: 'subagent.charter' }, text: 'Research the market' } }),
  }))
  assert.equal(state.threads.conv_child_a.status, 'running')

  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'step',
    envelope: envelope({ data: { event: { type: 'subagent.contribution' }, text: 'Found three sources', refs: ['conv:fi:conv_child_a.turn_c1/f.md'] } }),
  }))
  assert.equal(state.threads.conv_child_a.contributions.length, 1)
  assert.equal(state.threads.conv_child_a.contributions[0].text, 'Found three sources')
  assert.deepEqual(state.threads.conv_child_a.contributions[0].refs, ['conv:fi:conv_child_a.turn_c1/f.md'])

  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'step',
    envelope: envelope({ data: { event: { type: 'subagent.converged' }, text: 'Charter completed.' } }),
  }))
  assert.equal(state.threads.conv_child_a.status, 'converged')
  assert.equal(state.threads.conv_child_a.statusDetail, 'Charter completed.')

  const failed = chatReducer(parentState(), chatActions.subagentStreamEvent({
    kind: 'step',
    envelope: envelope({ data: { event: { type: 'subagent.failed' }, reason: 'budget exhausted' } }),
  }))
  assert.equal(failed.threads.conv_child_a.status, 'failed')
  assert.equal(failed.threads.conv_child_a.statusDetail, 'budget exhausted')
})

const PARENT_CONVERSATION = {
  conversation_id: 'conv_parent',
  conversation_title: 'Market work',
  turns: [
    {
      turn_id: 'turn_p1',
      artifacts: [
        { type: 'chat:user', ts: '2026-07-12T09:00:00Z', data: { text: 'research this' } },
      ],
      forks: [
        { child_conversation_id: 'conv_child_a', charter_goal: 'Research the market', forked_at: '2026-07-12T09:01:00Z' },
        { child_conversation_id: 'conv_child_b', charter_goal: 'Draft the summary', forked_at: '2026-07-12T09:02:00Z' },
      ],
    },
    {
      turn_id: 'turn_p2',
      artifacts: [
        {
          type: 'artifact:conv.artifacts.events',
          ts: '2026-07-12T09:10:00Z',
          data: {
            payload: {
              items: [
                {
                  type: 'external_event',
                  event: { type: 'subagent.converged' },
                  data: { child_conversation_id: 'conv_child_a', text: 'Completed the charter.' },
                },
              ],
            },
          },
        },
      ],
    },
  ],
}

test('reload: `forks` descriptors become collapsed stubs at their turns; stored completions fold status', () => {
  const state = chatReducer({ ...initialState }, chatActions.hydrateConversation({ conversation: PARENT_CONVERSATION }))
  const threadA = state.threads.conv_child_a
  const threadB = state.threads.conv_child_b
  assert.ok(threadA && threadB)
  assert.equal(threadA.hydration, 'stub')
  assert.equal(threadA.parentTurnId, 'turn_p1')
  assert.equal(threadA.charterGoal, 'Research the market')
  assert.equal(threadA.status, 'converged', 'terminal event stored on a later turn folds into the stub')
  assert.equal(threadA.statusDetail, 'Completed the charter.')
  assert.equal(threadB.status, 'unknown', 'no stored completion -> no guessed status')
  assert.equal(threadA.turns.length, 0, 'collapsed stub carries no turns until expanded')
})

test('expanding a stub hydrates the fetched child conversation through the same historical pipeline', () => {
  let state = chatReducer({ ...initialState }, chatActions.hydrateConversation({ conversation: PARENT_CONVERSATION }))
  state = chatReducer(state, chatActions.subagentThreadLoading('conv_child_a'))
  assert.equal(state.threads.conv_child_a.hydration, 'loading')
  state = chatReducer(state, chatActions.subagentThreadHydrated({
    childConversationId: 'conv_child_a',
    conversation: {
      conversation_id: 'conv_child_a',
      forked_from: { conversation_id: 'conv_parent', turn_id: 'turn_p1' },
      turns: [
        {
          turn_id: 'turn_c1',
          artifacts: [
            { type: 'chat:assistant', ts: '2026-07-12T09:05:00Z', data: { text: 'Here are the findings.' } },
          ],
        },
      ],
    },
  }))
  const thread = state.threads.conv_child_a
  assert.equal(thread.hydration, 'ready')
  assert.equal(thread.turns.length, 1)
  assert.equal(thread.turns[0].answer, 'Here are the findings.')
  // a failed fetch is retryable state, not a lost thread
  const errored = chatReducer(state, chatActions.subagentThreadLoadError({
    childConversationId: 'conv_child_b',
    error: 'fetch failed',
  }))
  assert.equal(errored.threads.conv_child_b.hydration, 'error')
  assert.equal(errored.threads.conv_child_b.hydrationError, 'fetch failed')
})

test('threads reset on conversation switch', () => {
  let state = chatReducer({ ...initialState }, chatActions.hydrateConversation({ conversation: PARENT_CONVERSATION }))
  assert.equal(Object.keys(state.threads).length, 2)
  state = chatReducer(state, chatActions.startNewConversation())
  assert.deepEqual(state.threads, {})
})

// ── Fix A: widget deltas (web_search / web_fetch) thread by the stamp OR the
// child conversation identity, regardless of sub_type ─────────────────────────

test('a stamped web-search widget delta threads regardless of sub_type', () => {
  let state = parentState()
  const webSearch = {
    ...envelope({ delta: { text: JSON.stringify({ results: [{ url: 'https://a' }], objective: 'x', queries: ['q'] }), marker: 'subsystem', index: 0 } }),
    extra: { sub_type: 'web_search.filtered_results', search_id: 's1', artifact_name: 'Web Search' },
  }
  state = chatReducer(state, chatActions.subagentStreamEvent({ kind: 'delta', envelope: webSearch }))
  const thread = state.threads.conv_child_a
  assert.ok(thread, 'the stamped widget delta opened/fed the thread')
  assert.equal(state.turns.length, 0, 'the widget delta stayed out of the main lane')
  const artifact = thread.turns[0]?.artifacts.find((a) => a.kind === 'web_search')
  assert.ok(artifact, 'a web_search artifact built inside the thread turn')
  assert.equal(artifact.items.length, 1)
})

test('an UNSTAMPED child widget delta threads by the child conversation identity', () => {
  let state = parentState()
  // The thread opens on a stamped thinking delta the backend did stamp.
  state = chatReducer(state, chatActions.subagentStreamEvent({
    kind: 'delta',
    envelope: envelope({ delta: { text: 'thinking…', marker: 'thinking', index: 0 } }),
  }))
  assert.ok(state.threads.conv_child_a)
  // A web_fetch delta the backend did NOT stamp still carries the child's own
  // conversation id — it folds into that child's thread, not the main lane.
  const unstamped = {
    ...envelope({ stamp: null, delta: { text: JSON.stringify({ urls: [{ url: 'https://a', status: 'success' }] }), marker: 'subsystem', index: 0 } }),
    extra: { sub_type: 'web_fetch.results', execution_id: 'e1', artifact_name: 'Web Fetch' },
  }
  assert.equal(subagentThreadChildId(unstamped, state.threads), 'conv_child_a')
  state = chatReducer(state, chatActions.subagentStreamEvent({ kind: 'delta', envelope: unstamped }))
  assert.equal(state.turns.length, 0, 'the unstamped child delta never reached the main lane')
  const artifact = state.threads.conv_child_a.turns[0]?.artifacts.find((a) => a.kind === 'web_fetch')
  assert.ok(artifact, 'the web_fetch artifact folded into the existing thread')
})

test('a main-lane envelope with no stamp and no matching thread does not thread', () => {
  const state = parentState()
  const mainLane = {
    ...envelope({ stamp: null, delta: { text: 'hi', marker: 'answer', index: 0 } }),
    conversation: { session_id: '', conversation_id: 'conv_parent', turn_id: 'turn_p1' },
  }
  assert.equal(subagentThreadChildId(mainLane, state.threads), null)
})

// ── Fix C: the delegating agent's chosen persona name flows to the thread ──────

test('the stamp agent_title names the thread; a reload fork descriptor carries it too', () => {
  const stamp = { ...STAMP, agent_title: 'Science news researcher' }
  const live = chatReducer(parentState(), chatActions.subagentStreamEvent({
    kind: 'start',
    envelope: envelope({ type: 'chat.start', stamp, data: { message: '' } }),
  }))
  assert.equal(live.threads.conv_child_a.agentTitle, 'Science news researcher')

  const conversation = {
    conversation_id: 'conv_parent',
    turns: [{
      turn_id: 'turn_p1',
      artifacts: [],
      forks: [{ child_conversation_id: 'conv_child_a', charter_goal: 'Research', agent_title: 'Science news researcher', forked_at: '2026-07-12T09:01:00Z' }],
    }],
  }
  const reloaded = chatReducer({ ...initialState }, chatActions.hydrateConversation({ conversation }))
  assert.equal(reloaded.threads.conv_child_a.agentTitle, 'Science news researcher')
})

// ── Fix B: a subagent completion opens an agent-authored continuation turn ─────

function startEnvelope({ turnId = 'turn_cont', data }) {
  return {
    type: 'chat.start',
    timestamp: '2026-07-12T10:00:00Z',
    service: { request_id: 'r1' },
    conversation: { session_id: '', conversation_id: 'conv_parent', turn_id: turnId },
    event: { step: 'turn', status: 'started' },
    data,
  }
}

test('an agent-authored continuation turn hydrates the helper persona (live start)', () => {
  const state = chatReducer(
    { ...initialState, conversationId: 'conv_parent' },
    chatActions.chatStarted(startEnvelope({
      data: {
        message: 'subagent.converged (react.subagent)',
        authored_by: 'agent',
        agent_title: 'Science news researcher',
        handoff: 'Found three fresh sources on the topic.',
      },
    })),
  )
  const turn = state.turns.find((t) => t.id === 'turn_cont')
  assert.ok(turn.authoredBy, 'the turn is agent-authored, not the user')
  assert.equal(turn.authoredBy.agentTitle, 'Science news researcher')
  assert.equal(turn.authoredBy.handoff, 'Found three fresh sources on the topic.')
})

test('an agent-authored turn with no contribution carries the persona but no handoff', () => {
  const state = chatReducer(
    { ...initialState, conversationId: 'conv_parent' },
    chatActions.chatStarted(startEnvelope({
      data: { message: 'subagent.converged (react.subagent)', authored_by: 'agent', agent_title: 'Helper' },
    })),
  )
  const turn = state.turns[0]
  assert.equal(turn.authoredBy.agentTitle, 'Helper')
  assert.equal(turn.authoredBy.handoff, null)
})

test('a user-authored turn carries no persona', () => {
  const state = chatReducer(
    { ...initialState, conversationId: 'conv_parent' },
    chatActions.chatStarted(startEnvelope({ data: { message: 'hello there' } })),
  )
  assert.equal(state.turns[0].authoredBy ?? null, null)
})

test('reload: a stored agent-authored triggering input hydrates the persona', () => {
  const conversation = {
    conversation_id: 'conv_parent',
    turns: [{
      turn_id: 'turn_cont',
      artifacts: [{
        type: 'chat:user',
        ts: '2026-07-12T09:05:00Z',
        data: {
          text: 'subagent.converged (react.subagent)',
          authored_by: 'agent',
          agent_title: 'Science news researcher',
          handoff: 'Found three fresh sources on the topic.',
        },
      }],
    }],
  }
  const state = chatReducer({ ...initialState }, chatActions.hydrateConversation({ conversation }))
  const turn = state.turns[0]
  assert.ok(turn.authoredBy)
  assert.equal(turn.authoredBy.agentTitle, 'Science news researcher')
  assert.equal(turn.authoredBy.handoff, 'Found three fresh sources on the topic.')
})

test('helper-agents deny key: toggle patch, optimistic flip, merge, and re-enable', () => {
  // offered + untouched -> on; the toggle produces the deny patch
  assert.equal(isSubagentsDisabled({}), false)
  assert.deepEqual(subagentsTogglePatch({}), { subagents: true })
  // optimistic flip mirrors the server merge
  const off = applySelectionPatch({}, { subagents: true })
  assert.deepEqual(off, { subagents: true })
  assert.equal(isSubagentsDisabled(off), true)
  // re-enable clears the stored key entirely
  const on = applySelectionPatch(off, subagentsTogglePatch(off))
  assert.deepEqual(on, {})
  // debounced saves merge; later toggle wins; other categories ride along
  const merged = mergeSelectionPatches({ subagents: true, skills: { s1: true } }, { subagents: false })
  assert.deepEqual(merged, { skills: { s1: true }, subagents: false })
  // the persisted body is the patch minus the model pick — the deny key rides
  // `disabled` exactly like the other categories
  const { model, ...disabled } = { ...merged, model: null }
  assert.equal(model, null)
  assert.deepEqual(disabled, { skills: { s1: true }, subagents: false })
})
