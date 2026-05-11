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
   - `Timeline.render(...)` estimates total model input tokens.
   - If `system/instructions + rendered timeline > max_tokens`, it compacts immediately.
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

If the tentative cut lands inside the **current turn**, the current turn is
protected. Blocks from the active turn have not yet been finalized into an
immutable turn log, so compaction must not move the summary boundary past them.
Large current-turn blocks may be hidden in the model-facing projection, but the
original `tc:`, `ar:`, and `fi:` blocks stay in the active timeline under their
original logical paths.

If the tentative cut lands inside a historical, non-current turn, the cut is advanced
to the next turn boundary and the historical turn is compacted as a whole. The
turn-prefix summarizer is not allowed to replace active current-turn data.

---

## What Compaction Produces
Compaction inserts a **summary block**:

- `type = conv.range.summary`
- `path = su:<turn_id>.conv.range.summary`
- `meta.compaction_digest` describes compacted artifacts
- `meta.covered_turn_ids` lists turns compacted into the summary
- `meta.split_turn_id` may exist on older/legacy summaries; current-turn data
  must not be summarized behind a split-turn summary boundary

The model-facing renderer wraps this block as a prior-conversation checkpoint:

```text
[COMPACTED PRIOR CONVERSATION MEMORY]
[path: su:<turn_id>.conv.range.summary]
covered_turns: first_turn, second_turn, ... penultimate_turn, last_turn (count=N)
compacted_time_range: 2026-02-01T10:00:00Z -> 2026-02-03T12:30:00Z
conversation_first_message_ts: 2026-02-01T10:00:00Z
origin: model-generated compaction of older timeline blocks removed from the visible stream
use: treat this as prior conversation state; newer visible turns below may supersede it
recovery: use logical paths from the summary or react.memsearch/react.read when exact old content is needed
## Active Work Reminder
active_request:
- <recognizable active request>
retrieval_anchors:
- phrase: "<exact error, log phrase, user wording, or unique title>"
- entity: "<tool id, function/class name, bundle id, task id, turn id, or subsystem>"
- time: "<timestamp or time range if known>"
read_refs:
- <KDCube logical path only: ar:/tc:/fi:/ws:/su:/so:, or "(none yet)">
done:
- <completed work relevant to this request>
open:
- <unresolved work or verification gaps>
next:
- <immediate next action>
recovery_plan:
- first: "Use this visible reminder and retained suffix before searching."
- if_needed: "Use react.memsearch with the exact phrase/entity anchors above."
- then_read: "Use react.read(read_refs) for old content. Skills read in full. ks: reads in full only when no knowledge_read_visible_* cap is configured; capped text uses stats_only and line ranges. Exec stdout is not an uncapped read channel."

<rest of model-generated summary text>
[END COMPACTED PRIOR CONVERSATION MEMORY]
```

`Active Work Reminder` is intentionally duplicated inside every compacted memory
summary. It is the fast re-orientation and retrieval block for the next model
after one or many compactions. It should make a follow-up like "do this now" or
"address the other issue from that log" resolvable before the model decides
whether to use `react.memsearch`. If search is needed, the reminder should
already contain exact phrases, entities, timestamps, and model-facing logical
refs that make search and read recovery cheap and precise. `read_refs` must not
contain physical host paths; only KDCube logical paths are readable by
`react.read`. `active_request` is the narrow resumable task; `Goals` in the
rest of the summary is the broader set of user/project objectives and may
include completed or parked work.

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

When compaction is forced by a too-large **current turn**, it does **not** insert
a prior-conversation summary for the current turn prefix. Instead:

- prior history, if any, is summarized before the current turn
- if there is no prior history, no `conv.range.summary` is created
- current-turn blocks stay in the timeline and in `timeline.json`
- the compacted current-turn prefix is hidden only in the rendered projection
- the compactor inserts one visible `[MID-TURN COMPACTION n]` checkpoint at the
  original cut point, between the compacted prefix and the retained suffix
- if multiple mid-turn compactions happen in one turn, the renderer shows only
  the latest checkpoint
- the checkpoint `engineering_ledger` is rebuilt from the current-turn timeline
  prefix each time; it is not copied or parsed from older checkpoint text
- blocks hidden by an earlier mid-turn compaction are still scanned when the
  latest checkpoint is rebuilt, because they remain timeline blocks with their
  original logical paths and original token estimates

Model-facing shape:

```text
TURN <turn_id> (started at ...)

[USER MESSAGE]
[path: ar:<turn_id>.user.prompt]
...

[MID-TURN COMPACTION 1]
turn_id: <turn_id>
position: current-turn prefix compacted here; newer timeline blocks below are normal
use: continue from the timeline below; this is not prior conversation memory
recovery: exact source blocks remain recoverable by logical path; use react.read(path). Skills read in full. ks: reads in full only when uncapped; capped text uses stats_only and line ranges.

semantic_progress:
active_request:
- <immediate current-turn request>
retrieval_anchors:
- phrase: "<exact user wording, error text, result title, or unique phrase>"
- entity: "<tool id, call id, artifact name, turn id, or subsystem>"
- time: "<timestamp or range if known>"
read_refs:
- <tc:/ar:/fi:/ws:/su:/so: logical refs, or "(none yet)">
done:
- <prefix work already completed>
open:
- <what the retained suffix still needs to resolve>
next:
- <immediate next action>
recovery_plan:
- first: "Continue from the retained suffix and this reminder."
- then_read: "Use react.read(path) for large results. Skills read in full. ks: reads in full only when uncapped; capped text recovers by stats_only plus line ranges."
original_request:
- <full user ask that started the turn>
early_progress:
- <key choices and work done before this checkpoint>
context_for_suffix:
- <facts needed to understand the normal timeline blocks below>
compacted_large_results:
- <logical path, result shape/schema, tiny sample, and recovery method>

engineering_ledger:
- tool_call_id: <call_id>
  tool: email.process_user_emails
  call: tc:<turn_id>.<call_id>.call
  params: "..."
  result: tc:<turn_id>.<call_id>.result
  result_tokens_estimate: 89167
  result_shape: "ok=bool, messages=list[50]"
  result_hint: "ok=true ..."
  files:
  - fi:<turn_id>.outputs/report.pdf mime=application/pdf
  sources:
  - so:sources_pool[1-3]
[/MID-TURN COMPACTION 1]

┌──────── ROUND 2 ────────┐
  [TOOL CALL <read_call>].call react.read
  ...
  [TOOL RESULT <read_call>].result react.read
  read_paths:
  - tc:<turn_id>.<call_id>.result (tokens=89167)
└────────────────────────┘
```

**Summary content** is generated by:
- `summarize_context_blocks_progressive(..., max_tokens=800)`
- `summarize_turn_prefix_progressive(..., max_tokens=900)` for the
  `semantic_progress` section of mid-turn checkpoints

Compaction summaries are model-generated. If the model returns an empty summary,
the caller must treat that as a failed/empty compaction result and avoid replacing
history with a mechanical fallback summary.

Compaction is triggered by model-visible token pressure. If the candidate
compacted projection does not reduce model-visible tokens, the runtime skips
applying it and emits `chat.compaction` with `status=skipped` and
`reason=no_visible_token_reduction`. This prevents a compaction pass from
replacing clear history with a longer summary/checkpoint.

---

## What Exactly Gets Summarized
Compaction summarizes **everything between the last summary (if any) and the cut point**.
For historical compaction, the retained window starts **at the cut point**.
For a current-turn split, the cut point is moved back to the beginning of the
current turn before summary insertion.

If the cut falls inside the **current turn**, current-turn blocks are not
summarized away. The compaction boundary is moved to before the current turn,
and large current-turn data blocks are compacted only in the rendered view.

If the cut falls inside an older turn, the cut advances to the next turn
boundary so the older turn is summarized as a whole.

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

## Active Work Reminder
active_request:
- fix the processor idle watchdog regression from the May 8 log
retrieval_anchors:
- phrase: "Task idle timeout exceeded after 600.768s"
- entity: "processor watchdog; ChatCommunicator.emit; ClaudeCodeAgent stdout reader"
- time: "2026-05-08T14:01:55Z to 2026-05-08T16:12:15Z"
read_refs:
- su:<turn_id>.conv.range.summary
- ar:<turn_id>.assistant.completion
done:
- mid-turn compaction representation was fixed
open:
- verify chat emissions refresh active task activity during long ReAct turns
next:
- inspect processor activity tracking and communicator event paths
recovery_plan:
- first: "Use this visible reminder and the retained suffix."
- if_needed: "react.memsearch query='Task idle timeout exceeded after 600.768s processor watchdog' targets=['summary','user','assistant','tool'] mode='timeline'"
- then_read: "react.read(read_refs plus any ar:/tc:/su: paths named by memsearch results)"

## Goals
...
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
[COMPACTED PRIOR CONVERSATION MEMORY]   # only when prior history existed
path: su:<current_turn>.conv.range.summary
covered_turns: previous historical turns
<summary of prior history only>
[END COMPACTED PRIOR CONVERSATION MEMORY]

TURN <current_turn> (started at ...)
  [USER MESSAGE]

[MID-TURN COMPACTION 1]             # inserted where the compaction cut happened
semantic_progress:
active_request:
- <current-turn request>
done/open/next:
- <compact turn-prefix handoff>
compacted_large_results:
- <large result paths, shape, and recovery method>
engineering_ledger:
- tool_call: path=tc:<current_turn>.<call>.call ...
- tool_result: path=tc:<current_turn>.<call>.result ...
[/MID-TURN COMPACTION 1]

  [TOOL CALL react.read or exec_tools.execute_code_python ...]
  ... current turn continues ...
```

What remains visible after compaction:

- the latest `conv.range.summary` memory checkpoint
- plan carry/history blocks needed to preserve active plan state
- preserved user followup/steer blocks whose originals fell behind the cut
- the retained suffix from the selected cut point onward
- for current-turn splits, the entire active current turn with large data blocks
  retained in `timeline.json`; the rendered view shows only the latest
  mid-turn compaction checkpoint plus the normal retained suffix
- the latest mid-turn checkpoint's `engineering_ledger`, recomputed from the
  current-turn timeline prefix rather than inherited from older checkpoint text
- user/assistant messages, tool calls/results, files, source-pool rows, and
  attachment blocks that belong to the retained suffix
- appended sources/announce blocks when render requested them

What does not remain visible:

- blocks before the latest summary, except through the summary/checkpoint text
- older raw turns that were summarized into the checkpoint
- older hidden TTL-pruning skeleton rows that fell before the compaction cut

Historical artifacts are not deleted from durable storage by compaction. Current
turn artifacts are stronger: they are not moved behind the summary boundary at
all, because before turn finalization the active timeline is their source of
truth. Their logical paths remain directly reachable by `react.read`; large
text should be recovered into model-visible context through bounded
`react.read` ranges where the namespace supports ranges.

---

## Persistence Rule
After compaction, the timeline is **persisted from the last summary onward**:

- Current-turn split invariant: the latest summary, if inserted, is placed
  before the current turn. Current-turn blocks remain after the summary and are
  persisted with their original logical paths.
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

## Accounting
Compaction LLM calls are charged under the caller component, usually the active
bundle id, with the specific compaction role stored as the accounting agent:

- `context.compaction.summary`
- `context.compaction.turn_prefix`

This keeps bundle-level spend grouped with the turn that triggered compaction
while still preserving a breakdown by compaction role.

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
