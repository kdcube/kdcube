# Context Progression & Compaction

This describes how context is built and updated during a turn.

## At Turn Start
1) `ContextBrowser.load_context(...)` fetches:
   - recent turn logs
   - recent `conv.range.summary` artifacts from the index
2) Browser builds:
   - `history_blocks` (older turns + summaries)
   - `current_turn_blocks` (user prompt + attachments)
3) Browser caches these blocks per (conversation_id, turn_id).

## During the Turn
- Agents request blocks via:
  `timeline(...)`
- Downstream agents append **in-turn progress blocks** via `ContextBrowser.contribute(...)`:
  - These blocks represent work done so far in response to the current user request.
  - Examples: gate/coordinator decisions, react tool calls/results.
  - When `persist=True`, they are stored in the turn log blocks for next-turn reconstruction.
- React rounds append `react.tool.call` / `react.tool.result` blocks as contributions.
- Agents can also set:
  - sources pool via `ContextBrowser.set_sources_pool(...)`
  - ephemeral announce blocks via `ContextBrowser.announce(...)`
  - on exit, the current announce block is persisted into the turn log blocks and then cleared

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
