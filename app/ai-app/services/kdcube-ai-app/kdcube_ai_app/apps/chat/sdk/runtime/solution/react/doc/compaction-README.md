# Context Compaction (v2)

Compaction is triggered by `Timeline.render(...)` when the estimated size of
the block stream would exceed limits. It inserts `conv.range.summary` at a cut point
and hides older blocks from render. The timeline is **persisted from the last summary onward**,
so blocks before the summary are evicted at persistence time.

## When it happens
1) **Before sending** (normal path)
   - `timeline(...)` estimates size.
   - If too large, it compacts immediately and inserts `conv.range.summary` before history.
2) **Retry on context-limit error**
   - Agent call fails with a context-length error.
   - Caller retries with `force_sanitize=True`, which forces compaction.

## What it does
- Selects a cut point based on a token budget (keeps recent tokens, never cuts on tool results).
- Creates a `conv.range.summary` block that inventories the compacted window.
- Stores that summary in the index (not in the turn log).
- Inserts the summary block in-place at the cut point.
- `timeline.render(...)` slices the visible stream from the latest summary onward.
  Older blocks are retained **in memory** for the current turn, but are **evicted** on persist.

## Cut-point heuristic (details)
1) **Find recent window**
   - Estimate tokens for visible blocks.
   - Keep roughly 70% of the budget for the most recent content.
   - Optionally protect the last *N* turns (default `keep_recent_turns=6`).

2) **Pick a safe cut point**
   - Candidate cut points are blocks that are *not* tool results (never cut inside a tool result).
   - Prefer a cut point that aligns with a turn boundary or a message boundary.
     - *Turn boundary*: `turn.header` or `user.prompt`.
     - *Message boundary*: `user.prompt`, `assistant.completion`, `react.tool.call`,
       or any block authored by `user`/`assistant`.

3) **Split-turn handling**
   - If the cut falls inside a turn, summarize the prefix of that turn separately and append it to the main summary
     under **"Turn Context (split turn)"**.
   - The **cut point block itself remains** in the retained (post‑summary) window.
   - Only the prefix blocks before the cut are summarized.

## Pseudo‑code (exact heuristics)
```
blocks = timeline.blocks
sys_est = len(system_text)/4
block_est = estimate_tokens(blocks)
if sys_est + block_est <= max_tokens*0.9: return blocks

boundary_start = last_summary_index + 1
context_budget = max_tokens - sys_est
keep_recent_tokens = 0.7 * context_budget

if keep_recent_turns:
  recent_start = find_recent_turn_start(keep_recent_turns)
  recent_tokens = estimate_tokens(blocks[recent_start:])
  keep_recent_tokens = max(keep_recent_tokens, recent_tokens)

cut_index = find_cut_point(keep_recent_tokens)
if cut falls inside a turn:
  prefix_blocks = turn_prefix
  prefix_summary = summarize_turn_prefix(prefix_blocks)

history_blocks = blocks[boundary_start:cut_index] (excluding summaries)
summary = summarize(history_blocks, previous_summary)
summary += prefix_summary (if any)

insert summary block at cut_index
return updated blocks
```

### Split turns
If compaction cuts inside a turn, the prefix of that turn is summarized separately and
merged into the main summary as a “Turn Context (split turn)” section.

### Compaction digest
The summary block includes `meta.compaction_digest` with details about:
- streamed artifacts (path, mime, visibility, sources_used)
- file writes / patches / exec outputs (via tool classification)
- memory_read hits (query + paths + turn_ids)
- hidden blocks and their replacement text

## Hooks
If provided in `ContextBrowser.set_runtime_context(...)`:
- `on_before_compaction(stats)` — emits a “compacting” status
- `on_after_compaction(stats)` — emits a “back to work” status

`stats` includes:
- `tokens_before`
- `tokens_after`
- `compacted_blocks`
- `summary_block_count`

## Important
- Compaction can happen **mid-turn**, inserting a summary inside the current turn.
- Summary blocks are **not stored** in the turn log; they are index-only.
- On persist, the timeline is truncated to **only the post‑summary window**.
  Next turn starts with that compacted prefix (summary + following blocks).

## Timeline before/after compaction (schematic)
```
Before:
  [TURN A ... blocks ...] [TURN B ... blocks ...] [TURN C ... blocks ...]
                 ^ cache checkpoint 1      ^ cache checkpoint 2 (tail)

After compaction (in-memory):
  [TURN A ... blocks ...] [SUMMARY] [TURN B tail ...] [TURN C ...]
                 ^ cache checkpoint 1      ^ cache checkpoint 2 (tail)

After persistence (next turn load):
  [SUMMARY] [TURN B tail ...] [TURN C ...]
           ^ cache checkpoint 1      ^ cache checkpoint 2 (tail)
```

See also:
- `context-layout.md`
- `context-progression.md`

## Test cases (coverage)
Each case has a matching test in `test_timeline_compaction.py`.

1) **Insert summary and keep cut‑point**  
   Ensures a `conv.range.summary` is inserted and the cut‑point block remains visible.

2) **Split‑turn prefix summary**  
   If a cut falls inside a turn, the prefix is summarized under “Turn Context (split turn)”.

3) **No compaction under limit**  
   When estimated tokens are below the threshold (and `force=False`), blocks are unchanged.

4) **Compaction after existing summary**  
   If a prior summary exists, new summary insertion occurs after the last summary.

5) **Hidden blocks surfaced in digest**  
   Hidden blocks (meta.hidden) contribute to `compaction_digest.hidden_blocks`.

6) **Tool-call boundary preserved**  
   Compaction avoids cutting inside tool call/result sequences; the first retained block is never a `react.tool.result`.

7) **Cache points retained after render**  
   After compaction + render, cache checkpoints still exist in the output stream.
