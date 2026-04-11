---
id: ks:docs/sdk/agents/react/context-progression.md
title: "Context Progression"
summary: "How context is built and updated during a turn."
tags: ["sdk", "agents", "react", "context", "progression"]
keywords: ["turn progression", "fetch_ctx", "tool calls", "context updates"]
see_also:
  - ks:docs/sdk/agents/react/context-layout.md
  - ks:docs/sdk/agents/react/react-context-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
---
# Context Progression & Compaction

This describes how context is built and updated during a turn.

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
- The rendered model view groups tool output into:
  - `[TOOL CALL <id>].call <tool_id>`
  - `[TOOL RESULT <id>].summary <tool_id>` (artifact tools)
  - `[TOOL RESULT <id>].result <tool_id>` (non‑artifact tools)
  - `[TOOL RESULT <id>].artifact <tool_id>` per artifact (logical_path + content)
- `react.read` may skip re‑emitting blocks already visible (dedup) and records this in its status block.
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
- A consumed `steer` is treated as stop/reorient control. React exits the current turn at the next safe checkpoint and persists the work completed so far.
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
│ TURN PROGRESS LOG          │   (agent contributions, react logs and finally final_answer)       [growing]
│                            │   (+ live-folded external followup/steer blocks)                  [growing]
└────────────────────────────┘
┌────────────────────────────┐
│ SOURCES POOL (optional)     │   (tail, uncached)                        [ephemeral]
└────────────────────────────┘
┌────────────────────────────┐
│ ANNOUNCE (optional)         │   (tail, uncached)                        [ephemeral]
└────────────────────────────┘
```

### Progression notes
- Contributions append to **TURN PROGRESS LOG** as the turn advances.
- `announce` is transient during the turn; on exit it is persisted into the turn log blocks and cleared.
- Compaction inserts **RANGE SUMMARIES** at a cut point; render starts at the latest summary.
