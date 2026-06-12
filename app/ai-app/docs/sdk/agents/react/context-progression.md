---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-progression.md
title: "Context Progression"
summary: "How context is built and updated during a turn."
tags: ["sdk", "agents", "react", "context", "progression"]
keywords: ["turn progression", "fetch_ctx", "tool calls", "context updates"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-layout.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/micro-agents-and-subagents-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/turn-log-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/session-view-README.md
---
# Context Progression & Compaction

This describes how context is built and updated during a turn.

## Cache Progression Across Calls
The rendered timeline progresses across calls, but prompt-cache reuse depends
on the exact prefix sent to the model. That prefix starts with system and
instruction bytes before any timeline block.

```
Turn 1, main agent

  [SYS main v1]
  [timeline: prior history]
  [current user prompt]
  [round/tool blocks]
  [ANNOUNCE 1 - uncached tail]

  writes cache checkpoints inside the stable timeline stream


Turn 2, same main agent, same instruction

  [SYS main v1]                              same prefix start
  [cached timeline prefix from Turn 1]        cache hit possible
  [new current user prompt]
  [ANNOUNCE 2 - uncached tail]                current state, not cached


Turn 2, same timeline but per-user system suffix changed

  [SYS main v1 + user-specific suffix]        different prefix bytes
  [same cached timeline content]              cache hit is not shared
  [ANNOUNCE 2 - uncached tail]


Round 2, same user but selected tools changed

  [SYS main v1]
  [user suffix]
  [tools: web, python, memory]                changed before timeline
  [same timeline content]                     downstream cache miss
  [ANNOUNCE 2 - uncached tail]


Subagent call

  [SYS subagent v1]                           separate prefix
  [handoff summary / refs / copied slice]      separate cache story
  [subtask prompt]
```

Important consequences:

- A subagent is not a cheap continuation of the main agent cache. It is another
  model request with its own system/instruction envelope and its own cache
  checkpoints.
- A per-user custom instruction suffix in the system/instruction envelope makes
  the cache prefix user-specific. That may be correct for behavior, but it
  prevents cross-user prefix sharing.
- Tool catalogs and skill catalogs are usually rendered in the same instruction
  envelope. If they are user-selectable or can change between rounds, cache
  reuse after that catalog segment is lost for every changed selection.
- Current derived state belongs in ANNOUNCE or another current tail block when
  it must be visible on this call but should not rewrite the stable cache.
- Adding data to a subagent requires work: generate a summary, pass refs for
  later pull/read, or copy a precise timeline slice. The precise option is
  cheaper at model time than copying everything, but it needs a mechanism and
  adds latency.

## At Turn Start
1) `ContextBrowser.load_context(...)` fetches:
   - recent turn logs
   - recent `conv.range.summary` artifacts from the index
   - the latest timeline payload cursor (`last_external_event_id` / `last_external_event_seq`)
2) Browser builds:
   - `history_blocks` (older turns + summaries)
   - `current_turn_blocks` (user prompt + attachments)
   - folds any unread external events from the shared conversation event source into the timeline snapshot
3) Browser caches these blocks per (conversation_id, turn_id).

## During the Turn
- Agents request blocks via:
  `timeline(...)`
- Downstream agents append **in-turn progress blocks** via `ContextBrowser.contribute(...)`:
  - These blocks represent work done so far in response to the current user request.
  - Examples: gate/coordinator decisions, react tool calls/results.
  - When `persist=True`, they are stored in the turn log blocks for next-turn reconstruction.
- React rounds append `react.tool.call` / `react.tool.result` blocks as contributions.
- While a turn is active, the timeline owner can also fold external `followup` / `steer`
  events into the same in-memory timeline. These become `user.followup` / `user.steer`
  blocks and trigger `on_timeline_event(...)` hooks.
- If a live reactive event arrives after a visible completion attempt, the same turn may later
  append another `assistant.completion`. These completions are persisted individually.
- Final/exit answer attempts may also emit `channel:summary`. Those summaries are
  contributed immediately as `conv.working.summary` blocks in the same turn,
  associated with that completion attempt. Tool/code execution rounds should not
  emit working summaries just because they are intermediate work.
- React also persists a path-addressable `react.turn.finalize` block for final
  turn stats. After TTL pruning, that block is not replayed as the large boxed
  announce text; it participates in the compact `[TURN STATUS]` card together
  with `react.state`, `react.exit`, and `react.workspace.publish`.
- A long turn can therefore contain multiple completion attempts and multiple
  working summaries if followups arrive during finalization and create new
  portions of work. The canonical `ws:<turn_id>.conv.working.summary` handle is
  an alias to the latest working summary for that turn; attempt-scoped paths
  remain individually addressable.
- The rendered model view groups tool output into:
  - `[TOOL CALL <id>].call <tool_id>`
  - `[TOOL RESULT <id>].summary <tool_id>` (artifact tools)
  - `[TOOL RESULT <id>].result <tool_id>` (non‑artifact tools)
  - `[TOOL RESULT <id>].artifact <tool_id>` per artifact (logical_path + content)
- `react.read` may skip re-emitting full blocks already visible (dedup) and
  records this in its status block. When this happens, the tool result should
  say that the requested path already exists in visible context and, when
  possible, point at the visible location. Ranged `react.read` requests are
  different: each requested line/symbol range is emitted as its own visible
  evidence block, even if the same logical path is already visible elsewhere.
- Agents can also set:
  - sources pool via `ContextBrowser.set_sources_pool(...)`
  - ephemeral announce blocks via `ContextBrowser.announce(...)`
  - on exit, the current announce block is persisted into the turn log blocks and then cleared

## Shared external events (live + fallback)
`followup` and `steer` do not need to wait for turn end anymore.

- Ingress appends them to a shared durable conversation event source.
- If the active React turn owns the timeline, `ContextBrowser` listens to that source,
  folds events live into the current timeline, persists the external-event cursor, and
  notifies runtime hooks.
- A consumed `followup` keeps the same turn alive and is seen by the next decision boundary.
- A consumed `steer` is treated as stop/reorient control. Engineering interrupts the active decision generation or cancellable tool phase immediately when possible, then React re-enters with the steer block already on the timeline and only a short bounded finalize window.
- If there is no live owner, processor promotion uses that same durable source to continue
  the conversation later. There is not a separate “live log” and “fallback queue” model anymore.

## Compaction
Two places can trigger compaction:
1) **Before sending**: `timeline(...)`
   - If estimated size exceeds limit → `sanitize_context_blocks(...)`
   - Selects a cut point based on a token budget (keeps recent tokens; never cut on tool results)
   - Inserts a `conv.range.summary` block at the cut point
   - Saves summary artifact to index (`conv.range.summary`)
2) **Retry on context-limit error**:
   - The agent retries once with `force_sanitize=True`

### Example: Fetch + Retry
```python
blocks = await ctx_browser.timeline(
    conversation_id=turn_id,
    turn_id=turn_id,
    cache_last=True,
)
try:
    await agent_call(blocks=blocks)
except ServiceException as exc:
    if is_context_limit_error(exc.err):
        blocks = await ctx_browser.timeline(
            conversation_id=turn_id,
            turn_id=turn_id,
            cache_last=True,
            force_sanitize=True,
        )
        await agent_call(blocks=blocks)
    else:
        raise
```

### Important
- Compaction can happen mid-turn; a summary block can be inserted inside the current turn.
- The summary block is **not** stored in the turn log.
- Older blocks remain in the timeline but `timeline.render(...)` hides them by slicing
  from the most recent summary onward.
- External followup/steer blocks participate in the same compaction rules as other timeline blocks.
- In normal chatbot workflows, the pre-send render budget defaults from
  `ai.react.context_max_tokens` / `AI_REACT_CONTEXT_MAX_TOKENS` (default
  `80000`) unless the bundle sets `max_tokens`.
- TTL-pruned historical turns render as `conv.working.summary` cards when React wrote
  one at turn completion. The model sees goal, outcome, key facts, and refs,
  which is enough to decide whether a referenced path should be read.
- If a pruned turn has no working summary, hidden blocks fall back to compact
  retrieval-index rows: logical path plus a small semantic hint, not the full
  historical turn payload. Timestamps are kept for turn starts and assistant responses,
  and omitted from hidden historical tool/user/file refs.
- The TTL-pruned retrieval skeleton contains recoverable user/assistant and
  tool call/result facts. TTL pruning suppresses round scaffolding and transient
  chatter: `react.round.start`, `react.thinking`, `react.notes`,
  `react.notice`, and `stage.suggested_followups`.
- Fallback retrieval rows are grouped under one `[PRUNED TURN DATA]` marker per
  turn and use neutral labels such as `user:`, `assistant:`, `tool_call:`, and
  `tool_result:`.
- TTL pruning owns replacement bounding. It caps automatic replacement text
  before calling `Timeline.hide_paths(...)`. Explicit `react.hide` preserves its
  replacement exactly and is governed by cache-point editability, not by the TTL
  replacement cap.

## Context Access Diagram (Timeline + Contribute)
```
Turn start:
  set_runtime_context(...)
  load_context(...) → history_blocks + current_turn_blocks cached

Agent call:
  blocks = timeline(...)
  agent consumes blocks
  agent produces contribution blocks
  contribute(blocks, persist=True)

React loop:
  for each round:
    timeline(...)
    decision/tool call
    contribute(react.tool.call/result, persist=True)

Retry on context-limit:
  timeline(force_sanitize=True)
```

## Context Layers (ASCII)

```
┌────────────────────────────┐
│ RANGE SUMMARIES             │   (conv.range.summary, from index)        [stable]
└────────────────────────────┘
┌────────────────────────────┐
│ HISTORY BLOCKS              │   (prior turns: user → contrib → assistant)[stable]
└────────────────────────────┘
┌────────────────────────────┐
│ CURRENT TURN USER BLOCKS    │   (prompt + attachments)                  [stable]
└────────────────────────────┘
┌────────────────────────────┐
│ TURN PROGRESS LOG          │   (agent contributions, prompt-like followups/steers,             [growing]
│                            │    and one or more assistant.completion blocks)                   [growing]
└────────────────────────────┘
┌────────────────────────────┐
│ SOURCES POOL (optional)     │   (tail, uncached)                        [ephemeral]
└────────────────────────────┘
┌────────────────────────────┐
│ ANNOUNCE (optional)         │   (tail, uncached)                        [ephemeral]
│                            │   (+ current-turn live event summary)     [ephemeral]
└────────────────────────────┘
```

### Progression notes
- Contributions append to **TURN PROGRESS LOG** as the turn advances.
- `announce` is transient during the turn; on exit it is persisted into the turn log blocks and cleared.
- Compaction inserts **RANGE SUMMARIES** at a cut point; render starts at the latest summary.
