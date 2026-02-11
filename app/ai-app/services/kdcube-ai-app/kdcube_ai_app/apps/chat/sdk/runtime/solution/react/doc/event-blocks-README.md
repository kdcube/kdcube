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
- `user.attachment.meta`
- `user.attachment`
- `assistant.completion` (historical or current)
- `agent.log.header`
- `react.plan`
- `react.tool.call`
- `react.tool.result`
- `react.note` (internal notes written via `react.write(channel="internal")`)
- `react.completion`
- `react.plan.ack`
- `conv.range.summary` (summary of earlier turns)
- `stage.gate`
- `stage.coordinator`
- `stage.feedback`
- `stage.clarification`
- `stage.clarification.resolved`

## Tool Call / Result Blocks
1) Tool Call
   - `type`: `react.tool.call`
   - `mime`: `application/json`
   - `path`: `tc:<turn_id>.tool_calls.<call_id>.in.json`
   - `text`: JSON payload `{tool_id, tool_call_id, reasoning, params}`

2) Tool Result
   - `type`: `react.tool.result`
   - `mime`: based on output (`application/json`, `text/markdown`, `image/png`, `application/pdf`, ...)
   - `path`: `tc:<turn_id>.tool_calls.<call_id>.out.json`
   - `text` or `base64` content
   - Metadata is encoded inside the JSON `text` payload for meta blocks:
     - `artifact_path`
     - `physical_path` (OUT_DIR‑relative)
     - `mime`
     - `kind` (`file` | `display`)
     - `visibility` (`external` | `internal`)
     - `tool_id`, `tool_call_id`
     - `sources_used` (list of SIDs, if any)
    - `hosted_uri`, `rn`, `key`, `local_path` when available (external files after hosting)

### Example: exec with missing output file
```json
{
  "type": "react.tool.result",
  "call_id": "abc123",
  "mime": "application/json",
  "path": "tc:turn_123.tool_calls.abc123.out.json",
  "text": "{\n  \"tool_id\": \"exec_tools.execute_code_python\",\n  \"tool_call_id\": \"abc123\",\n  \"error\": {\n    \"code\": \"missing_artifact\",\n    \"message\": \"Artifact 'report_md' not produced\",\n    \"details\": {\"missing_artifact\": \"report_md\"}\n  },\n  \"ts\": \"2026-02-09T03:15:49Z\"\n}"
}
```

Search/fetch tools emit only SIDs in the result block; full content lives in `sources_pool`.

### Internal notes (react.note)
`react.write(channel="internal")` emits:
- a normal meta block (visibility=internal)
- a `react.note` block with `meta.channel="internal"`
These notes are visible to agents (not to end users). They should be short, telegraphic, and tagged
with `[P]` / `[D]` / `[S]` per the shared instruction.

Summary blocks include:
- `type`: `conv.range.summary`
- `meta.covered_turn_ids`: list of turn ids summarized

## Hidden blocks (memory_hide)
Blocks can be hidden with `react.memory_hide(path, replacement_text)`:
- Original blocks remain in the timeline with `meta.hidden=true`.
- One block in the group stores `meta.replacement_text`.
- Rendering replaces hidden blocks with a single text block:
  `"HIDDEN — <replacement_text>. Retrieve with react.read(<path>)"`
- Hidden state is persisted in the timeline.

## Paths (Stable)
- `ar:<turn_id>.user.prompt`
- `ar:<turn_id>.assistant.completion`
- `fi:<turn_id>.user.attachments/<name>`
- `fi:<turn_id>.files/<relative_path>`
- `tc:<turn_id>.tool_calls.<id>.in.json` / `.out.json`
- `so:sources_pool[...]`

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

[react.tool.call] (JSON)
{ "tool_id": "web_tools.web_search", "tool_call_id": "18f62649fb3b", "params": { ... } }

[react.tool.result] (JSON meta)
{ "artifact_path": "tc:turn_1770603271112_2yz1lp.tool_calls.18f62649fb3b.out.json", "tool_call_id": "18f62649fb3b" }

[react.tool.call] (JSON)
{ "tool_id": "react.patch", "tool_call_id": "6a3f1e0d9b21", "params": { "path": "turn_1770603271112_2yz1lp/files/draft.md", "patch": "..." } }

[react.tool.result] (JSON meta)
{
  "artifact_path": "fi:turn_1770603271112_2yz1lp.files/draft.md",
  "physical_path": "turn_1770603271112_2yz1lp/files/draft.md",
  "tool_call_id": "6a3f1e0d9b21",
  "edited": true
}

[react.note] (internal)
[INTERNAL NOTE]
[D] decided to use Wanderlog + TheFork as primary sources
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
path: tc:turn_...tool_calls.<id>.in.json
text: {"tool_id":"react.write",...}

type: react.tool.result   (meta JSON)
path: tc:turn_...tool_calls.<id>.out.json
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
  "plan_id": "plan:turn_...:abcd",
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
