---
id: ks:docs/sdk/agents/react/compaction-README.md
title: "Compaction"
summary: "Context hard‑ceiling behavior: compaction blocks and visibility rules."
tags: ["sdk", "agents", "react", "compaction", "context"]
keywords: ["conv.range.summary", "hard ceiling", "visible stream", "context length", "pruning"]
see_also:
  - ks:docs/sdk/agents/react/feedback-README.md
  - ks:docs/sdk/agents/react/context-caching-README.md
  - ks:docs/sdk/agents/react/context-layout.md
  - ks:docs/sdk/agents/react/session-view-README.md
  - ks:docs/sdk/agents/react/memory-recovery-path-README.md
---
# Context Compaction (v2)

Compaction is the **hard ceiling** protection for context length. It inserts a
`conv.range.summary` block and drops older blocks from the **visible** stream.
It is separate from TTL pruning (which hides blocks with replacement text).

---

## When Compaction Runs
Compaction runs in two situations:

1) **Normal render path (pre‑send)**
   - `Timeline.render(...)` estimates total tokens.
   - If `system + blocks > 0.9 * max_tokens`, it compacts immediately.
   - Chatbot workflows use `ai.react.context_max_tokens` /
     `AI_REACT_CONTEXT_MAX_TOKENS` as the default render budget when the bundle
     does not set `max_tokens` explicitly. The default is `80000`.

2) **Retry after context‑limit error**
   - If the decision call fails with a context‑limit error, `retry_with_compaction`
     forces compaction and retries.

In both cases, compaction is executed inside `Timeline.render()` via
`sanitize_context_blocks(...)`.

---

## Render Pipeline Order (Important)
During `render()`:

1. **TTL pruning** (`apply_session_cache_ttl_pruning`) runs first.
2. **Compaction** (`sanitize_context_blocks`) runs next if needed or forced.
3. The visible stream is sliced **after the latest summary**.
4. Hidden historical turns render as `conv.working.summary` card(s) when
   summaries exist. Multiple working summaries are preserved so distinct
   same-turn work portions remain recoverable. Without a working summary, hidden
   blocks render as compact retrieval-index rows with the logical path plus a
   small semantic hint.
5. Cache points are recomputed for the visible stream.

This ordering ensures compaction works on the already‑pruned timeline and that
cache points remain valid after compaction.

TTL pruning stores a bounded `replacement_text` on hidden blocks, but compaction
and render should treat that as retrieval metadata.
Explicit `react.hide` preserves the caller-provided replacement exactly.

Finalization internals render as one compact `[TURN STATUS]` card with round
count, exit reason/error, elapsed time, plan status, and selected workspace
publish fields. The card is built from `react.turn.finalize`, `react.state`,
`react.exit`, and `react.workspace.publish`.

The retrievable skeleton contains tool calls/results and user/assistant blocks.
TTL pruning suppresses round scaffolding and transient chatter:
`react.round.start`, `react.thinking`, `react.notes`, `react.notice`, and
`stage.suggested_followups`.
Fallback retrieval rows are introduced by one `[PRUNED TURN DATA]` marker per
turn and use neutral labels such as `user:`, `assistant:`, `tool_call:`, and
`tool_result:`.

---

## Exact Cut‑Point Rules (Authoritative)
Cut points are chosen by `_find_compaction_cut_point` using these exact rules:

### 1) Candidate cut points
A block is a **cut‑point candidate** if `_is_cut_point_block(block)` returns true:

- **Reject**: `react.tool.result`, `conv.range.summary`
- **Accept**: `user.prompt`, `assistant.completion`, `react.tool.call`, `turn.header`
- **Accept**: any block with `author` in `{user, assistant}`
- **Accept**: any block with `author` present and **not** in `{system, tool}`

This prevents cutting inside tool results and prefers user/assistant or tool call boundaries.

### 2) Message blocks for token accounting
Token accounting uses `_is_message_block`:

- `react.tool.result` counts as a message block
- Otherwise, same as `_is_cut_point_block`

This ensures tool results contribute to the “recent tokens” budget, but they
are **not valid cut‑points**.

### 3) Cut index selection
- Walk backward accumulating tokens from message blocks until
  `accumulated >= keep_recent_tokens`.
- Choose the **first candidate cut point at or after** that index.
- Then **backtrack** until the cut is on a message boundary or just after a
  summary block.

### 4) Turn boundary and split turns
A **turn start block** is:
- `turn.header` or `user.prompt`, or
- any block with `author == user`

If the cut does **not** land on a turn start in the **current turn**, it is a
split‑turn cut and the prefix of that turn is summarized separately under:

`"Turn Context (split turn)"`

The cut block itself remains in the retained window.

If the tentative cut lands inside a historical, non-current turn, the cut is advanced
to the next turn boundary and the historical turn is compacted as a whole. The
turn-prefix summarizer is only for a too-large current turn prefix; it is not a
gate that decides whether historical compaction may proceed.

---

## What Compaction Produces
Compaction inserts a **summary block**:

- `type = conv.range.summary`
- `path = su:<turn_id>.conv.range.summary`
- `meta.compaction_digest` describes compacted artifacts
- `meta.covered_turn_ids` lists turns compacted into the summary
- `meta.split_turn_id` exists if compaction cut a turn in half

The model-facing renderer wraps this block as a prior-conversation checkpoint:

```text
[COMPACTED PRIOR CONVERSATION MEMORY]
[path: su:<turn_id>.conv.range.summary]
covered_turns: first_turn, second_turn, ... penultimate_turn, last_turn (count=N)
compacted_time_range: 2026-02-01T10:00:00Z -> 2026-02-03T12:30:00Z
conversation_first_message_ts: 2026-02-01T10:00:00Z
split_turn_id: turn_b
origin: model-generated compaction of older timeline blocks removed from the visible stream
use: treat this as prior conversation state; newer visible turns below may supersede it
recovery: use logical paths from the summary or react.memsearch/react.read when exact old content is needed
<model-generated summary text>
[END COMPACTED PRIOR CONVERSATION MEMORY]
```

The debug files under `debug/rendering/rendered-...txt` are written from the
same model message blocks sent to the decision agent, so they are the right
place to inspect whether the model-facing shape is clear.

`covered_turns` is capped in the rendered checkpoint. Small lists may render in
full; large lists render as:

```text
covered_turns: first_turn, second_turn, ... penultimate_turn, last_turn (count=N)
```

This keeps provenance visible without spending tokens on hundreds of turn ids.
The compacted checkpoint also carries temporal orientation:

- `compacted_time_range` is the first and last timestamp found in the summarized range
- `conversation_first_message_ts` is the timestamp of the first user message in
  the conversation, propagated across repeated compactions

Compaction may also insert plan-carry blocks immediately after the summary:

- a carried latest active `react.plan` snapshot if the active plan would otherwise fall behind the summary boundary
- a visible `react.plan.history` index block for older compacted plans
- stable `ar:` latest-snapshot aliases for plans:
  - `ar:plan.latest:<plan_id>`

**Summary content** is generated by:
- `summarize_context_blocks_progressive(..., max_tokens=800)`
- plus `summarize_turn_prefix_progressive(..., max_tokens=400)` only if the cut splits the current turn

Compaction summaries are model-generated. If the model returns an empty summary,
the caller must treat that as a failed/empty compaction result and avoid replacing
history with a mechanical fallback summary.

---

## What Exactly Gets Summarized
Compaction summarizes **everything between the last summary (if any) and the cut point**.
The retained window starts **at the cut point**.

If the cut falls **inside a turn**, only the **prefix** of that turn is summarized
(and included under “Turn Context (split turn)”). The cut block itself stays visible.

If a turn contains multiple `assistant.completion` blocks, compaction treats each visible
completion as an ordinary message block. The latest-path alias still points to the most recent
completion for that turn; earlier visible completions keep their numbered paths.

This includes external in-turn user contributions such as:
- `user.followup`
- `user.steer`

They are treated as first-class user control input. If they fall behind the latest
summary, visible copies are preserved after the summary boundary as:
- `user.followup.preserved`
- `user.steer.preserved`

That keeps same-turn followup/stop intent visible even after compaction.

---

## Model-Facing Shape After Compaction

Schematic visible stream after historical compaction:

```text
[COMPACTED PRIOR CONVERSATION MEMORY]
path: su:turn_13083713.conv.range.summary
covered_turns: telegram_turn_13083619, ..., turn_13083708
compacted_time_range: 2026-05-03T01:15:31Z -> 2026-05-05T23:42:47Z
conversation_first_message_ts: 2026-05-03T01:15:31Z
origin: model-generated compaction of older timeline blocks removed from the visible stream
use: prior conversation state; newer visible turns below may supersede it
recovery: use logical paths from the summary or react.memsearch/react.read for exact content

Goal: ...
Outcome: ...
Key facts:
- ...
Key artifacts:
- fi:...
[END COMPACTED PRIOR CONVERSATION MEMORY]

[ACTIVE/CARRIED PLAN]             # only if an active plan would otherwise be lost
[PLAN HISTORY]                    # only if older plans were compacted
[FOLLOWUP DURING TURN preserved]  # only if user followup/steer fell behind the cut

TURN <cut_or_next_turn> (started at ...)
  ... retained suffix renders normally ...

TURN <current_turn> (started at ...)
  ... current turn renders normally ...

SOURCES POOL / ANNOUNCE           # appended tail blocks when requested
```

Schematic visible stream when the current turn itself is too large:

```text
[COMPACTED PRIOR CONVERSATION MEMORY]
path: su:<current_turn>.conv.range.summary
covered_turns: previous turns and the current-turn prefix
split_turn_id: <current_turn>

<summary of prior history>

---

Turn Context (split turn):
<summary of current-turn prefix>
[END COMPACTED PRIOR CONVERSATION MEMORY]

TURN <current_turn> (started at ...)
  ... retained current-turn suffix from the cut point onward ...
```

What remains visible after compaction:

- the latest `conv.range.summary` memory checkpoint
- plan carry/history blocks needed to preserve active plan state
- preserved user followup/steer blocks whose originals fell behind the cut
- the retained suffix from the selected cut point onward
- user/assistant messages, tool calls/results, files, source-pool rows, and
  attachment blocks that belong to the retained suffix
- appended sources/announce blocks when render requested them

What does not remain visible:

- blocks before the latest summary, except through the summary/checkpoint text
- older raw turns that were summarized into the checkpoint
- older hidden TTL-pruning skeleton rows that fell before the compaction cut

The old artifacts are not deleted by compaction. Their logical paths remain
recoverable through `react.read`, `react.memsearch`, and refs carried in the
summary/digest.

---

## Persistence Rule
After compaction, the timeline is **persisted from the last summary onward**:

- In‑memory for the current turn: pre‑summary blocks still exist but are no
  longer visible.
- On persistence / next turn load: only blocks **from the latest summary onward**
  are kept.
- Historical plan refs that were carried forward remain readable after persistence because their preserved copies live after the summary boundary.
- The external-event replay cursor is still persisted with the timeline payload:
  `last_external_event_id` and `last_external_event_seq`.

This keeps the prefix small while allowing the summary to represent old turns.

---

## Cache Points After Compaction
Cache points are **recomputed** after compaction and hidden‑replacement.
The rules are the same as normal rendering:

1. **Previous‑turn cache point** (last block before current turn, if available)
2. **Pre‑tail cache point** (last block of round `N‑4`, based on
   `cache_point_offset_rounds=4`)
3. **Tail cache point** (last block in visible stream)

This ensures caching stays valid even after summary insertion.

---

## Reference Diagram (with cache points)
The cache points are placed on **specific blocks**, not “between” turns:

1) **Prev‑turn cache point** → the *last block* immediately before the current turn header.  
2) **Pre‑tail cache point** → the *last block* of round `N‑4` from the tail (if enough rounds).  
3) **Tail cache point** → the *last block* in the visible stream.  

### Example (enough rounds for pre‑tail)
Current turn is **TURN E**. Round ends are denoted with `·end`.

```
Before:
  [TURN A·end] [TURN B·end] [TURN C·end] [TURN D·end] [TURN E·end]
   ^ pre‑tail cache (end of TURN A, N‑4)
                                ^ prev‑turn cache (end of TURN D)
                                          ^ tail cache (end of TURN E)
```

After compaction (summary covers A..C; visible D..E):

```
After compaction (in memory):
  [SUMMARY(A..C)] [TURN D·end] [TURN E·end]
                     ^ prev‑turn cache (end of TURN D)
                               ^ tail cache (end of TURN E)
  (pre‑tail cache is omitted if visible rounds < min_rounds)
```

If the cut lands inside TURN C:

```
Before:
  [TURN A·end] [TURN B·end] [TURN C(prefix)] [TURN C·end] [TURN D·end] [TURN E·end]

After compaction (in memory):
  [SUMMARY(A..B + C‑prefix)] [TURN C·end] [TURN D·end] [TURN E·end]
```

---

## Compaction Digest
`meta.compaction_digest` includes structured summaries of compacted blocks:
- produced artifacts (path, mime, visibility, sources_used)
- tool outputs (by tool type)
- memsearch hits and paths
- hidden blocks and replacement text

It is used to recover context if later needed via `react.read`.
For plan history specifically, the primary recovery handles are the `react.plan.history` entries plus the stable `ar:plan.latest:<plan_id>` refs rather than the digest itself.

Working summaries are also injected into the compaction prompt when they belong
to turns being compacted. They are serialized as `[Working Summary]`, so the
compactizer sees them as durable task state.

---

## Hooks
If provided in `RuntimeCtx`:

- `on_before_compaction({before_tokens})`
- `on_after_compaction({before_tokens, after_tokens, compacted_tokens})`

---

## Test Coverage
Tests live in `test_timeline_compaction.py` and cover:
- summary insertion
- split‑turn handling
- no compaction under limit
- compaction after existing summary
- digest includes hidden blocks
- tool boundary preservation
- cache points after render
- TTL-pruned cache notices, working-summary turn cards, and hidden-block
  retrieval-index fallback rows
- explicit `react.hide` preserving replacement text while TTL pruning bounds
  automatic replacement text
