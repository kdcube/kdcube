---
id: ks:docs/sdk/agents/react/memory-recovery-path-README.md
title: "Memory Recovery Path"
summary: "How a React agent recovers exact prior-turn data after TTL pruning and hard compaction."
tags: ["sdk", "agents", "react", "memory", "recovery", "compaction", "pruning"]
keywords: ["react.memsearch", "react.read", "working summary", "turn index", "logical paths", "artifact recovery"]
see_also:
  - ks:docs/sdk/agents/react/session-view-README.md
  - ks:docs/sdk/agents/react/compaction-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
---
# Memory Recovery Path

This document describes how the model recovers exact prior-turn data after the
visible timeline has been TTL-pruned or hard-compacted.

The intended model is:

- summaries are the semantic map
- logical paths are the recovery handles
- turn logs and artifact storage remain the source of exact data
- a deterministic turn index should cover the cases where a summary did not
  mention a useful artifact or tool result

## Namespaces

The recovery path is built on stable logical paths:

| Namespace | Meaning |
|---|---|
| `ws:` | React working summary for a turn |
| `su:` | model-generated compacted conversation range summary |
| `ar:` | authored conversation artifacts such as user prompts, assistant completions, plans |
| `tc:` | tool call / tool result blocks |
| `fi:` | user attachments, produced files, and opt-in snapshot artifacts |
| `so:` | sources pool rows and slices |

The model should not need physical paths for normal recovery. It uses
`react.read` on logical paths. It uses `react.pull` only when execution code
needs a local file.

## Search Capabilities

`react.memsearch` has two retrieval families:

| Family | Use when | Required input | Backing store |
|---|---|---|---|
| Semantic | the model remembers a topic or phrase | `query`, optional `scope` | conversation index over user/assistant/artifact rows; `scope=user` searches this user across conversations |
| Turn catalog | the model needs turns by order or time | `mode`, `ordinal` and/or `from`/`to`, optional `scope` | Postgres turn-log rows with timestamp ordering |

The turn catalog is backed by persisted `kind:turn.log` rows. It uses
Postgres timestamps and window-function ordinals, not timestamp-shaped
`turn_id` strings. This keeps old turn IDs valid and makes questions like
"the second turn" deterministic.

Supported `react.memsearch` modes:

| Mode | Query needed? | Typical question | Parameters |
|---|---:|---|---|
| `semantic` | yes | "Find the Anthropic invoice ZIP attempt" | `query`, optional `from`/`to` |
| `ordinal` | no | "What was the second turn about?" | `ordinal`, optional `scope` |
| `temporal` | no | "What did we discuss in March?" | `from`, `to`, optional `scope` |
| `timeline` | no | "What have we talked about so far?" | `targets=["summary"]`, broad `top_k`, `order` |

Supported targets:

| Target | Returned snippets |
|---|---|
| `summary` | working-summary snippets and `ws:` paths |
| `user` | user prompts, followups, and steers |
| `assistant` | assistant completions |
| `attachment` | original and event-scoped user attachments |

Every useful hit should include `turn_id` and `turn_index_path`. If snippets
identify the turn but not the exact artifact/tool path, read:

```text
react.read(["ar:<turn_id>.react.turn.index"])
```

## What Remains Visible After Compaction

After hard compaction, the model may no longer see the older `ar:`, `tc:`,
`fi:`, or `so:` rows from the compacted turns. It sees a compacted checkpoint:

```text
[COMPACTED PRIOR CONVERSATION MEMORY]
[path: su:<cut_turn>.conv.range.summary]
covered_turns: first_turn, second_turn, ... penultimate_turn, last_turn (count=N)
compacted_time_range: ...
conversation_first_message_ts: ...
origin: model-generated compaction of older timeline blocks removed from the visible stream
recovery: use logical paths from the summary or react.memsearch/react.read when exact old content is needed
## Active Work Reminder
active_request:
- ...
retrieval_anchors:
- phrase: "exact error, user wording, log phrase, or title"
- entity: "tool id, function/class, bundle id, task id, turn id, subsystem"
- time: "timestamp or time range if known"
read_refs:
- "KDCube logical path only: ar:/tc:/fi:/ws:/su:/so:, or (none yet)"
done:
- ...
open:
- ...
next:
- ...
recovery_plan:
- first: "Use this visible reminder and retained suffix."
- if_needed: "Use react.memsearch with exact phrase/entity anchors."
- then_read: "Use react.read(read_refs). Skills read in full. ks: reads in full only when no knowledge_read_visible_* cap is configured; capped text uses stats_only and bounded line ranges. Exec stdout is not an uncapped read channel."

<rest of model-generated summary text>
[END COMPACTED PRIOR CONVERSATION MEMORY]
```

That checkpoint is the first map. `Active Work Reminder` is the retrieval-ready
handoff: it must include exact phrases, entities, timestamps, KDCube logical
refs, and a concrete recovery plan so the next model can recognize the active
task before it searches. Physical host paths are not readable recovery handles;
if they were mentioned by a user, they may be preserved as quoted context, but
not as `read_refs`. `active_request` is the immediate resumable item; `Goals`
in the rest of the checkpoint is the broader set of user/project objectives.
The rest of the checkpoint should include the important goals, outcomes,
decisions, artifacts, and logical refs that survived through the compaction
prompt.

If compaction cuts the currently running turn, the checkpoint is followed by a
timeline-shaped compacted prefix:

```text
[COMPACTED CURRENT TURN PREFIX]
TURN <turn_id> (started at ...)
[USER MESSAGE]
...
┌──────── COMPACTED ROUND 1 ────────┐
  [AI Agent thinking...]
  [AI Agent say]: ...
  [TOOL CALL ...].call ...
  [TOOL RESULT ...].result ...
  result: compacted large result; exact content is recoverable by logical_path
  recover_with: react.read(path); skills read in full; ks reads in full only when uncapped; capped text uses stats_only + line ranges
└────────────────────────┘
```

Treat that as earlier rounds from the same turn. Continue from it; do not
repeat completed rounds just because their full payloads are compacted.

## Recovery Chain

### 1. Exact path is visible

If the checkpoint or a working summary already contains the needed path, use it
directly.

```text
visible summary/checkpoint
  -> path is present
  -> react.read([path])
```

For oversized text payloads, `react.read` returns a bounded visible preview
instead of copying the full content into the visible timeline:

```text
react.read(["tc:<turn>.<call>.result"])
  -> status=truncated_for_visible_context
  -> preview is capped by ai.react.read_visible_* settings
  -> exact path remains recoverable
  -> use react.read again only for bounded previews; skills read in full; ks reads in full only when uncapped; capped text uses stats_only + range items
```

For bulk JSON/email/search results, use the preview and shape to plan. Exec may
process data and write a smaller derived artifact, but its stdout is capped and
must not be treated as a full-content recovery channel.

When the agent first needs only shape/size metadata, it can avoid visible
content entirely:

```text
react.read({"paths":["tc:<turn>.<call>.result"],"stats_only":true})
  -> status=stats_only
  -> bytes/tokens/text_symbols/mime in the status block
  -> no content block added to the visible timeline
```

Examples:

```text
react.read(["ws:turn_13083704.conv.working.summary"])
react.read(["tc:turn_13083704.tc_8fc21a80902b.result"])
react.read(["fi:turn_13083704.outputs/email-attachments/invoice.pdf"])
react.read(["fi:turn_13083704.snapshots/wizard-state.yaml"])
react.read(["ar:turn_13083704.assistant.completion"])
react.read(["so:sources_pool[1,3,5]"])
```

Snapshot artifacts use a dedicated `fi:<turn_id>.snapshots/<name>` namespace
for durable story/wizard state. The format is not enforced; snapshot files may
be YAML, JSON, Markdown, or another text-oriented representation chosen by the
bundle or event generator. This concept is opt-in per ReAct agent; bundle config
must enable `react.story_snapshots.enabled: true`, or bundle code must call
`build_react(..., story_snapshots_enabled=True)`, before the snapshot instruction
block is added to that agent's decision prompt. Cross-conversation recovery
continues to work for ordinary file/output refs even when this block is off.

A cross-conversation `fi:` artifact path carries the conversation scope as a
leading `conv_` segment. The ordinary same-conversation form stays short; the
scoped form appears only when some recovery/linking surface emits a path to
another conversation:

```json
{
  "items": [
    {
      "path": "fi:conv_9f3d27c2.turn_x.snapshots/wizard-state.yaml"
    }
  ]
}
```

The `turn_id` is still part of the logical path. If an `fi:` path starts
`fi:conv_<conversation_id>.turn_<id>...`, the `conv_` segment is the
conversation scope and the artifact belongs to that other conversation. Current
conversation `fi:` paths do not have this segment. Consumers must preserve the
segment and pass the scoped path directly to `react.read`, `react.pull`,
`react.checkout`, or `react.rg`.

For non-text binary files needed by code:

```text
visible summary/checkpoint
  -> fi: path is present
  -> react.pull([fi path])
  -> exec code reads the pulled local file
```

### 2. Exact path is not visible, but the model knows what to search for

Use `react.memsearch` when the model does not have a logical path but suspects
the information exists in prior turns. Choose the mode by the user's clue.

Broad conversation overview:

```text
user asks "what have we talked about so far?"
  -> react.memsearch({mode: "timeline", targets: ["summary"], order: "asc", top_k: <enough>})
  -> no query; generic query strings like "conversation topics discussed" do not help
  -> summarize the returned working summaries by turn order
```

Semantic clue:

```text
visible checkpoint gives semantic clue
  -> react.memsearch({query, targets=["summary", ...]})
  -> memsearch returns turn_id + snippet paths
  -> react.read(snippet path or ws:<turn_id>.conv.working.summary)
```

Ordinal or temporal clue:

```text
user asks "what was the second turn about?"
  -> react.memsearch({mode: "ordinal", ordinal: 2, targets: ["summary", "user", "assistant"]})
  -> memsearch returns turn_id, ordinal, started_at, turn_index_path, snippet paths

user asks "what did we discuss around March?"
  -> react.memsearch({mode: "temporal", from: "...", to: "...", targets: ["summary", "user", "assistant"]})
  -> read returned refs, or read ar:<turn_id>.react.turn.index for exact inventory
```

Topic plus temporal clue:

```text
user asks "2 months ago I think we discussed invoices"
  -> convert the relative date to an ISO range
  -> react.memsearch({query: "invoices", from: "<iso>", to: "<iso>", targets: ["summary", "user", "assistant"]})
  -> omit mode
  -> semantic search is narrowed to that time window
```

Typical semantic query:

```json
{
  "query": "Anthropic April 2026 invoices zip",
  "targets": ["summary"],
  "top_k": 5
}
```

Useful target expansion:

```json
{
  "query": "Anthropic April 2026 invoice PDFs",
  "targets": ["summary", "user", "assistant", "attachment"],
  "top_k": 5
}
```

Typical ordinal query:

```json
{
  "mode": "ordinal",
  "ordinal": 2,
  "targets": ["summary", "user", "assistant"]
}
```

Typical temporal catalog query:

```json
{
  "mode": "temporal",
  "from": "2026-03-01T00:00:00Z",
  "to": "2026-04-01T00:00:00Z",
  "targets": ["summary", "user", "assistant"],
  "order": "asc"
}
```

Memsearch result shape:

```json
{
  "turn_id": "turn_13083704",
  "turn_index_path": "ar:turn_13083704.react.turn.index",
  "working_summary_path": "ws:turn_13083704.conv.working.summary",
  "ordinal": 42,
  "total_turns": 91,
  "started_at": "2026-05-05T19:27:38Z",
  "snippets": [
    {
      "role": "summary",
      "path": "ws:turn_13083704.conv.working.summary",
      "ts": "2026-05-05T19:37:19Z"
    }
  ]
}
```

Behavior:

- `targets=["summary"]` searches prior turn context and returns
  `conv.working.summary` snippets from matching turn logs.
- Summary snippets carry paths like
  `ws:<turn_id>.conv.working.summary.attempt.N`.
- `react.read(["ws:<turn_id>.conv.working.summary"])` is a canonical alias for
  the latest working-summary attempt for that turn.
- `mode="ordinal"`, `mode="temporal"`, and `mode="timeline"` can run without
  `query` because they use the deterministic turn catalog.
- In catalog modes, a `query` value is ignored. Do not pass generic query text
  such as `"conversation topics discussed"`.
- `query` with `from`/`to` remains semantic search, but narrowed to the
  timestamp window.

### 3. Summary is found, then exact refs are read

The normal recovery path through a working summary is:

```text
react.memsearch(targets=["summary"])
  -> hit: turn_id=turn_13083704
  -> snippet path: ws:turn_13083704.conv.working.summary.attempt.1
  -> react.read(["ws:turn_13083704.conv.working.summary"])
  -> summary contains Refs
  -> react.read / react.pull exact refs
```

Example summary shape:

```text
[WORKING SUMMARY]
[path: ws:turn_13083704.conv.working.summary.attempt.1]
Goal: Create ZIP with all Anthropic April 2026 invoice PDFs.
Outcome: Failed to ZIP after materializing 20 PDFs; exec sandbox could not access hosted artifacts.
Key facts:
- Gmail scan found 10 Anthropic emails and 20 PDF attachments.
- Materialization succeeded with file_count=20 and errors=0.
- ZIP failed at hosted artifact vs exec filesystem boundary.
Refs:
- user: ar:turn_13083704.user.prompt
- email scan result: tc:turn_13083704.tc_8fc21a80902b.result
- materialized attachments: tc:turn_13083704.tc_29268b000988.result
- assistant final: ar:turn_13083704.assistant.completion
```

The model can then read the exact objects:

```text
react.read([
  "tc:turn_13083704.tc_29268b000988.result",
  "ar:turn_13083704.assistant.completion"
])
```

## Responsibility Split

### React model that handled the turn

The React decision model writes the `summary` channel on complete/exit rounds.
That text becomes the turn's durable working summary.

The model is responsible for the semantic content:

- goal
- outcome
- key facts
- refs to decisive user prompts, tool calls/results, produced artifacts, and
  assistant completion

This is why summary quality matters. The model knows why a tool result or file
was important, so it should name the refs that make future recovery cheap.

### React runtime

The runtime persists the model's `summary` channel as:

```text
ws:<turn_id>.conv.working.summary.attempt.<N>
```

The runtime also resolves the canonical alias:

```text
ws:<turn_id>.conv.working.summary
```

to the latest available summary attempt. The canonical path is a read alias, not
a second persisted block.

The runtime is responsible for:

- stable summary paths
- attempt metadata
- canonical latest-attempt alias resolution
- keeping working-summary blocks visible through TTL pruning
- exposing exact artifacts and blocks through `react.read`

### Compaction model

The compaction model produces `su:<turn_id>.conv.range.summary` for older
timeline ranges that are removed from the visible stream.

It should treat working summaries and tool/artifact digests as high-value input.
It should preserve enough semantic context and logical refs that the next model
can continue without reopening old turns by default.

### Turn log and artifact storage

The turn log and artifact storage are authoritative for exact content:

- original user messages
- assistant completions
- tool calls/results
- files
- sources
- attachments

Summaries point to this exact data, but summaries are not the exact data.

## The Current Gap

Working summaries are an effective semantic map, but they are not a complete
inventory. If a useful artifact or tool result was not mentioned in the summary,
the model currently has to recover it indirectly:

```text
compacted checkpoint
  -> memsearch by semantic query
  -> maybe find a nearby user/assistant/attachment/summary snippet
  -> maybe discover a path
```

That is not deterministic enough for artifact-centric workflows.

The problem case:

```text
User: "Use the spreadsheet you made three days ago"
Visible context after compaction:
  - compacted checkpoint mentions the broad work
  - working summary forgot to mention the spreadsheet path
Model needs:
  - the exact fi: path
```

Semantic search may find the turn, but without a deterministic turn inventory
the model may still not know what exact artifacts were produced.

## Target: Turn Index

Add a deterministic, system-generated turn index view that can be read by
logical path. This is not a new stored timeline block inside the turn. It should
be reconstructed on demand from the persisted turn log and artifact metadata
when the model calls `react.read`.

Proposed path:

```text
ar:<turn_id>.react.turn.index
```

Proposed recovery chain:

```text
compacted memory
  -> react.memsearch finds relevant turn/summary
  -> react.read(["ws:<turn_id>.conv.working.summary"])
  -> if refs are missing or incomplete:
       react.read(["ar:<turn_id>.react.turn.index"])
  -> read/pull exact ar:/tc:/fi:/so: refs from the index
```

The turn index should be compact enough for model use, but complete enough for
deterministic recovery.

Schematic index:

```text
[TURN INDEX]
[path: ar:turn_13083704.react.turn.index]
turn_id: turn_13083704
started_at: 2026-05-05T19:27:38Z
ended_at: 2026-05-05T19:37:19Z

summaries:
- latest working summary: ws:turn_13083704.conv.working.summary
  source: ws:turn_13083704.conv.working.summary.attempt.1
  label: Anthropic April invoice ZIP attempt

messages:
- user prompt: ar:turn_13083704.user.prompt
  hint: user asked to retry the Anthropic April invoice ZIP workflow
- assistant completion: ar:turn_13083704.assistant.completion
  hint: final answer reported materialization success but ZIP failure

events:
- none

tools:
- email scan: tc:turn_13083704.tc_8fc21a80902b.call / tc:turn_13083704.tc_8fc21a80902b.result
  tool: email.process_user_emails
  status: success
  hint: found 10 Anthropic April emails and current attachment IDs
- attachment materialization: tc:turn_13083704.tc_29268b000988.call / tc:turn_13083704.tc_29268b000988.result
  tool: email.materialize_email_attachments
  status: success
  hint: materialized 20 Anthropic PDF invoice attachments

artifacts:
- invoice PDF: fi:turn_13083704.outputs/email-attachments/Invoice_1.pdf
  mime: application/pdf
  source_tool: tc_29268b000988
  hint: Anthropic invoice PDF materialized from Gmail
- invoice PDF: fi:turn_13083704.outputs/email-attachments/Invoice_2.pdf
  mime: application/pdf
  source_tool: tc_29268b000988
  hint: Anthropic invoice PDF materialized from Gmail

sources:
- so:sources_pool[1-2]
  hint: source pool rows visible in the sources pool when restored/read
```

The index should not duplicate large content. It should carry enough semantic
metadata to choose the next `react.read` or `react.pull`. Bare path lists are
not sufficient because they do not tell the model why a row matters.

## What The Model Should Do

Preferred order:

1. Use visible working summaries and compacted checkpoints.
2. If an exact visible logical path exists, call `react.read` or `react.pull`.
3. If no path exists, call `react.memsearch` with the mode that matches the clue.
4. Read the matching working summary with
   `ws:<turn_id>.conv.working.summary`.
5. If the working summary lacks the needed refs, read the turn index:
   `ar:<turn_id>.react.turn.index`.
6. Read or pull the exact `ar:`, `tc:`, `fi:`, or `so:` refs from the index.

The model should not reopen large old data unless the summary/checkpoint/index
indicates it is relevant to the current task.

## Implementation Notes

The next implementation should make the turn index system-generated, not
model-generated.

The model-generated working summary remains the semantic map. The system
generated turn index becomes the deterministic inventory.

The turn index should be derived from persisted turn-log/timeline blocks:

- user prompt, followup, and steer paths when present
- reactive-event turns may have no ordinary `user.prompt`; in that case the
  index should still expose the triggering event blocks and produced artifacts
- assistant completion paths, including multiple completions when present
- working-summary attempt paths
- tool call and result paths, with tool id and status
- artifact paths, mime, source tool call, visibility/kind, and a short semantic
  hint when available
- sources pool selectors
- start/end timestamps

It should be readable via `react.read` and searchable/discoverable via
`react.memsearch` only through compact metadata, not by dumping full artifacts.
