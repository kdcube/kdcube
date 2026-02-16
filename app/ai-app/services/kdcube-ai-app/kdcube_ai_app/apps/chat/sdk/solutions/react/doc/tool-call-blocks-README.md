# Tool Call Blocks (react v2)

This document summarizes **what gets written to the timeline** when React executes tools.  
It focuses on:
- `react.write(...)`
- `rendering_tools.write_*` (PDF/PNG/DOCX/PPTX)
- `exec_tools.execute_code_*` (code execution with contract)

**External tool return shape (required):**  
All external tools **must** return an envelope that includes at least:
```json
{
  "ok": true|false,
  "error": null | {
    "code": "...",
    "message": "...",
    "where": "...",
    "managed": true|false
  }
}
```
If `ok=false`, the error is extracted from this envelope and becomes `tr["error"]`
for timeline blocks and downstream handling.

**Builtin tool envelope handling (execution layer):**
- For **builtin tools** (as defined by `tools_insights.is_builtin_tool`), the execution layer
  unwraps `{ok, error, ret}` before building timeline blocks.
- `output` is always set to **`ret`** (not the envelope).
- `ok=false` becomes an error on the tool result (even if `ret` is present).
- Execution-level errors (tool_call exceptions / subprocess errors) are combined with tool errors.
- `managed` is **not** shown in timeline error payloads.

## Common ordering (all tool calls)

When a decision uses `action=call_tool`, the timeline receives blocks in this order:

1. **Decision notes** (optional)
   - `type`: `react.notes`
   - `path`: `ar:<turn_id>.react.notes.<tool_call_id>`
   - `text`: the decision `notes`
   - `meta.channel="timeline_text"`
   - Emitted by the **decision node**, independent of which tool is called.
   - Rendered as: `[AI Agent say]: <notes>`

2. **Tool call**
   - `type`: `react.tool.call`
   - `path`: `tc:<turn_id>.tool_calls.<tool_call_id>.in.json`
   - `text`: JSON `{tool_id, tool_call_id, params, ts}`
   - Notes are **not** embedded in the call payload.

3. **Notices (optional)**
   - `type`: `react.notice`
   - `path`: `tc:<turn_id>.tool_calls.<tool_call_id>.notice.json`
   - Used for protocol violations, path rewrites, tool errors, etc.

4. **Tool results**
   - One or more `react.tool.result` blocks:
     - **Meta block** (JSON) with `artifact_path`, `physical_path`, `mime`, `kind`, `visibility`, `tool_id`, `tool_call_id`, etc.
     - **Content block** (text or base64) when applicable.

---

## react.hide (cache‑bounded)

`react.hide` replaces a block with a short placeholder. It is **restricted** by:
- `RuntimeCtx.cache.editable_tail_size_in_tokens`
- **Pre‑tail cache checkpoint** (from `cache_point_*` settings)

If the target path is **before** the pre‑tail cache point, the tool returns
`code=hide_before_cache` and does not hide anything.

---

## react.write(...)

`react.write` always produces a meta block plus content.  
If token accounting is available, it is stored in the **meta JSON** (`"tokens": <count>`).
For write tools we also validate the output file and record:
- `size_bytes` in meta
- `write_warning` if the file is unusually small
- `tool_result_error` + meta `error` if the file is missing/empty

**Timeline sequence**

1. `react.notes` (optional)
2. `react.tool.call`
   - `params.content` is **truncated** to the first 100 characters and then appended with:
     `"[truncated.. see fi:<turn_id>.files/<name>]"` to avoid duplication.
3. `react.notice` (optional)
   - `protocol_violation.path_rewritten`
   - `react.write.hosting_failed` (file missing / hosting failure)
4. `react.tool.result` (meta JSON)
5. Content block:
   - `type=react.tool.result` when channel is `timeline_text` or `canvas`
   - `type=react.note` when channel is `internal`

**Example (simplified)**
```json
// react.notes
{ "type": "react.notes", "path": "ar:turn_1.react.notes.abc", "text": "Drafting summary" }

// react.tool.call (content truncated)
{ "type": "react.tool.call", "path": "tc:turn_1.tool_calls.abc.in.json",
  "text": "{ \"tool_id\": \"react.write\", \"tool_call_id\": \"abc\", \"params\": {\"content\": \"# Report... [truncated.. see fi:turn_1.files/report.md]\", ...} }" }

// react.tool.result (meta, tokens included if available)
{ "type": "react.tool.result", "path": "tc:turn_1.tool_calls.abc.out.json",
  "text": "{ \"artifact_path\": \"fi:turn_1.files/report.md\", \"physical_path\": \"turn_1/files/report.md\", \"kind\": \"display\", \"visibility\": \"external\", \"tokens\": 1234, \"size_bytes\": 1842 }" }

// content
{ "type": "react.tool.result", "path": "fi:turn_1.files/report.md", "mime": "text/markdown", "text": "# Report..." }


```

---

## rendering_tools.write_* (pdf/png/docx/pptx)

These are handled by the **external tool path** and behave like file‑producing tools.
The tool return payload does **not** include a path; the artifact path is derived from the
tool call `params.path` (normalized to `turn_<id>/files/...`).
Write validation applies here too (`size_bytes`, `write_warning`, missing/empty ⇒ error).

**Timeline sequence**

1. `react.notes` (optional)
2. `react.tool.call`
3. `react.notice` (optional)
   - `protocol_violation.param_ref_not_visible`
   - `protocol_violation.path_rewritten`
   - `tool_call_error`
   - `tool_result_error`
4. `react.tool.result` (meta JSON)
5. Optional binary block (only for PDF/Image):
   - `type=react.tool.result`
   - `mime=application/pdf` or `image/*`
   - `base64=<...>`

**Example (PDF)**
```json
// meta
{ "type": "react.tool.result", "path": "tc:turn_1.tool_calls.def.out.json",
  "text": "{ \"artifact_path\": \"fi:turn_1.files/report.pdf\", \"physical_path\": \"turn_1/files/report.pdf\", \"mime\": \"application/pdf\", \"kind\": \"file\", \"visibility\": \"external\" }" }

// binary
{ "type": "react.tool.result", "path": "fi:turn_1.files/report.pdf", "mime": "application/pdf", "base64": "<...>" }
```

---

## exec_tools.execute_code_* (contracted outputs)

Exec tools produce:
- A **text report** at `tc:<turn_id>.tool_calls.<tool_call_id>.out.json` describing runtime error (if any),
  file‑level errors, and the list of produced files.
- Per‑file blocks **only for produced artifacts** (meta + optional binary/text).
- For **text files**, exec will embed up to **20 KB** of file content in the content block
  (larger files are truncated with a `...[truncated]` suffix).

### How the model *sees* file blocks (rendered)

The model does **not** see raw blocks; it sees the rendered message content:

**PDF (`rendering_tools.write_pdf`)**
```
<text: meta JSON including tool_call_id, size_bytes, etc.>
<document media_type=application/pdf b64_len=...>
```

**PPTX (`rendering_tools.write_pptx`)**
```
<text: meta JSON including tool_call_id, size_bytes, etc.>
```
(no binary/document block is emitted for PPTX)

**Text file (exec output)**
```
<text: meta JSON including tool_call_id, size_bytes, etc.>
<text: file contents up to 20KB, then "...[truncated]" if longer>
```

**Important:** Exec tools do **not** emit `tool_call_error` or `tool_result_error` notices.
All errors are consolidated into the text report.

### Case A: All contract files produced, but runtime reports error

Blocks:
1) `react.notes` (optional)  
2) `react.tool.call`  
3) **Exec report** (text) at `tc:...out.json`  
4) For each produced artifact:  
   - `react.tool.result` (meta JSON)  
   - optional binary block (pdf/image)  
   - optional text block (if tool output includes text)  

### Case B: Partial contract produced, no runtime error

Blocks:
1) `react.notes` (optional)  
2) `react.tool.call`  
3) **Exec report** (text) at `tc:...out.json`  
4) For each **produced** artifact: meta (+ binary if pdf/image)  

### Case C: Partial contract produced + runtime error

Blocks:
1) `react.notes` (optional)  
2) `react.tool.call`  
3) **Exec report** (text) at `tc:...out.json`  
4) For each produced artifact: meta (+ binary if pdf/image)  

**Exec report example (one success, one failure)**
```json
{ "type": "react.tool.result",
  "path": "tc:turn_1.tool_calls.xyz.out.json",
  "mime": "text/markdown",
  "text": "Runtime error: execution_failed — Missing output files: turn_1/files/report.xlsx\nFile errors:\n- turn_1/files/report.xlsx: file not produced\nSucceeded:\n- turn_1/files/summary.pdf"
}
```

---

## Web tools (search / fetch)

Web tools are **builtin** and use the `{ok, error, ret}` envelope.  
By the time timeline blocks are built:
- `output` == `ret`
- `ok=false` (if any) becomes `tr["error"]`

### web_tools.web_search

**Timeline sequence**
1) `react.notes` (optional)  
2) `react.tool.call`  
3) `react.tool.result` (meta JSON)  
4) `react.tool.result` (content JSON; list of results)  

**Example (simplified)**
```json
// tool call
{ "type": "react.tool.call", "path": "tc:turn_1.tool_calls.s1.in.json",
  "text": "{ \"tool_id\": \"web_tools.web_search\", \"tool_call_id\": \"s1\", \"params\": {\"queries\": [\"best restaurants wuppertal\"], \"n\": 5} }" }

// meta
{ "type": "react.tool.result", "path": "tc:turn_1.tool_calls.s1.out.json",
  "text": "{ \"artifact_path\": \"tc:turn_1.tool_calls.s1.out.json\", \"mime\": \"application/json\", \"kind\": \"file\", \"visibility\": \"internal\" }" }

// content (results list)
{ "type": "react.tool.result", "path": "tc:turn_1.tool_calls.s1.out.json",
  "mime": "application/json",
  "text": "[{\"sid\":1,\"title\":\"...\",\"url\":\"https://...\"},{\"sid\":2,\"title\":\"...\",\"url\":\"https://...\"}]"
}
```

### web_tools.web_fetch

**Timeline sequence**
1) `react.notes` (optional)  
2) `react.tool.call`  
3) `react.tool.result` (meta JSON)  
4) `react.tool.result` (content JSON; dict keyed by URL)  

**Example (simplified)**
```json
// tool call
{ "type": "react.tool.call", "path": "tc:turn_1.tool_calls.f1.in.json",
  "text": "{ \"tool_id\": \"web_tools.web_fetch\", \"tool_call_id\": \"f1\", \"params\": {\"urls\": [\"https://example.com\"]} }" }

// meta
{ "type": "react.tool.result", "path": "tc:turn_1.tool_calls.f1.out.json",
  "text": "{ \"artifact_path\": \"tc:turn_1.tool_calls.f1.out.json\", \"mime\": \"application/json\", \"kind\": \"file\", \"visibility\": \"internal\" }" }

// content (fetch payload)
{ "type": "react.tool.result", "path": "tc:turn_1.tool_calls.f1.out.json",
  "mime": "application/json",
  "text": "{\"https://example.com\": {\"content\": \"...\", \"title\": \"Example\"}}"
}
```

---

## Summary

For all tools, the timeline always includes:
- A tool call block (`react.tool.call`)
- Zero or more notices (`react.notice`)
- One or more tool results (`react.tool.result`)  

When decision `notes` are provided, they always appear **as a separate `react.notes` block before the call** (emitted by the decision node, independent of tool handler).
