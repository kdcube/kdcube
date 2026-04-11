---
id: ks:docs/sdk/agents/react/event-blocks-README.md
title: "Event Blocks"
summary: "Block schema and event types emitted in the React timeline."
tags: ["sdk", "agents", "react", "events", "blocks"]
keywords: ["block types", "mime", "path", "tool results", "timeline events"]
see_also:
  - ks:docs/sdk/agents/react/tool-call-blocks-README.md
  - ks:docs/sdk/agents/react/context-progression.md
  - ks:docs/sdk/agents/react/react-round-README.md
---
# React Event Blocks

React emits a block-based event log (chronological, oldest -> newest).
Blocks are dicts with:
- `type` (required)
- `author`
- `turn_id`
- `ts`
- `mime`
- `path`
- `text` (for text content)
- `base64` (for binary content)
- `meta` (optional)

## Core Block Types
- `turn.header` (historical)
- `user.prompt`
- `user.followup`
- `user.steer`
- `user.attachment.meta`
- `user.attachment`
- `assistant.completion` (historical or current)
- `agent.log.header`
- `react.plan`
- `react.tool.call`
- `react.tool.code` (exec code captured from decision stream)
- `react.tool.result`
- `react.thinking` (internal thinking captured from decision stream; hidden by default)
- `react.notes` (decision notes emitted before tool calls; user-visible)
- `react.note` (internal notes written via `react.write(channel="internal")`)
- `react.decision.raw` (raw ReactDecisionOutV2 JSON; only on schema error retry)
- `react.completion`
- `react.plan.ack`
- `react.workspace.publish`
- `conv.range.summary` (summary of earlier turns)
- `system.message` (system notices persisted in the timeline)
- `stage.gate`
- `stage.coordinator`
- `stage.feedback`
- `stage.clarification`
- `stage.clarification.resolved`

## Tool Call / Result Blocks
1) Tool Call
   - `type`: `react.tool.call`
   - `mime`: `application/json`
   - `path`: `tc:<turn_id>.<call_id>.call`
   - `text`: JSON payload `{tool_id, tool_call_id, params}`

2) Tool Result
   - `type`: `react.tool.result`
   - `mime`: based on output (`application/json`, `text/markdown`, `image/png`, `application/pdf`, ...)
   - `path`: `tc:<turn_id>.<call_id>.result`
   - `text` or `base64` content
   - Metadata JSON block text contains a **safe digest** (no hosted_uri/rn/key/physical_path):
     - `artifact_path`
     - `physical_path` (OUT_DIR‑relative)
     - `mime`
     - `kind` (`file` | `display`)
     - `visibility` (`external` | `internal`)
     - `tool_call_id` (tool_id is derived via the tool call map)
     - `sources_used` (list of SIDs, if any)
   - File content blocks (path `fi:...`) store hosting info in `meta`:
     `hosted_uri`, `rn`, `key`, `physical_path`, plus `meta.digest`.
   - Hosting metadata is **not** rendered in the timeline text.

3) Exec Code (react.tool.code)
   - `type`: `react.tool.code`
   - `mime`: `text/x-python` (or `text/plain`)
   - `path`: `fi:<turn_id>.code.<call_id>`
   - `text`: code content
   - Rendered as:
     `[AI Agent wrote code] fi:<turn_id>.code.<call_id>:\n<code>`
   - For exec tools, `react.tool.code` is emitted **before** the tool call block.

### Example: exec with missing output file
```json
{
  "type": "react.tool.result",
  "call_id": "abc123",
  "mime": "application/json",
  "path": "tc:turn_123.abc123.result",
  "text": "{\n  \"tool_id\": \"exec_tools.execute_code_python\",\n  \"tool_call_id\": \"abc123\",\n  \"error\": {\n    \"code\": \"missing_artifact\",\n    \"message\": \"Artifact 'report_md' not produced\",\n    \"details\": {\"missing_artifact\": \"report_md\"}\n  },\n  \"ts\": \"2026-02-09T03:15:49Z\"\n}"
}
```

Search/fetch tools emit only SIDs in the result block; full content lives in `sources_pool`.

## External user event blocks
These blocks are produced when the shared conversation event source is folded into the
active timeline:

1) Followup
   - `type`: `user.followup`
   - `author`: `user`
   - `mime`: `text/markdown`
   - `path`: `ar:<turn_id>.external.followup.<message_id>`
   - `text`: followup text, if provided
   - `meta`:
     - `event_kind = "followup"`
     - `message_id`
     - `stream_id`
     - `sequence`
     - `target_turn_id`
     - `active_turn_id_at_ingress`
     - `owner_turn_id`
     - `explicit`
     - `source`
     - optional `payload`

2) Steer
   - `type`: `user.steer`
   - same shape as followup, but:
   - `path`: `ar:<turn_id>.external.steer.<message_id>`
   - `meta.event_kind = "steer"`

Rendered form:
```text
[FOLLOWUP DURING TURN]
[path: ar:<turn_id>.external.followup.<message_id>]
...
```

or:

```text
[STEER DURING TURN]
[path: ar:<turn_id>.external.steer.<message_id>]
...
```

These are model-visible timeline blocks, not merely transport-level ingress messages.

Behavioral meaning:
- `user.followup` is additional user input that the current live turn actually consumed.
- `user.steer` is a control signal. In React, a consumed steer requests interruption of the active turn at the next safe checkpoint.
- If a live turn does not consume the event, the same durable event may later be promoted into a normal scheduled turn by proc.

### react.read status block
`react.read` always emits a **status JSON block first** at `tc:<turn_id>.<call_id>.result` with
`paths`, `missing`, and `exists_in_visible_context`. Result blocks follow after the status block.

### Decision notes (react.notes)
Decision `notes` are emitted as their own block **before** the tool call:
- `type`: `react.notes`
- `mime`: `text/markdown`
- `path`: `ar:<turn_id>.react.notes.<tool_call_id>`
- `text`: notes content
- `meta.channel="timeline_text"`

These notes are user-visible and rendered as:
`[AI Agent say]: <notes>`

### Assistant completion (assistant.completion)
Assistant completions can include `meta.sources_used` (SIDs). When rendered for the model,
this is injected as a separate line:
```
[sources_used: [1,2,3]]
```

### Internal Memory Beacons (react.note / react.note.preserved)
`react.write(channel="internal")` emits an Internal Memory Beacon:
- a normal meta block (visibility=internal)
- a `react.note` block with `meta.channel="internal"`
- optionally later, after compaction, a preserved clone `react.note.preserved`

These notes are visible to agents (not to end users). They should be short, telegraphic, and tagged
with `[P]` / `[D]` / `[S]` / `[A]` / `[K]` per the shared instruction.
They are best written when the agent has something stable and reusable to carry forward, usually near the end of the turn.

Example:
```text
[INTERNAL NOTE]
[K] fi:turn_123.files/src/app/auth/service.py - invite flow implementation; reopen here before changing user onboarding
```

### Thinking blocks (react.thinking)
Decision streaming captures the internal thinking section and stores it as a hidden block:
- `type`: `react.thinking`
- `mime`: `text/markdown`
- `path`: `ar:<turn_id>.react.thinking.<iteration>`
- `text`: thinking content
- `meta.channel="thinking"`
- `meta.title`: human‑readable label for UI (e.g., `solver.react.v2.decision (2)`)
- `meta.hidden=true` (kept out of model render)

These are **not** persisted as `conv.thinking.stream` artifacts anymore; UI artifacts are
reconstructed from the turn log during fetch.

### Decision raw (react.decision.raw)
On **schema error** retries, we store the raw JSON the model produced so it can see what went wrong:
- `type`: `react.decision.raw`
- `mime`: `application/json`
- `path`: `ar:<turn_id>.react.decision.raw.<iteration>`
- `text`: raw JSON from the ReactDecisionOutV2 channel
- Rendered as `[REACT DECISION RAW]` followed by the raw JSON.

Summary blocks include:
- `type`: `conv.range.summary`
- `meta.covered_turn_ids`: list of turn ids summarized

### Workspace publish event (react.workspace.publish)
Successful and failed git-workspace publication is captured as an internal event block:
- `type`: `react.workspace.publish`
- `author`: `react.workspace`
- `mime`: `application/json`
- `path`: `ar:<turn_id>.react.workspace.publish`
- `text`: JSON payload with high-signal publish status and metadata
- `meta.status`: `succeeded` or `failed`

This block is:
- persisted in the timeline
- excluded from normal model render
- intended for observability and runtime recovery, not for user-facing narration

Typical fields in `text`:
- `turn_id`
- `workspace_implementation`
- `status`
- on success:
  - `commit_sha`
  - `lineage_ref`
  - `version_ref`
- on failure:
  - `message`
  - `error`

ANNOUNCE may surface only a compact derivative of this state, for example:
- `current_turn_publish: pending|succeeded|failed`
- `last_published_turn: ...`
- `publish_error: ...` on failure

## System messages
`system.message` blocks are persisted system notices that should be rendered in the model context.

Cache TTL pruning emits:
- `type`: `system.message`
- `meta.kind`: `cache_ttl_pruned`
- `meta.ttl_seconds`: TTL that triggered pruning

Example:
```json
{
  "type": "system.message",
  "author": "system",
  "turn_id": "turn_123",
  "path": "ar:turn_123.system.message.cache_pruned",
  "text": "[SYSTEM MESSAGE] Context was pruned because the session TTL (1800s) was exceeded. ...",
  "meta": {"kind": "cache_ttl_pruned", "ttl_seconds": 1800}
}
```

## Hidden blocks (hide)
Blocks can be hidden with `react.hide(path, replacement_text)` (path is a logical path, not a query):
- Original blocks remain in the timeline with `meta.hidden=true`.
- One block in the group stores `meta.replacement_text`.
- Rendering replaces hidden blocks with a single text block:
  `"HIDDEN — <replacement_text>. Retrieve with react.read(<path>)"`
- Hidden state is persisted in the timeline.

## Paths (Stable)
- `ar:<turn_id>.user.prompt`
- `ar:<turn_id>.assistant.completion`
- `ar:<turn_id>.react.notes.<tool_call_id>`
- `ar:<turn_id>.react.decision.raw.<iteration>`
- `fi:<turn_id>.user.attachments/<name>`
- `fi:<turn_id>.files/<relative_path>`
- `fi:<turn_id>.code.<call_id>`
- `tc:<turn_id>.<call_id>.call` / `.result`
- `so:sources_pool[...]`
- `ks:<relpath>` (knowledge space; read‑only)

Physical (OUT_DIR‑relative):
- attachments: `turn_<id>/attachments/<name>`
- files: `turn_<id>/files/<relative_path>`

## Examples

### Rendered timeline (what agents see)
```
[TURN turn_1770603271112_2yz1lp] ts=2026-02-09T02:14:32.676425Z

[USER MESSAGE]
[path: ar:turn_1770603271112_2yz1lp.user.prompt]
could you find top 3 places to eat here in wuppertal

[USER ATTACHMENT] menu.pdf | application/pdf
summary: 2‑page menu, prices & address (Wuppertal)
[path: fi:turn_1770603271112_2yz1lp.user.attachments/menu.pdf]
[physical_path: turn_1770603271112_2yz1lp/attachments/menu.pdf]

<document media_type=application/pdf b64_len=183942>

[REACT.PLAN]
id=plan_abc
□ 1) Search top restaurants
□ 2) Cross‑check ratings
□ 3) Draft ranked list

[AI Agent say]: Searching for top-rated restaurants in Wuppertal

[react.tool.call] (JSON)
{ "tool_id": "web_tools.web_search", "tool_call_id": "tc_18f62649fb3b", "params": { ... } }

[react.tool.result] (JSON meta)
{ "artifact_path": "tc:turn_1770603271112_2yz1lp.tc_18f62649fb3b.result", "tool_call_id": "tc_18f62649fb3b" }

[react.tool.result] (JSON content)
[{ "sid": 1, "title": "...", "url": "https://..." }, { "sid": 2, "title": "...", "url": "https://..." }]

[ASSISTANT MESSAGE]
[sources_used: [1,2]]
Here are the top three places...

[react.tool.call] (JSON)
{ "tool_id": "react.patch", "tool_call_id": "tc_6a3f1e0d9b21", "params": { "path": "turn_1770603271112_2yz1lp/files/draft.md", "patch": "..." } }

[react.tool.result] (JSON meta)
{
  "artifact_path": "fi:turn_1770603271112_2yz1lp.files/draft.md",
  "physical_path": "turn_1770603271112_2yz1lp/files/draft.md",
  "tool_call_id": "tc_6a3f1e0d9b21",
  "edited": true
}

[react.note] (internal)
[INTERNAL NOTE]
[D] decided to use Wanderlog + TheFork as primary sources
```

### Rendered model view (summary/result/artifact)
The model does **not** see raw tool-result JSON directly. The rendered timeline groups results:
```
[TOOL CALL tc_...] .call <tool_id>
tc:<turn_id>.<tool_call_id>.call

[TOOL RESULT tc_...].summary <tool_id>     # artifact-producing tools
Status: success|error
Artifacts:
- logical_path: fi:...
  (metadata: kind/visibility/channel/tokens/sources_used)

[TOOL RESULT tc_...].result <tool_id>      # non-artifact tools
logical_path: so:... / tc:... / ar:... / sk:... / ks:...
<result payload>

[TOOL RESULT tc_...].artifact <tool_id>    # each artifact
logical_path: fi:... (physical_path only if hosted)
<content block immediately follows>
```

### Exec ordering (notes → code → tool call)
```
[AI Agent say]: Converting the data into an Excel report
[AI Agent wrote code] fi:turn_123.code.tc_abcd1234:
<python code...>
[TOOL CALL tc_abcd1234] exec_tools.execute_code_python
Params:
{ "contract": [ ... ], "prog_name": "weather_report" }
```

### artifact_path vs physical_path
```
artifact_path : fi:turn_1771234567890_abcd.files/reports/summary.md
physical_path : turn_1771234567890_abcd/files/reports/summary.md   # OUT_DIR‑relative
```

### react.write(kind=display)
Blocks emitted:
```
type: react.tool.call
path: tc:turn_...<id>.call
text: {"tool_id":"react.write",...}

type: react.tool.result   (meta JSON)
path: tc:turn_...<id>.result
text: {
  "artifact_path":"fi:turn_...files/report.md",
  "physical_path":"turn_.../files/report.md",
  "kind":"display",
  "visibility":"external",
  "rn":"ef:...:artifact:report.md",
  "hosted_uri":"s3://...",
  "key":"...",
  "mime":"text/markdown",
  "sources_used":[1,2]
}

type: react.tool.result   (content)
path: fi:turn_...files/report.md
mime: text/markdown
text: "...generated markdown..."
```
Rendered view: one `.summary` block listing the artifact (logical_path + sources_used),
followed by a `.artifact` header with the logical path and the content block.

### react.write(kind=file)
Same shape as above, but meta has:
```
"kind":"file","visibility":"external"
```
and the content block may be emitted as binary if mime is image/pdf.

### react.patch
Blocks emitted:
```
react.tool.call
react.tool.result (meta JSON for each changed file; visibility=internal)
react.tool.result (patch text)
react.tool.result (summary JSON)
```
If kind='file', the patched file is hosted; hosted fields appear in meta JSON.

### react.plan / react.plan.ack
Plan snapshot and acknowledgements:
```
type: react.plan
mime: application/json
path: ar:turn_...react.plan.<plan_id>
text: {
  "plan_id": "plan_alpha",
  "steps": ["...", "..."],
  "status": {"1":"done","2":"pending"},
  "created_ts": "...",
  "last_ts": "..."
}

type: react.plan.ack
mime: text/markdown
path: ar:turn_...react.plan.ack.<iteration>
text:
  ✓ 1. Locate sources
  … 2. Draft report — in progress
```

### write_* rendering tool (e.g., write_pdf / write_png)
Blocks emitted:
```
react.tool.call (params include path)
react.tool.result (meta JSON with artifact_path + physical_path + kind=file + rn/hosted_uri/key if hosted)
react.tool.result (binary block) with path=fi:turn_...files/<file>
```

### exec tool producing two files (xlsx + pdf)
Blocks emitted (per file), all tied by the same `call_id`:
```
react.tool.call (exec_tools.execute_code_python)  # call_id=<id>
react.tool.result (meta JSON for file #1: xlsx, call_id=<id>)
react.tool.result (meta JSON for file #2: pdf, call_id=<id>)
react.tool.result (binary block for file #2, call_id=<id>)  # pdf only
```
Note: only **pdf/image** outputs get binary blocks (base64). Other files (e.g., xlsx)
are represented by metadata only and must be read from `physical_path` on disk.

User attachments follow the same rule:
- `application/pdf` and `image/*` -> binary block
- other types (e.g., `.docx`, `.xlsx`, `.txt`) -> **meta only**, read via `physical_path`

## Caching Strategy
The decision prompt is built from blocks:
1) history blocks (built once)
2) current turn blocks (built once)
3) event blocks (appended per tool call)
4) sources pool block (uncached)
5) active state block (uncached)

Only the **last React-produced block** is cached.
